from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    WARNING = "warning"
    FAILED = "failed"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        if self == HealthStatus.HEALTHY:
            return "SALUDABLE (PASSED)"
        elif self == HealthStatus.WARNING:
            return "ADVERTENCIA"
        elif self == HealthStatus.FAILED:
            return "FALLO CRÍTICO"
        return "DESCONOCIDO"

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
    model: str = "Desconocido"
    serial: str = "N/A"
    firmware: str = "N/A"
    size_human: str = "Desconocido"  # e.g., "240.0 GB" or "1.0 TB"
    size_bytes: int = 0
    protocol: str = "ATA/SATA"       # SATA, NVMe, SCSI, USB
    rotation_rate: str = "SSD"       # "SSD" or "7200 RPM", etc.
    health_status: HealthStatus = HealthStatus.UNKNOWN
    health_summary: str = "Sin escanear"
    temperature_c: Optional[int] = None
    power_on_hours: Optional[int] = None
    power_cycles: Optional[int] = None
    attributes: List[SmartAttribute] = field(default_factory=list)
    is_remote: bool = False
    server_id: Optional[str] = None
    server_name: Optional[str] = None
    raw_json: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None

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
            return f"{hours:,} hrs ({years:.1f} años)"
        elif hours >= 24:
            days = hours / 24.0
            return f"{hours:,} hrs ({days:.1f} días)"
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
    auth_type: str = "key"  # "key" or "password"
    key_path: Optional[str] = None
    password: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "auth_type": self.auth_type,
            "key_path": self.key_path
            # Note: passwords are not saved to disk for security
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ServerConfig":
        return cls(
            id=data.get("id", ""),
            name=data.get("name", "Servidor"),
            host=data.get("host", ""),
            port=data.get("port", 22),
            username=data.get("username", "root"),
            auth_type=data.get("auth_type", "key"),
            key_path=data.get("key_path"),
            password=None
        )
