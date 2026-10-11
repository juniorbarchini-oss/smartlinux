import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any


# smartctl -d types that address one physical disk behind a RAID controller
# ("megaraid,3", "sat+megaraid,3", "cciss,0", ...). Bridge types such as "sat,12" are not.
PASSTHROUGH_TYPE_RE = re.compile(r"^(?:sat\+)?(megaraid|cciss|aacraid|areca|3ware|hpt|sssraid),(\d+)", re.I)


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    WARNING = "warning"
    FAILED = "failed"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        if self == HealthStatus.HEALTHY:
            return "PASSED (HEALTHY)"
        elif self == HealthStatus.WARNING:
            return "WARNING"
        elif self == HealthStatus.FAILED:
            return "CRITICAL FAILURE"
        return "UNSCANNED"

    @property
    def color(self) -> str:
        if self == HealthStatus.HEALTHY:
            return "#10b981"  # Emerald green
        elif self == HealthStatus.WARNING:
            return "#f59e0b"  # Amber yellow
        elif self == HealthStatus.FAILED:
            return "#ef4444"  # Crimson red
        return "#94a3b8"  # Slate gray

    @property
    def icon(self) -> str:
        if self == HealthStatus.HEALTHY:
            return "🟢"
        elif self == HealthStatus.WARNING:
            return "🟡"
        elif self == HealthStatus.FAILED:
            return "🔴"
        return "⚪"


@dataclass
class SmartAttribute:
    id: int
    name: str
    current: str
    worst: str
    threshold: str
    raw: str
    status: str
    status_type: HealthStatus = HealthStatus.HEALTHY
    description: str = ""


@dataclass
class DiskInfo:
    device_path: str                 # e.g., "/dev/sda" or "/dev/nvme0n1"
    name: str                        # e.g., "sda"
    model: str = "Unknown Drive"
    serial: str = "N/A"
    firmware: str = "N/A"
    size_human: str = "Unknown"      # e.g., "240.0 GB" or "1.0 TB"
    size_bytes: int = 0
    protocol: str = "ATA/SATA"       # SATA, NVMe, SCSI, USB
    rotation_rate: str = "SSD"       # "SSD" or "7200 RPM", etc.
    health_status: HealthStatus = HealthStatus.UNKNOWN
    health_summary: str = "Unscanned"
    temperature_c: Optional[int] = None
    power_on_hours: Optional[int] = None
    power_cycles: Optional[int] = None
    attributes: List[SmartAttribute] = field(default_factory=list)
    is_remote: bool = False
    is_usb: bool = False
    server_id: Optional[str] = None
    server_name: Optional[str] = None
    raw_json: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    self_test_status: Optional[str] = None
    # USB adapter hiding SMART in UAS mode (usb_compat.UasCandidate), set after a local scan
    uas_candidate: Optional[Any] = None
    # "Read in compatibility mode on ..." when this reading came from Bulk-Only mode
    compat_read_note: Optional[str] = None
    # Epoch time of the last manual SMART read in this session (None = not scanned yet)
    last_scanned: Optional[float] = None
    # smartctl -d type from `smartctl --scan` when it is needed to address the disk
    # (e.g. "megaraid,3" for a physical disk behind a MegaRAID/PERC controller)
    smart_dev_type: Optional[str] = None
    # Human-readable controller the disk belongs to, e.g. "PERC H730 Mini (MegaRAID)"
    controller: Optional[str] = None
    # Physical slot / device id on that controller (the N of megaraid,N)
    slot: Optional[int] = None
    # Logical volume exported by a RAID controller: no SMART of its own
    is_virtual: bool = False
    # From the vendor tool (perccli/storcli, "Vendor tool" mode only): e.g. "VMDATA1 · RAID0 ·
    # 1 disk · no redundancy" on a virtual drive, the volume name on a physical disk
    raid_volume: Optional[str] = None
    raid_detail: Optional[str] = None
    raid_health: Optional[HealthStatus] = None
    vendor_media_errors: Optional[int] = None   # "Media Error Count" from the vendor tool
    # SAS/SCSI identity details (filled from smartctl when available)
    vendor: Optional[str] = None
    form_factor: Optional[str] = None
    transport: Optional[str] = None

    @property
    def uid(self) -> str:
        """Unique id of this disk on its host. Several physical disks behind one RAID
        controller share a device_path (/dev/bus/0); the -d type tells them apart."""
        if self.smart_dev_type and PASSTHROUGH_TYPE_RE.match(self.smart_dev_type):
            return f"{self.device_path}#{self.smart_dev_type}"
        return self.device_path

    @property
    def display_path(self) -> str:
        """Device path as smartctl needs it, e.g. "/dev/bus/0 -d megaraid,3"."""
        if self.uid != self.device_path:
            return f"{self.device_path} -d {self.smart_dev_type}"
        return self.device_path

    @property
    def host_key(self) -> str:
        """Unique id of this disk across all hosts SmartLinux knows."""
        return f"{self.server_id or 'local'}_{self.uid}"

    @property
    def display_title(self) -> str:
        return f"{self.model} ({self.size_human})"

    @property
    def formatted_power_on(self) -> str:
        if self.power_on_hours is None:
            return "N/A"
        hours = self.power_on_hours
        if hours >= 8760:
            years = hours / 8760.0
            return f"{hours:,} hrs ({years:.1f} yrs)"
        elif hours >= 24:
            days = hours / 24.0
            return f"{hours:,} hrs ({days:.1f} days)"
        return f"{hours:,} hrs"

    @property
    def formatted_temperature(self) -> str:
        if self.temperature_c is None:
            return "N/A"
        return f"{self.temperature_c} °C"


@dataclass
class ServerConfig:
    id: str
    name: str
    host: str
    port: int = 22
    username: str = "root"
    auth_type: str = "password"  # "password" or "key"
    key_path: Optional[str] = None
    password: Optional[str] = None
    legacy_ssh: bool = False  # force the PuTTY `plink` engine (old devices, e.g. ssh-dss only)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "auth_type": self.auth_type,
            "key_path": self.key_path,
            "password": self.password,
            "legacy_ssh": self.legacy_ssh
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ServerConfig":
        return cls(
            id=data.get("id", ""),
            name=data.get("name", "Server"),
            host=data.get("host", ""),
            port=data.get("port", 22),
            username=data.get("username", "root"),
            auth_type=data.get("auth_type", "password"),
            key_path=data.get("key_path"),
            password=data.get("password"),
            legacy_ssh=bool(data.get("legacy_ssh", False))
        )
