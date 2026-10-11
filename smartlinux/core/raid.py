"""Hardware RAID controllers (LSI/Broadcom MegaRAID, Dell PERC, ...): shared by the local
detector and the remote clients so both discover and read the same way.

- Physical disks behind a MegaRAID controller are only reachable as
  ``smartctl -d megaraid,N /dev/bus/H``: several scan entries share one path, the -d type
  tells them apart (see DiskInfo.uid).
- The block devices the OS sees (/dev/sda, model "PERC H730 Mini") are virtual drives
  (RAID volumes): they have no SMART of their own and are never read.

Read-only: nothing here talks to the controller beyond smartctl's own read commands.
"""
import re
from typing import Any, Callable, Dict, List, Optional

from .models import PASSTHROUGH_TYPE_RE as _PASSTHROUGH_RE, DiskInfo, HealthStatus

# Model / vendor strings of logical volumes exported by RAID controllers
_VIRTUAL_MODEL_TERMS = (
    "perc ", "megaraid", "logical volume", "virtual disk", "virtual drive",
    "smart array", "lsi mr", "avago mr", "intel raid",
)
_VIRTUAL_MODEL_RE = re.compile(r"^(perc\b|mr\d{4}|lsi\s|avago\s|.*\blogical volume\b)", re.I)

_SKIP_PATHS = ("/loop", "/ram", "/zram", "/dm-", "/sr")

VIRTUAL_SUMMARY = "Virtual drive (RAID volume)"
VIRTUAL_GROUP = "Virtual drives (RAID volumes)"

_FAMILY_LABELS = {
    "megaraid": "MegaRAID",
    "cciss": "HP Smart Array",
    "aacraid": "Adaptec",
    "areca": "Areca",
    "3ware": "3ware",
    "hpt": "HighPoint",
    "sssraid": "3SNIC",
}


def passthrough_family(dev_type: Optional[str]) -> Optional[str]:
    m = _PASSTHROUGH_RE.match(dev_type or "")
    return m.group(1).lower() if m else None


def is_passthrough_type(dev_type: Optional[str]) -> bool:
    """True for -d types that address a physical disk behind a RAID controller."""
    return passthrough_family(dev_type) is not None


def slot_from_type(dev_type: Optional[str]) -> Optional[int]:
    m = _PASSTHROUGH_RE.match(dev_type or "")
    return int(m.group(2)) if m else None


def read_types_for(dev_type: str) -> List[str]:
    """-d types to try for an explicit scan entry: the scanned one, then (MegaRAID with a
    SATA disk behind it) the SAT-over-MegaRAID variant."""
    types = [dev_type]
    family = passthrough_family(dev_type)
    if family == "megaraid" and not dev_type.lower().startswith("sat+"):
        types.append(f"sat+{dev_type}")
    return types


def is_virtual_raid_model(model: Optional[str], vendor: Optional[str] = None) -> bool:
    """Whether a block device's model/vendor is a RAID controller's logical volume."""
    text = f" {(vendor or '').strip()} {(model or '').strip()} ".lower()
    model_s = (model or "").strip()
    if not model_s:
        return False
    if _VIRTUAL_MODEL_RE.match(model_s):
        return True
    return any(term in text for term in _VIRTUAL_MODEL_TERMS)


def mark_virtual(disk: DiskInfo, controller: Optional[str] = None) -> DiskInfo:
    """Turns a listed block device into a virtual drive: never scanned, no invented health."""
    disk.is_virtual = True
    disk.controller = controller or disk.model
    disk.health_status = HealthStatus.UNKNOWN
    disk.health_summary = VIRTUAL_SUMMARY
    disk.protocol = "RAID volume"
    disk.rotation_rate = "Virtual"
    return disk


def default_controller_label(device_path: str, dev_type: str) -> str:
    """Controller name before system info is known, e.g. "MegaRAID controller (/dev/bus/0)"."""
    family = _FAMILY_LABELS.get(passthrough_family(dev_type) or "", "RAID")
    return f"{family} controller ({device_path})"


def physical_name(dev_type: str) -> str:
    slot = slot_from_type(dev_type)
    return f"slot {slot}" if slot is not None else dev_type


def scan_entries(scan_json: Optional[Dict[str, Any]], skip: Callable[[str], bool] = None) -> List[Dict[str, str]]:
    """Entries of ``smartctl --scan -j`` with their -d type kept, without duplicates."""
    entries: List[Dict[str, str]] = []
    seen = set()
    for dev in (scan_json or {}).get("devices", []) or []:
        name = dev.get("name", "") or ""
        if not name or any(x in name for x in _SKIP_PATHS):
            continue
        if skip and skip(name):
            continue
        dev_type = (dev.get("type") or "").strip()
        key = (name, dev_type if is_passthrough_type(dev_type) else "")
        if key in seen:
            continue
        seen.add(key)
        entries.append({
            "name": name,
            "type": dev_type,
            "protocol": (dev.get("protocol") or "").strip(),
            "info_name": dev.get("info_name", "") or name,
        })
    return entries


def disk_from_scan_entry(entry: Dict[str, str], make: Callable[..., DiskInfo]) -> DiskInfo:
    """DiskInfo for one scan entry. ``make(device_path, **kwargs)`` builds the base object
    (local or remote)."""
    name = entry["name"]
    dev_type = entry.get("type") or None
    protocol = (entry.get("protocol") or "SATA").upper()
    if is_passthrough_type(dev_type):
        disk = make(
            name,
            model="Physical Drive",
            protocol="SCSI/SAS" if protocol == "SCSI" else protocol,
            smart_dev_type=dev_type,
            slot=slot_from_type(dev_type),
            controller=default_controller_label(name, dev_type),
        )
        disk.name = physical_name(dev_type)
        return disk
    return make(
        name, model="Physical Drive", protocol=protocol,
        is_usb=("USB" in protocol or "usb" in name),
        smart_dev_type=dev_type,
    )


def sectors_to_bytes(value: str) -> int:
    try:
        return int(value.strip()) * 512
    except (ValueError, AttributeError):
        return 0


def harmonize_controller_labels(disks: List[DiskInfo]):
    """Without lspci the physical disks only know "MegaRAID controller (/dev/bus/0)" and the
    virtual drives only their model ("PERC H730 Mini"). With one controller of each, they
    are the same card: name both "PERC H730 Mini (MegaRAID)"."""
    physical = [d for d in disks if is_passthrough_type(d.smart_dev_type)]
    virtual = [d for d in disks if d.is_virtual]
    families = {passthrough_family(d.smart_dev_type) for d in physical}
    paths = {d.device_path for d in physical}
    models = {(d.model or "").strip() for d in virtual}
    if len(families) != 1 or len(paths) != 1 or len(models) != 1 or not next(iter(models)):
        return
    family = _FAMILY_LABELS.get(families.pop(), "RAID")
    model = models.pop()
    label = model if family.lower() in model.lower() else f"{model} ({family})"
    for d in physical + virtual:
        d.controller = label
