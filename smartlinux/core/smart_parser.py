from typing import Dict, Any, List, Optional, Tuple
from .models import DiskInfo, SmartAttribute, HealthStatus


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
            data.get("device", {}).get("model_name") or 
            data.get("model_family") or 
            "Generic Drive"
        ).strip()
        disk.serial = data.get("serial_number", "N/A").strip()
        disk.firmware = data.get("firmware_version", "N/A").strip()

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

        rot_rate = data.get("rotation_rate", 0)
        if rot_rate == 0 or "NVME" in disk.protocol:
            disk.rotation_rate = "SSD (Solid State)"
        else:
            disk.rotation_rate = f"{rot_rate} RPM (HDD)"

        # Telemetry: Temperature, Hours, Cycles
        cls._extract_telemetry(data, disk)

        # Parse SMART attributes and determine Health
        if "nvme_smart_health_information_log" in data:
            cls._parse_nvme_health(data, disk)
        elif "ata_smart_attributes" in data:
            cls._parse_ata_attributes(data, disk)
        else:
            cls._parse_generic_health(data, disk)

        return disk

    @classmethod
    def _extract_telemetry(cls, data: Dict[str, Any], disk: DiskInfo):
        temp_dict = data.get("temperature", {})
        if "current" in temp_dict:
            disk.temperature_c = int(temp_dict["current"])
        
        power_time = data.get("power_on_time", {})
        if "hours" in power_time:
            disk.power_on_hours = int(power_time["hours"])
        
        if "power_cycle_count" in data:
            disk.power_cycles = int(data["power_cycle_count"])

    @classmethod
    def _parse_ata_attributes(cls, data: Dict[str, Any], disk: DiskInfo):
        smart_status = data.get("smart_status", {})
        passed = smart_status.get("passed", True)
        
        attr_table = data.get("ata_smart_attributes", {}).get("table", [])
        attributes: List[SmartAttribute] = []

        has_critical_failure = not passed
        has_warning = False
        warning_reasons = []

        for item in attr_table:
            attr_id = int(item.get("id", 0))
            name = item.get("name", f"Attr_{attr_id}")
            val = item.get("value", 0)
            worst = item.get("worst", 0)
            thresh = item.get("thresh", 0)
            raw_dict = item.get("raw", {})
            raw_val = raw_dict.get("value", 0)
            raw_str = raw_dict.get("string", str(raw_val))
            when_failed = item.get("when_failed", "")

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
            elif attr_id in (187, 188) and raw_val > 0:
                status_type = HealthStatus.WARNING
                status_str = "ATTENTION"
                has_warning = True

            desc = cls.CRITICAL_ATA_ATTRS.get(attr_id, "")

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
