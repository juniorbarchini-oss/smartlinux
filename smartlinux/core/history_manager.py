import json
import os
import re
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from .models import DiskInfo

HISTORY_DIR = os.path.expanduser("~/.local/share/smartlinux")
HISTORY_FILE = os.path.join(HISTORY_DIR, "history.jsonl")

# Matched by attribute NAME, not numeric ID: SmartParser reuses ATA attribute IDs
# (5, 196, 197) for unrelated NVMe metrics (e.g. id 5 = "Unsafe Shutdowns" on NVMe),
# so an ID-based lookup would silently mislabel NVMe values as ATA sector counts.
TRACKED_ATA_ATTR_NAMES = {
    "reallocated_sector_ct": "reallocated_sector_ct",
    "reallocated_event_count": "reallocated_event_count",
    "current_pending_sector": "pending_sector",
}
NVME_WEAR_ATTR_NAME = "percentage used (wear)"

# Optional fields (added later; older lines simply do not have them, readers use .get()):
#   grown_defects       SAS "Grown Defect List"
#   uncorrected_errors  SAS read + write + verify uncorrected errors, ATA Reported_Uncorrect
#   media_errors        vendor tool (perccli/storcli) "Media Error Count"
SAS_GROWN_DEFECTS = "grown defect list"
SAS_UNCORRECTED = ("read errors uncorrected", "write errors uncorrected", "verify errors uncorrected")
ATA_UNCORRECTED = "reported_uncorrect"


def _optional_fields(disk: DiskInfo, attr_raw_by_name: Dict[str, Any]) -> Dict[str, Any]:
    fields: Dict[str, Any] = {}
    grown = _to_number(attr_raw_by_name.get(SAS_GROWN_DEFECTS))
    if grown is not None:
        fields["grown_defects"] = grown
    sas = [_to_number(str(attr_raw_by_name[n]).replace(",", "")) for n in SAS_UNCORRECTED if n in attr_raw_by_name]
    sas = [v for v in sas if v is not None]
    if sas:
        fields["uncorrected_errors"] = sum(sas)
    else:
        ata = _to_number(attr_raw_by_name.get(ATA_UNCORRECTED))
        if ata is not None:
            fields["uncorrected_errors"] = ata
    if getattr(disk, "vendor_media_errors", None) is not None:
        fields["media_errors"] = disk.vendor_media_errors
    return fields


def _to_number(raw: Any) -> Optional[float]:
    """Extracts a leading number from a smartctl raw value (e.g. '32', '5%', '2 (Average 19)')."""
    if raw is None:
        return None
    match = re.match(r"-?\d+(\.\d+)?", str(raw).strip())
    if not match:
        return None
    value = float(match.group(0))
    return int(value) if value.is_integer() else value


class HistoryManager:
    """Appends and reads SMART snapshots as JSON Lines, keyed by disk serial number.

    JSONL instead of SQLite: Humberto wants a file he can open or grep directly,
    with no viewer dependency. Written only on a manual scan (on-demand, no polling).
    """

    @classmethod
    def _ensure_dir(cls):
        if not os.path.exists(HISTORY_DIR):
            os.makedirs(HISTORY_DIR, exist_ok=True)

    @classmethod
    def _disk_key(cls, disk: DiskInfo) -> str:
        if disk.serial and disk.serial != "N/A":
            return disk.serial
        return f"{disk.model}_{disk.device_path}"

    @classmethod
    def append_snapshot(cls, disk: DiskInfo):
        """Appends one snapshot line for this disk. Skips disks with no SMART data yet."""
        if not disk.attributes and disk.health_status.value == "unknown":
            return

        cls._ensure_dir()
        attr_raw_by_name = {a.name.strip().lower(): a.raw for a in disk.attributes}

        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "serial": cls._disk_key(disk),
            "model": disk.model,
            "device_path": disk.device_path,
            "health_status": disk.health_status.value,
            "temperature_c": disk.temperature_c,
            "power_on_hours": disk.power_on_hours,
        }
        for attr_name, field_name in TRACKED_ATA_ATTR_NAMES.items():
            snapshot[field_name] = _to_number(attr_raw_by_name.get(attr_name))
        snapshot["nvme_wear_pct"] = _to_number(attr_raw_by_name.get(NVME_WEAR_ATTR_NAME))
        snapshot.update(_optional_fields(disk, attr_raw_by_name))

        try:
            with open(HISTORY_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"Error appending SMART history: {e}")

    @classmethod
    def get_history(cls, disk: DiskInfo, limit: int = 200) -> List[Dict[str, Any]]:
        """Returns snapshots for this disk's serial, oldest first, most recent `limit` entries.

        Reads line-by-line and keeps only a bounded tail in memory, so the UI
        stays responsive even if history.jsonl grows large over months of scans.
        """
        key = cls._disk_key(disk)
        if not os.path.exists(HISTORY_FILE):
            return []

        matched: List[Dict[str, Any]] = []
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entry.get("serial") == key:
                        matched.append(entry)
                        if len(matched) > limit:
                            matched.pop(0)
        except Exception as e:
            print(f"Error reading SMART history: {e}")
            return []

        return matched
