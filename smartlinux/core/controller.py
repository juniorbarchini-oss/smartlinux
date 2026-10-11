"""RAID controller as a unit: which disks belong to it and its aggregate health.

Pure helpers shared by the sidebar (status dot on the controller node), the controller view and
the Server panel. No I/O.
"""
from dataclasses import dataclass, field
from typing import Dict, List

from . import raid
from .models import DiskInfo, HealthStatus

NOT_SCANNED_TEXT = "Not scanned yet. Click Scan controller to read all disks and the RAID state at once."
DISCOVERY_TEXT = ("This server has a RAID controller. Open it and click Scan controller to read every "
                  "disk and the RAID state at once.")
SINGLE_DRIVE_NOTE = "single-drive volume"

# Worst first wins. Unscanned (UNKNOWN, gray) ranks above healthy: a controller is only green
# once every disk was read and nothing worse was found.
_RANK = {HealthStatus.HEALTHY: 0, HealthStatus.UNKNOWN: 1, HealthStatus.WARNING: 2, HealthStatus.FAILED: 3}


def is_controller_disk(disk: DiskInfo) -> bool:
    """Disks that live under a RAID controller node: its physical disks and its virtual drives."""
    return bool(disk.controller) and (disk.is_virtual or raid.is_passthrough_type(disk.smart_dev_type))


@dataclass
class ControllerGroup:
    label: str
    physical: List[DiskInfo] = field(default_factory=list)
    virtual: List[DiskInfo] = field(default_factory=list)


def group_by_controller(disks: List[DiskInfo]) -> Dict[str, ControllerGroup]:
    groups: Dict[str, ControllerGroup] = {}
    for d in disks:
        if not is_controller_disk(d):
            continue
        group = groups.setdefault(d.controller, ControllerGroup(d.controller))
        (group.virtual if d.is_virtual else group.physical).append(d)
    for group in groups.values():
        group.physical.sort(key=lambda d: (d.slot is None, d.slot or 0))
    return groups


def is_scanned(disk: DiskInfo) -> bool:
    return disk.last_scanned is not None or bool(disk.attributes)


def disk_health(disk: DiskInfo) -> HealthStatus:
    if not is_scanned(disk):
        return HealthStatus.UNKNOWN
    return disk.health_status


def worst(statuses) -> HealthStatus:
    statuses = [s for s in statuses if s is not None]
    if not statuses:
        return HealthStatus.UNKNOWN
    return max(statuses, key=lambda s: _RANK.get(s, 1))


def controller_health(physical: List[DiskInfo], virtual: List[DiskInfo], report=None) -> HealthStatus:
    """The worst of its disks (SMART + what the vendor tool says about each one), its volumes and
    the controller itself (battery, status). Gray while something is still unscanned."""
    statuses = [disk_health(d) for d in physical]
    statuses += [d.raid_health for d in physical if d.raid_health]
    statuses += [d.raid_health for d in virtual if d.raid_health]
    if report is not None:
        statuses.append(report.health)
    return worst(statuses)


def has_scan_data(physical: List[DiskInfo], report=None) -> bool:
    return report is not None or any(is_scanned(d) for d in physical)


def defects_text(disk: DiskInfo) -> str:
    """Grown defects (SAS) or reallocated sectors (ATA), from the SMART reading."""
    for attr in disk.attributes:
        if attr.name == "Grown Defect List":
            return str(attr.current)
    for attr in disk.attributes:
        if attr.id == 5 and "realloc" in attr.name.lower():
            return str(attr.raw)
    return "—"


def single_drive_volume(volume) -> bool:
    return volume is not None and volume.raid_type.upper() == "RAID0" and len(volume.members) == 1


def storage_controller_for(info, label: str):
    """The system_info StorageController whose label is ``label`` (None if unknown)."""
    if info is None:
        return None
    for c in info.controllers:
        if c.label == label:
            return c
    return None
