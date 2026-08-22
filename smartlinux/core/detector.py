import json
import os
import platform
import shutil
import subprocess
from typing import List, Tuple, Optional
from .models import DiskInfo, HealthStatus
from .smart_parser import SmartParser


class LocalDiskDetector:
    """Discovers physical local drives across Linux and macOS and reads their SMART metrics."""

    @classmethod
    def get_smartctl_bin(cls) -> str:
        candidates = [
            shutil.which("smartctl"),
            "/opt/homebrew/bin/smartctl",
            "/opt/homebrew/sbin/smartctl",
            "/usr/local/sbin/smartctl",
            "/usr/local/bin/smartctl",
            "/usr/sbin/smartctl",
            "/sbin/smartctl"
        ]
        for candidate in candidates:
            if candidate and os.path.exists(candidate) and os.access(candidate, os.X_OK):
                return candidate
        return "smartctl"

    @classmethod
    def check_smartctl_available(cls) -> Tuple[bool, str]:
        """
        Checks if smartctl is installed and executable without permission errors.
        Returns (is_ok, error_or_warning_message).
        """
        smartctl_path = cls.get_smartctl_bin()
        if not shutil.which(smartctl_path) and not os.path.exists(smartctl_path):
            is_mac = (platform.system() == "Darwin")
            if is_mac:
                return False, (
                    "El comando 'smartctl' no está instalado en este sistema.\n"
                    "En macOS, instálelo con Homebrew ejecutando:\n"
                    "brew install smartmontools"
                )
            else:
                return False, (
                    "El comando 'smartctl' no está instalado en el sistema.\n"
                    "Instale el paquete 'smartmontools' ejecutando:\n"
                    "sudo apt install smartmontools  (o el gestor de paquetes de su distribución)"
                )

        # Test running smartctl --version
        try:
            res = subprocess.run([smartctl_path, "--version"], capture_output=True, text=True, timeout=3)
            if res.returncode != 0:
                return False, f"Error al ejecutar smartctl: {res.stderr.strip()}"
        except Exception as e:
            return False, f"Excepción al ejecutar smartctl: {str(e)}"

        # Test running smartctl --scan -j to check permissions
        try:
            scan_res = subprocess.run([smartctl_path, "--scan", "-j"], capture_output=True, text=True, timeout=5)
            if scan_res.returncode == 0:
                return True, "smartctl disponible y con permisos configurados."
            else:
                return False, (
                    "smartctl requiere permisos para acceder a los dispositivos de bloque.\n"
                    "Configure el bit SUID ejecutando:\n"
                    f"sudo chmod u+s {smartctl_path}"
                )
        except Exception as e:
            return False, f"Error al comprobar permisos de smartctl: {str(e)}"

    @classmethod
    def discover_drives(cls) -> List[DiskInfo]:
        """
        Universally discovers real physical disks on local machine (Linux & macOS).
        """
        drives: List[DiskInfo] = []
        smartctl_bin = cls.get_smartctl_bin()
        is_darwin = (platform.system() == "Darwin")

        # Tier 1: smartctl --scan -j
        try:
            proc = subprocess.run([smartctl_bin, "--scan", "-j"], capture_output=True, text=True, timeout=5)
            if proc.returncode == 0 and proc.stdout.strip():
                data = json.loads(proc.stdout)
                for dev in data.get("devices", []):
                    dev_name = dev.get("name", "")
                    if not dev_name:
                        continue
                    if any(x in dev_name for x in ["/loop", "/ram", "/zram", "/dm-", "/sr"]):
                        continue
                    disk = DiskInfo(
                        device_path=dev_name,
                        name=dev_name.split("/")[-1],
                        model="Disco Físico",
                        protocol=dev.get("protocol", "SATA").upper()
                    )
                    drives.append(disk)
        except Exception as e:
            print(f"Error scanning with smartctl: {e}")

        # Linux Tier 2: lsblk
        if not drives and not is_darwin:
            try:
                cmd = ["lsblk", "-J", "-d", "-o", "NAME,PATH,MODEL,SIZE,TRAN,TYPE,ROTA"]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                if proc.returncode == 0:
                    data = json.loads(proc.stdout)
                    for dev in data.get("blockdevices", []):
                        dev_name = dev.get("name", "")
                        dev_path = dev.get("path", f"/dev/{dev_name}")
                        dev_type = dev.get("type", "").lower()
                        
                        if dev_type != "disk":
                            continue
                        if any(dev_name.startswith(p) for p in ["loop", "ram", "zram", "dm-", "sr", "cdrom"]):
                            continue

                        model = dev.get("model") or "Disco Físico"
                        size_str = dev.get("size") or "Desconocido"
                        tran = dev.get("tran") or "SATA"

                        disk = DiskInfo(
                            device_path=dev_path,
                            name=dev_name,
                            model=model.strip(),
                            size_human=size_str,
                            protocol=tran.upper() if tran else "ATA/SATA"
                        )
                        drives.append(disk)
            except Exception as e:
                print(f"Error enumerating drives with lsblk: {e}")

        return drives

    @classmethod
    def read_drive_smart(cls, device_path: str) -> DiskInfo:
        """Reads and parses full SMART data for a specific local drive."""
        smartctl_bin = cls.get_smartctl_bin()
        try:
            cmd = [smartctl_bin, "-j", "-a", device_path]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            
            if proc.stdout:
                try:
                    data = json.loads(proc.stdout)
                    disk = SmartParser.parse_smart_json(data, device_path)
                    return disk
                except json.JSONDecodeError:
                    pass

            disk = DiskInfo(
                device_path=device_path,
                name=device_path.split("/")[-1],
                health_status=HealthStatus.FAILED,
                health_summary="Error al decodificar salida de smartctl",
                error_message=proc.stderr.strip() or "Salida vacía o no válida"
            )
            return disk

        except Exception as e:
            disk = DiskInfo(
                device_path=device_path,
                name=device_path.split("/")[-1],
                health_status=HealthStatus.UNKNOWN,
                health_summary="Error al ejecutar smartctl",
                error_message=str(e)
            )
            return disk
