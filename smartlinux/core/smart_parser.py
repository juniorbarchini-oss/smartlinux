import re
from typing import Dict, Any, List, Optional, Tuple
from .models import DiskInfo, SmartAttribute, HealthStatus, PASSTHROUGH_TYPE_RE


def format_bytes_commercial(size_bytes: int) -> str:
    """Formats bytes into human readable commercial decimal units (GB/TB)."""
    if size_bytes <= 0:
        return "Unknown"
    
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    size = float(size_bytes)
    unit_idx = 0
    while size >= 1000.0 and unit_idx < len(units) - 1:
        size /= 1000.0
        unit_idx += 1
    
    if unit_idx >= 3:
        return f"{size:.1f} {units[unit_idx]}" if size < 100 else f"{int(round(size))} {units[unit_idx]}"
    return f"{size:.0f} {units[unit_idx]}"


# Device types tried in order by smartctl (-d). None = auto-detect. Order matters:
# native/ATA-passthrough types come BEFORE generic "scsi", because many USB bridges
# answer "scsi" with their own identity (e.g. "JMicron SATA") and no SMART data.
SMART_DEVICE_TYPES = [
    None, "sat", "sat,auto", "sat,12", "sat,16",
    "usbjmicron", "usbsunplus", "usbcypress", "usbprolific",
    "sntrealtek", "sntjmicron", "sntasmedia",
    "scsi",
]


def reading_quality(data: Dict[str, Any]) -> int:
    """Scores how much real SMART data a smartctl JSON reply carries.

    2 = full telemetry (ATA attribute table, NVMe health log, or SCSI/SAS counters)
    1 = only health verdict / identity (typical of a USB bridge answering for the disk)
    0 = nothing useful
    """
    if data.get("ata_smart_attributes", {}).get("table"):
        return 2
    if "nvme_smart_health_information_log" in data:
        return 2
    if "scsi_error_counter_log" in data or "scsi_grown_defect_list" in data:
        return 2
    if "smart_status" in data or "ata_smart_data" in data:
        return 1
    if any(k in data for k in ("model_name", "scsi_product", "user_capacity", "serial_number")):
        return 1
    return 0


# A handful of command timeouts is normal on USB-bridged drives (cable, bus power, adapter
# resets); only a sustained count is worth flagging.
COMMAND_TIMEOUT_ATTENTION_MIN = 10


def reading_rank(quality: int, size_bytes: int) -> tuple:
    """Orders candidate readings of one drive: a reading that identifies the drive
    (it reports a capacity) always beats one that does not, then richer telemetry wins.

    Some USB bridges answer probes meant for other bridge families with an empty
    but well-formed reply (e.g. an all-zero NVMe log scoring quality 2, size 0). Ranking
    by quality first let that empty reply outrank a real SCSI/SAT reading.
    """
    return (size_bytes > 0, quality)


# Vendors that pack several counters into one SMART raw value. smartctl prints the whole
# 48-bit number, which is meaningless as a count (and made the health check flag healthy
# drives). Each decoder returns (the count that matters for health, text to display).
def _seagate_errors_over_operations(unit: str):
    def decode(raw_val: int):
        errors = (raw_val >> 32) & 0xFFFF
        operations = raw_val & 0xFFFFFFFF
        return errors, f"{errors} errors / {operations:,} {unit}"
    return decode


def _seagate_command_timeouts(raw_val: int):
    count = raw_val & 0xFFFF
    return count, f"{count}"


VENDOR_RAW_DECODERS = {
    "seagate": {
        1: _seagate_errors_over_operations("reads"),    # Raw_Read_Error_Rate
        7: _seagate_errors_over_operations("seeks"),    # Seek_Error_Rate
        188: _seagate_command_timeouts,                 # Command_Timeout
    },
}


def detect_raw_vendor(data: Dict[str, Any]) -> Optional[str]:
    """Which packed-raw convention the drive follows (None = report smartctl's value as is)."""
    model = str(data.get("model_name", "") or "")
    family = str(data.get("model_family", "") or "")
    if "seagate" in family.lower() or "seagate" in model.lower() or re.match(r"^ST\d", model.strip()):
        return "seagate"
    return None


VENDOR_SPECIFIC_NOTE = (
    "The manufacturer does not document this attribute, so smartctl cannot name it. "
    "It cannot be judged on its own: only a change from its own previous value means something."
)


def is_unnamed_attribute(name) -> bool:
    """smartctl's placeholder for an ID that is not in its drive database."""
    return str(name or "").strip().lower() in ("", "unknown_attribute", "unknown attribute") or \
        bool(re.fullmatch(r"attr_\d+", str(name).strip().lower()))


class SmartParser:
    """Parses JSON output from smartctl (-j -a)."""

    CRITICAL_ATA_ATTRS = {
        5: "Reallocated Sectors (Bad Sectors)",
        187: "Reported Uncorrectable Errors",
        188: "Command Timeouts",
        196: "Reallocation Event Count",
        197: "Current Pending Sector Count",
        198: "Offline Uncorrectable Sector Count",
        199: "UDMA CRC Error Count (Potential cable/interface issue)"
    }

    @classmethod
    def parse_smart_json(cls, data: Dict[str, Any], device_path: str = "") -> DiskInfo:
        """Parses a smartctl JSON dictionary into a DiskInfo model."""
        disk = DiskInfo(
            device_path=device_path or data.get("device", {}).get("name", "/dev/unknown"),
            name=device_path.split("/")[-1] if device_path else data.get("device", {}).get("name", "").split("/")[-1]
        )
        disk.raw_json = data

        # Device Identification
        disk.model = (
            data.get("model_name") or 
            data.get("scsi_model_name") or 
            data.get("device", {}).get("model_name") or 
            (f"{data.get('scsi_vendor', '')} {data.get('scsi_product', '')}").strip() or 
            data.get("model_family") or 
            "Generic Drive"
        ).strip()
        disk.serial = (data.get("serial_number") or data.get("scsi_serial_number") or "N/A").strip()
        disk.firmware = (data.get("firmware_version") or data.get("scsi_revision") or "N/A").strip()

        # Capacity
        user_cap = data.get("user_capacity", {})
        if "bytes" in user_cap:
            disk.size_bytes = int(user_cap["bytes"])
            disk.size_human = format_bytes_commercial(disk.size_bytes)
        
        # Protocol & Rotation
        dev_protocol = data.get("device", {}).get("protocol", "").upper()
        dev_type = data.get("device", {}).get("type", "").upper()
        if "NVME" in dev_protocol or "NVME" in dev_type or "nvme" in disk.device_path:
            disk.protocol = "NVMe"
        elif "USB" in dev_protocol or "USB" in dev_type or "usb" in disk.device_path:
            disk.protocol = "USB"
            disk.is_usb = True
        elif "SATA" in dev_protocol or "ATA" in dev_protocol:
            disk.protocol = "SATA"
        elif "SCSI" in dev_protocol or "SAS" in dev_protocol:
            disk.protocol = "SCSI/SAS"
        else:
            disk.protocol = dev_protocol or "ATA/SATA"

        # SAS/SCSI identity (vendor, transport, form factor)
        transport = str(data.get("scsi_transport_protocol", {}).get("name", "") or "").strip()
        if transport:
            disk.transport = transport
            if transport.upper().startswith("SAS") and disk.protocol == "SCSI/SAS":
                disk.protocol = "SAS"
        vendor = str(data.get("scsi_vendor", "") or "").strip()
        if vendor:
            disk.vendor = vendor
        form_factor = data.get("form_factor", {})
        if isinstance(form_factor, dict) and form_factor.get("name"):
            disk.form_factor = str(form_factor["name"])
        if PASSTHROUGH_TYPE_RE.match(dev_type):
            disk.smart_dev_type = data.get("device", {}).get("type")

        rot_rate = data.get("rotation_rate", 0)
        if rot_rate == 0 or "NVME" in disk.protocol:
            disk.rotation_rate = "SSD (Solid State)"
        else:
            disk.rotation_rate = f"{rot_rate} RPM (HDD)"

        # Telemetry: Temperature, Hours, Cycles
        cls._extract_telemetry(data, disk)

        # Native self-test status (smartctl -t short/long), so the UI can show
        # what happened after a test started from the 'Self-Test' button.
        cls._extract_self_test_status(data, disk)

        # Parse SMART attributes and determine Health
        if "nvme_smart_health_information_log" in data:
            cls._parse_nvme_health(data, disk)
        elif "ata_smart_attributes" in data:
            cls._parse_ata_attributes(data, disk)
        elif "scsi_error_counter_log" in data or "scsi_grown_defect_list" in data:
            cls._parse_scsi_health(data, disk)
        else:
            cls._parse_generic_health(data, disk)

        return disk

    @classmethod
    def _extract_telemetry(cls, data: Dict[str, Any], disk: DiskInfo):
        temp_dict = data.get("temperature", {})
        if "current" in temp_dict and int(temp_dict["current"]) > 0:
            disk.temperature_c = int(temp_dict["current"])
        else:
            disk.temperature_c = None
        
        power_time = data.get("power_on_time", {})
        if "hours" in power_time:
            disk.power_on_hours = int(power_time["hours"])
        
        if "power_cycle_count" in data:
            disk.power_cycles = int(data["power_cycle_count"])
        elif "accumulated_start_stop_cycles" in data.get("scsi_start_stop_cycle_counter", {}):
            disk.power_cycles = int(data["scsi_start_stop_cycle_counter"]["accumulated_start_stop_cycles"])

    @classmethod
    def _format_hours_ago(cls, current_hours: Optional[int], entry_hours: Optional[int]) -> str:
        """Turns 'this test ran at lifetime hour N' into a human 'X days ago', so an old
        self-test log entry isn't mistaken for something that just happened."""
        if current_hours is None or entry_hours is None:
            return ""
        delta = current_hours - entry_hours
        if delta < 0:
            return ""
        if delta < 1:
            return " (just now)"
        if delta < 24:
            return f" ({delta} hour{'s' if delta != 1 else ''} ago)"
        days = delta // 24
        return f" ({days} day{'s' if days != 1 else ''} ago)"

    @classmethod
    def _extract_self_test_status(cls, data: Dict[str, Any], disk: DiskInfo):
        nvme_log = data.get("nvme_self_test_log")
        if nvme_log is not None:
            current_op = nvme_log.get("current_self_test_operation", {}).get("string")
            if current_op and "no self-test" not in current_op.lower():
                disk.self_test_status = current_op
                return
            table = nvme_log.get("table", [])
            if table:
                entry = table[0]
                type_str = entry.get("self_test_code", {}).get("string", "Self-Test")
                status_str = entry.get("self_test_result", {}).get("string", "Unknown")
                ago = cls._format_hours_ago(disk.power_on_hours, entry.get("power_on_hours"))
                disk.self_test_status = f"{type_str}: {status_str}{ago}"
            return

        scsi_last = data.get("scsi_self_test_0")
        if isinstance(scsi_last, dict):
            type_str = scsi_last.get("code", {}).get("string", "Self-Test")
            status_str = scsi_last.get("result", {}).get("string", "Unknown")
            ago = cls._format_hours_ago(disk.power_on_hours, scsi_last.get("power_on_time", {}).get("hours"))
            disk.self_test_status = f"{type_str}: {status_str}{ago}"
            return

        # ATA: 'ata_smart_data.self_test.status.string' is a permanent summary field,
        # present even when idle (e.g. plain "completed without error") - it's only
        # a *live* in-progress indicator when it actually mentions progress/percent.
        # Only trust it for that; otherwise prefer the self-test LOG entry below,
        # which carries the test type and lifetime_hours needed to say "X days ago".
        live_status = data.get("ata_smart_data", {}).get("self_test", {}).get("status", {}).get("string")
        if live_status and ("progress" in live_status.lower() or "%" in live_status):
            disk.self_test_status = live_status
            return

        table = data.get("ata_smart_self_test_log", {}).get("standard", {}).get("table", [])
        if table:
            entry = table[0]
            type_str = entry.get("type", {}).get("string", "Self-Test")
            status_str = entry.get("status", {}).get("string", "Unknown")
            ago = cls._format_hours_ago(disk.power_on_hours, entry.get("lifetime_hours"))
            disk.self_test_status = f"{type_str}: {status_str}{ago}"
        elif live_status:
            disk.self_test_status = live_status

    @classmethod
    def _parse_ata_attributes(cls, data: Dict[str, Any], disk: DiskInfo):
        smart_status = data.get("smart_status", {})
        passed = smart_status.get("passed", True)
        
        attr_table = data.get("ata_smart_attributes", {}).get("table", [])
        attributes: List[SmartAttribute] = []

        has_critical_failure = not passed
        has_warning = False
        warning_reasons = []
        decoders = VENDOR_RAW_DECODERS.get(detect_raw_vendor(data), {})

        for item in attr_table:
            attr_id = int(item.get("id", 0))
            name = item.get("name", f"Attr_{attr_id}")
            vendor_specific = is_unnamed_attribute(name)
            if vendor_specific:
                name = f"Vendor-specific (ID {attr_id})"
            val = item.get("value", 0)
            worst = item.get("worst", 0)
            thresh = item.get("thresh", 0)
            raw_dict = item.get("raw", {})
            raw_val = raw_dict.get("value", 0)
            raw_str = raw_dict.get("string", str(raw_val))
            when_failed = item.get("when_failed", "")

            decode = decoders.get(attr_id)
            if decode:
                raw_val, raw_str = decode(int(raw_val))

            status_type = HealthStatus.HEALTHY
            status_str = "OK"

            if (thresh > 0 and val <= thresh) or when_failed in ("FAILING_NOW", "In_the_past"):
                status_type = HealthStatus.FAILED
                status_str = "FAIL"
                has_critical_failure = True
            elif attr_id in (5, 196, 197, 198) and raw_val > 0:
                status_type = HealthStatus.WARNING
                status_str = "WARNING"
                has_warning = True
                warning_reasons.append(f"{name}: {raw_str}")
            elif (attr_id == 187 and raw_val > 0) or \
                    (attr_id == 188 and raw_val >= COMMAND_TIMEOUT_ATTENTION_MIN):
                status_type = HealthStatus.WARNING
                status_str = "ATTENTION"
                has_warning = True

            desc = cls.CRITICAL_ATA_ATTRS.get(attr_id, "")
            if vendor_specific:
                desc = VENDOR_SPECIFIC_NOTE

            if attr_id in (194, 190) and disk.temperature_c is None:
                try:
                    disk.temperature_c = int(str(raw_str).split()[0])
                except (ValueError, IndexError):
                    pass

            attributes.append(SmartAttribute(
                id=attr_id,
                name=name,
                current=str(val),
                worst=str(worst),
                threshold=str(thresh),
                raw=raw_str,
                status=status_str,
                status_type=status_type,
                description=desc
            ))

        disk.attributes = attributes

        if has_critical_failure:
            disk.health_status = HealthStatus.FAILED
            disk.health_summary = "Imminent Failure / Threshold Breached"
        elif has_warning:
            disk.health_status = HealthStatus.WARNING
            disk.health_summary = f"Warning: {', '.join(warning_reasons[:2])}"
        elif passed:
            disk.health_status = HealthStatus.HEALTHY
            disk.health_summary = "Healthy (All parameters within normal thresholds)"
        else:
            disk.health_status = HealthStatus.UNKNOWN
            disk.health_summary = "Status undetermined"

    @classmethod
    def _parse_nvme_health(cls, data: Dict[str, Any], disk: DiskInfo):
        nvme_log = data.get("nvme_smart_health_information_log", {})
        smart_status = data.get("smart_status", {})
        passed = smart_status.get("passed", True)

        crit_warning = nvme_log.get("critical_warning", 0)
        temp = nvme_log.get("temperature", 0)
        avail_spare = nvme_log.get("available_spare", 100)
        avail_spare_thresh = nvme_log.get("available_spare_threshold", 10)
        pct_used = nvme_log.get("percentage_used", 0)
        media_errors = nvme_log.get("media_errors", 0)
        power_cycles = nvme_log.get("power_cycles", 0)
        power_on_hours = nvme_log.get("power_on_hours", 0)
        unsafe_shutdowns = nvme_log.get("unsafe_shutdowns", 0)
        data_units_read = nvme_log.get("data_units_read", 0)
        data_units_written = nvme_log.get("data_units_written", 0)

        if temp > 0:
            disk.temperature_c = temp
        if power_on_hours > 0:
            disk.power_on_hours = power_on_hours
        if power_cycles > 0:
            disk.power_cycles = power_cycles

        attributes: List[SmartAttribute] = [
            SmartAttribute(
                id=1,
                name="Critical Warning",
                current=str(crit_warning),
                worst="0",
                threshold="0",
                raw=f"0x{crit_warning:02X}",
                status="OK" if crit_warning == 0 else "FAIL",
                status_type=HealthStatus.HEALTHY if crit_warning == 0 else HealthStatus.FAILED,
                description="Controller critical warnings bitmask"
            ),
            SmartAttribute(
                id=2,
                name="Available Spare",
                current=f"{avail_spare}%",
                worst=f"{avail_spare_thresh}%",
                threshold=f"{avail_spare_thresh}%",
                raw=f"{avail_spare}%",
                status="OK" if avail_spare > avail_spare_thresh else "WARNING",
                status_type=HealthStatus.HEALTHY if avail_spare > avail_spare_thresh else HealthStatus.WARNING,
                description="Remaining spare block capacity percentage"
            ),
            SmartAttribute(
                id=3,
                name="Percentage Used (Wear)",
                current=f"{pct_used}%",
                worst="100%",
                threshold="100%",
                raw=f"{pct_used}%",
                status="OK" if pct_used < 90 else ("WARNING" if pct_used < 100 else "FAIL"),
                status_type=HealthStatus.HEALTHY if pct_used < 90 else (HealthStatus.WARNING if pct_used < 100 else HealthStatus.FAILED),
                description="Estimated percentage of device life consumed"
            ),
            SmartAttribute(
                id=4,
                name="Media and Data Integrity Errors",
                current=str(media_errors),
                worst="0",
                threshold="0",
                raw=str(media_errors),
                status="OK" if media_errors == 0 else "FAIL",
                status_type=HealthStatus.HEALTHY if media_errors == 0 else HealthStatus.FAILED,
                description="Unrecoverable data integrity and media errors"
            ),
            SmartAttribute(
                id=5,
                name="Unsafe Shutdowns",
                current=str(unsafe_shutdowns),
                worst="0",
                threshold="0",
                raw=str(unsafe_shutdowns),
                status="OK",
                status_type=HealthStatus.HEALTHY,
                description="Number of unsafe power cuts / abrupt shutdowns"
            ),
            SmartAttribute(
                id=6,
                name="Data Units Read",
                current="N/A",
                worst="N/A",
                threshold="0",
                raw=f"{data_units_read * 512 / (1000**3):.2f} TB" if data_units_read else "0 TB",
                status="OK",
                status_type=HealthStatus.HEALTHY,
                description="Total data read from device"
            ),
            SmartAttribute(
                id=7,
                name="Data Units Written (TBW)",
                current="N/A",
                worst="N/A",
                threshold="0",
                raw=f"{data_units_written * 512 / (1000**3):.2f} TB" if data_units_written else "0 TB",
                status="OK",
                status_type=HealthStatus.HEALTHY,
                description="Total terabytes written to device"
            ),
        ]

        disk.attributes = attributes

        if not passed or crit_warning > 0 or media_errors > 0:
            disk.health_status = HealthStatus.FAILED
            disk.health_summary = "Critical Failure / Integrity Errors"
        elif avail_spare <= avail_spare_thresh or pct_used >= 95:
            disk.health_status = HealthStatus.WARNING
            disk.health_summary = f"Warning: High wear ({pct_used}%) or Low Spare ({avail_spare}%)"
        else:
            disk.health_status = HealthStatus.HEALTHY
            disk.health_summary = "Healthy (NVMe Health Passed)"

    @staticmethod
    def _int(value) -> Optional[int]:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _parse_scsi_health(cls, data: Dict[str, Any], disk: DiskInfo):
        """SAS/SCSI disks (e.g. behind a MegaRAID/PERC controller): no ATA attribute table,
        but a health status, a grown defect list and an error counter log.

        Health rules: SMART Health Status not OK -> FAILED; grown defects or uncorrected
        errors -> WARNING; corrected errors and non-medium errors alone are normal on SAS
        (ECC at work / bus noise) and never raise a warning."""
        attrs: List[SmartAttribute] = []
        warnings: List[str] = []
        ok, warn, fail = HealthStatus.HEALTHY, HealthStatus.WARNING, HealthStatus.FAILED

        def row(name, value, status_type=ok, status="OK", threshold="-", description=""):
            attrs.append(SmartAttribute(
                id=len(attrs) + 1, name=name, current=str(value), worst="-",
                threshold=str(threshold), raw=str(value), status=status,
                status_type=status_type, description=description,
            ))

        smart_status = data.get("smart_status", {})
        passed = smart_status.get("passed")
        ie_string = str(smart_status.get("scsi", {}).get("ie_string", "") or "").strip()
        if passed is not None:
            row("SMART Health Status", "OK" if passed else (ie_string or "FAILED"),
                ok if passed else fail, "OK" if passed else "FAIL",
                description="The drive's own overall health assessment (informational exceptions)")

        temp = data.get("temperature", {})
        if disk.temperature_c is not None:
            trip = temp.get("drive_trip")
            hot = trip is not None and disk.temperature_c >= int(trip)
            row("Current Temperature", f"{disk.temperature_c} °C", warn if hot else ok,
                "WARNING" if hot else "OK", f"{trip} °C" if trip is not None else "-",
                "Drive temperature; threshold is the drive's trip point")
            if hot:
                warnings.append(f"temperature {disk.temperature_c} °C")

        power = data.get("power_on_time", {})
        if "hours" in power:
            minutes = power.get("minutes")
            value = f"{int(power['hours']):,} h" + (f" {int(minutes)} min" if minutes is not None else "")
            row("Power-On Time", value, description="Accumulated power-on time")

        cycles = data.get("scsi_start_stop_cycle_counter", {})
        if "accumulated_start_stop_cycles" in cycles:
            spec = cycles.get("specified_cycle_count_over_device_lifetime")
            row("Start/Stop Cycles", f"{int(cycles['accumulated_start_stop_cycles']):,}",
                threshold=f"{int(spec):,}" if spec else "-",
                description="Accumulated start/stop cycles (threshold: specified over device lifetime)")
        if "accumulated_load_unload_cycles" in cycles:
            spec = cycles.get("specified_load_unload_count_over_device_lifetime")
            row("Load/Unload Cycles", f"{int(cycles['accumulated_load_unload_cycles']):,}",
                threshold=f"{int(spec):,}" if spec else "-",
                description="Accumulated head load/unload cycles (threshold: specified over device lifetime)")
        if cycles.get("year_of_manufacture"):
            week = cycles.get("week_of_manufacture")
            row("Manufactured", f"{cycles['year_of_manufacture']}" + (f", week {week}" if week else ""),
                description="Date of manufacture reported by the drive")

        grown = cls._int(data.get("scsi_grown_defect_list"))
        if grown is not None:
            row("Grown Defect List", grown, warn if grown > 0 else ok,
                "WARNING" if grown > 0 else "OK", "0",
                "Sectors the drive has remapped since leaving the factory (SAS equivalent "
                "of reallocated sectors). Any growth deserves attention.")
            if grown > 0:
                warnings.append(f"{grown} grown defect{'s' if grown != 1 else ''}")

        counters = data.get("scsi_error_counter_log", {})
        for op in ("read", "write", "verify"):
            entry = counters.get(op)
            if not isinstance(entry, dict):
                continue
            label = op.capitalize()
            processed = entry.get("gigabytes_processed")
            done = f" ({processed} GB processed)" if processed not in (None, "") else ""
            corrected = cls._int(entry.get("total_errors_corrected"))
            if corrected is not None:
                row(f"{label} Errors Corrected", f"{corrected:,}",
                    description=f"Errors fixed by the drive's ECC / retries; normal on SAS drives{done}")
            uncorrected = cls._int(entry.get("total_uncorrected_errors"))
            if uncorrected is not None:
                row(f"{label} Errors Uncorrected", f"{uncorrected:,}",
                    warn if uncorrected > 0 else ok, "WARNING" if uncorrected > 0 else "OK", "0",
                    f"{label} errors the drive could not recover{done}")
                if uncorrected > 0:
                    warnings.append(f"{uncorrected:,} uncorrected {op} error{'s' if uncorrected != 1 else ''}")

        nonmedium = data.get("scsi_nonmedium_error", {})
        nm_count = cls._int(nonmedium.get("count")) if isinstance(nonmedium, dict) else cls._int(nonmedium)
        if nm_count is None:
            nm_count = cls._int(data.get("scsi_non_medium_error_count"))
        if nm_count is not None:
            row("Non-Medium Error Count", f"{nm_count:,}",
                description="Errors not related to the media (bus/transport, resets). "
                            "Informational on SAS: not a drive problem by itself.")

        last_test = data.get("scsi_self_test_0")
        if isinstance(last_test, dict):
            result = last_test.get("result", {})
            code = cls._int(result.get("value"))
            text = str(result.get("string", "Unknown"))
            # SCSI self-test results 3..7 = the test failed (a segment, read, ...)
            failed = code is not None and 3 <= code <= 7
            row("Last Self-Test", f"{last_test.get('code', {}).get('string', 'Self-test')}: {text}",
                warn if failed else ok, "WARNING" if failed else "OK",
                description="Most recent entry of the drive's self-test log")
            if failed:
                warnings.append("last self-test failed")

        disk.attributes = attrs

        if passed is False:
            disk.health_status = HealthStatus.FAILED
            disk.health_summary = f"SMART Health Status: {ie_string or 'FAILED'}"
        elif warnings:
            disk.health_status = HealthStatus.WARNING
            disk.health_summary = f"Warning: {', '.join(warnings[:3])}"
        elif passed is True:
            disk.health_status = HealthStatus.HEALTHY
            disk.health_summary = "Healthy (SMART Health Status OK, no grown defects)"
        else:
            disk.health_status = HealthStatus.UNKNOWN
            disk.health_summary = "Status undetermined"

    @classmethod
    def _parse_generic_health(cls, data: Dict[str, Any], disk: DiskInfo):
        smart_status = data.get("smart_status", {})
        passed = smart_status.get("passed")
        if passed is True:
            disk.health_status = HealthStatus.HEALTHY
            disk.health_summary = "Healthy (SMART Passed)"
        elif passed is False:
            disk.health_status = HealthStatus.FAILED
            disk.health_summary = "Critical Failure Detected"
        else:
            disk.health_status = HealthStatus.UNKNOWN
            disk.health_summary = "SMART telemetry not available"

        if not disk.attributes and passed is not None:
            disk.attributes = [
                SmartAttribute(
                    id=1,
                    name="SMART Overall Health Status",
                    current="PASSED" if passed else "FAILED",
                    worst="PASSED",
                    threshold="PASSED",
                    raw="0" if passed else "1",
                    status="OK" if passed else "FAIL",
                    status_type=HealthStatus.HEALTHY if passed else HealthStatus.FAILED,
                    description="Device/Bridge SMART overall self-test health assessment"
                )
            ]
