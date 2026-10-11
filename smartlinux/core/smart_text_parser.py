"""Parser for the human-readable output of old smartctl builds (5.x and up).

smartctl < 5.43 (e.g. 5.38 on a WD My Cloud EX4) has no ``-j`` JSON output and no
``--scan``. This module turns the plain-text report of ``-a`` (or any mix of
``-H``, ``-i``, ``-l selftest``, ``-A``) into the same JSON-shaped dict that
``SmartParser.parse_smart_json`` consumes, so health rules, attribute status,
self-test summaries and telemetry stay in one place.

It also parses the BusyBox-friendly discovery sources (/proc/partitions,
/sys/block) used when ``smartctl --scan`` and ``lsblk`` are not available.
"""
import re
from typing import Any, Dict, List, Optional, Tuple

from .models import DiskInfo
from .smart_parser import SmartParser, format_bytes_commercial


# ---------------------------------------------------------------- detection helpers

def is_unrecognized_option(text: str) -> bool:
    """True when smartctl rejected a command-line switch (e.g. ``-j`` on 5.38)."""
    return bool(text) and "UNRECOGNIZED OPTION" in text.upper()


def looks_like_smartctl_text(text: str) -> bool:
    if not text:
        return False
    return bool(
        re.search(r"^\s*smartctl\s+(version\s+)?\d", text, re.I | re.M)
        or "=== START OF" in text
        or is_unrecognized_option(text)
    )


# ---------------------------------------------------------------- field helpers

def _leading_int(value: str) -> Optional[int]:
    """First integer of a raw value: '30 (Min/Max 20/45)' -> 30, '21883h+12m' -> 21883,
    '1,234' -> 1234."""
    m = re.match(r"\s*(\d[\d,]*)", value or "")
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _field(text: str, *labels: str) -> Optional[str]:
    """Value of the first 'Label:   value' line matching any label (case-insensitive)."""
    for label in labels:
        m = re.search(rf"^[ \t]*{re.escape(label)}[ \t]*:[ \t]*(.+?)[ \t]*$", text, re.I | re.M)
        if m:
            return m.group(1)
    return None


_ATTR_RE = re.compile(
    r"^\s*(\d{1,3})\s+(\S+)\s+(0x[0-9a-fA-F]+)\s+(\d+|---)\s+(\d+|---)\s+(\d+|---)\s+"
    r"(\S+)\s+(\S+)\s+(\S+)\s+(.*?)\s*$"
)
_SELFTEST_RE = re.compile(
    r"^#\s*(\d+)\s+(.+?)\s{2,}(.+?)\s+(\d+)%\s+(\d+)\s+(\S+)\s*$"
)


def _num(token: str) -> int:
    return int(token) if token.isdigit() else 0


def _parse_attributes(text: str) -> List[Dict[str, Any]]:
    table: List[Dict[str, Any]] = []
    in_table = False
    for line in text.splitlines():
        if re.match(r"\s*ID#\s+ATTRIBUTE_NAME", line):
            in_table = True
            continue
        if not in_table:
            continue
        if not line.strip():
            if table:
                break
            continue
        m = _ATTR_RE.match(line)
        if not m:
            continue
        raw_str = m.group(10)
        raw_val = _leading_int(raw_str)
        when_failed = m.group(9)
        table.append({
            "id": int(m.group(1)),
            "name": m.group(2),
            "flags": {"value": int(m.group(3), 16)},
            "value": _num(m.group(4)),
            "worst": _num(m.group(5)),
            "thresh": _num(m.group(6)),
            "when_failed": "" if when_failed == "-" else when_failed,
            "raw": {"value": raw_val if raw_val is not None else 0, "string": raw_str},
        })
    return table


def _parse_selftest_log(text: str) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    in_log = False
    for line in text.splitlines():
        if re.match(r"\s*SMART Self-test log", line, re.I):
            in_log = True
            continue
        if not in_log:
            continue
        if re.match(r"\s*(SMART Selective|SMART Error|=== )", line):
            break
        m = _SELFTEST_RE.match(line)
        if not m:
            continue
        entries.append({
            "num": int(m.group(1)),
            "type": {"string": m.group(2).strip()},
            "status": {"string": m.group(3).strip()},
            "lifetime_hours": int(m.group(5)),
        })
    return entries


def _self_test_in_progress(text: str) -> Optional[str]:
    m = re.search(
        r"Self-test routine in progress[.\s]*\n?\s*(\d+)% of test remaining", text, re.I
    )
    if m:
        return f"Self-test routine in progress... {m.group(1)}% of test remaining"
    return None


def _health_verdict(text: str) -> Optional[bool]:
    m = re.search(r"overall-health self-assessment test result:\s*(\w+)", text, re.I)
    if m:
        return m.group(1).upper() == "PASSED"
    m = re.search(r"SMART Health Status:\s*(\S+)", text, re.I)
    if m:
        return m.group(1).upper() == "OK"
    return None


def _capacity_bytes(text: str) -> int:
    value = _field(text, "User Capacity", "Total NVM Capacity", "Namespace 1 Size/Capacity")
    if not value:
        return 0
    m = re.match(r"\s*([\d,\.]+)\s*(bytes)?", value)
    if not m:
        return 0
    try:
        return int(m.group(1).replace(",", "").replace(".", ""))
    except ValueError:
        return 0


def _rotation_rate(text: str) -> int:
    value = _field(text, "Rotation Rate")
    if not value:
        return -1  # not reported (smartctl < 5.41): unknown, assume rotating HDD
    if "solid state" in value.lower():
        return 0
    m = re.match(r"\s*(\d+)", value)
    return int(m.group(1)) if m else -1


# ---------------------------------------------------------------- public API

def smart_text_to_dict(text: str) -> Dict[str, Any]:
    """Builds the smartctl-JSON-shaped dict equivalent of a plain-text report."""
    data: Dict[str, Any] = {"smartctl": {"output_format": "text"}}

    model = _field(text, "Device Model", "Model Number")
    if not model:
        vendor, product = _field(text, "Vendor"), _field(text, "Product")
        model = " ".join(p for p in (vendor, product) if p) or None
    if model:
        data["model_name"] = model
    family = _field(text, "Model Family")
    if family:
        data["model_family"] = family

    serial = _field(text, "Serial Number", "Serial number")
    if serial:
        data["serial_number"] = serial
    firmware = _field(text, "Firmware Version", "Revision")
    if firmware:
        data["firmware_version"] = firmware

    size = _capacity_bytes(text)
    if size:
        data["user_capacity"] = {"bytes": size}

    rot = _rotation_rate(text)
    data["rotation_rate"] = 0 if rot == 0 else (rot if rot > 0 else 5400)

    is_nvme = _field(text, "Model Number") is not None and "ID# ATTRIBUTE_NAME" not in text
    data["device"] = {"protocol": "NVMe" if is_nvme else "ATA"}

    verdict = _health_verdict(text)
    if verdict is not None:
        data["smart_status"] = {"passed": verdict}

    table = _parse_attributes(text)
    if table:
        data["ata_smart_attributes"] = {"table": table}
        by_id = {a["id"]: a for a in table}
        for attr_id in (9,):
            if attr_id in by_id:
                data["power_on_time"] = {"hours": by_id[attr_id]["raw"]["value"]}
        if 12 in by_id:
            data["power_cycle_count"] = by_id[12]["raw"]["value"]
        for attr_id in (194, 190):
            if attr_id in by_id:
                t = _leading_int(by_id[attr_id]["raw"]["string"])
                if t and 0 < t < 150:
                    data["temperature"] = {"current": t}
                    break
    elif is_nvme:
        _fill_nvme(text, data)
    else:
        _fill_scsi(text, data)

    log = _parse_selftest_log(text)
    if log:
        data["ata_smart_self_test_log"] = {"standard": {"table": log}}
    live = _self_test_in_progress(text)
    if live:
        data["ata_smart_data"] = {"self_test": {"status": {"string": live}}}
    return data


def _fill_nvme(text: str, data: Dict[str, Any]):
    def num(*labels: str) -> int:
        v = _field(text, *labels)
        return _leading_int(v) or 0 if v else 0

    log = {
        "critical_warning": int((_field(text, "Critical Warning") or "0").split()[0], 0),
        "temperature": num("Temperature"),
        "available_spare": num("Available Spare") or 100,
        "available_spare_threshold": num("Available Spare Threshold") or 10,
        "percentage_used": num("Percentage Used"),
        "media_errors": num("Media and Data Integrity Errors"),
        "power_cycles": num("Power Cycles"),
        "power_on_hours": num("Power On Hours"),
        "unsafe_shutdowns": num("Unsafe Shutdowns"),
        "data_units_read": num("Data Units Read"),
        "data_units_written": num("Data Units Written"),
    }
    data["nvme_smart_health_information_log"] = log


def _fill_scsi(text: str, data: Dict[str, Any]):
    temp = _field(text, "Current Drive Temperature")
    if temp:
        t = _leading_int(temp)
        if t and t > 0:
            data["temperature"] = {"current": t}
    hours = _field(text, "Accumulated power on time, hours:minutes")
    if hours:
        t = _leading_int(hours)
        if t is not None:
            data["power_on_time"] = {"hours": t}


def parse_smart_text(text: str, device_path: str = "") -> DiskInfo:
    """Parses a plain-text smartctl report into a DiskInfo."""
    data = smart_text_to_dict(text)
    disk = SmartParser.parse_smart_json(data, device_path)
    if disk.size_bytes > 0:
        disk.size_human = format_bytes_commercial(disk.size_bytes)
    return disk


def text_reading_quality(text: str) -> int:
    """Same scale as ``reading_quality`` for JSON: 2 = full attribute table / NVMe log,
    1 = identity or health verdict only, 0 = nothing useful."""
    if not text or is_unrecognized_option(text):
        return 0
    data = smart_text_to_dict(text)
    if "ata_smart_attributes" in data or "nvme_smart_health_information_log" in data:
        return 2
    if "smart_status" in data or "model_name" in data:
        return 1
    return 0


# ---------------------------------------------------------------- discovery (BusyBox)

_SKIP_PREFIXES = ("loop", "ram", "zram", "dm-", "md", "sr", "mtd", "mmcblk", "nbd", "fd", "cdrom")


def parse_proc_partitions(text: str) -> List[Tuple[str, int]]:
    """Whole-disk entries of /proc/partitions as (name, size_kib).

    Partitions (sda1, nvme0n1p2) and virtual devices (loop, md, mtdblock, dm-) are skipped.
    """
    disks: List[Tuple[str, int]] = []
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) != 4 or not parts[0].isdigit():
            continue
        name = parts[3]
        if name.startswith(_SKIP_PREFIXES) or name.startswith("mtdblock"):
            continue
        if not re.fullmatch(r"(sd[a-z]+|hd[a-z]+|vd[a-z]+|xvd[a-z]+|nvme\d+n\d+|disk\d+)", name):
            continue
        try:
            disks.append((name, int(parts[2])))
        except ValueError:
            continue
    return disks


def sanitize_sys_value(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()
