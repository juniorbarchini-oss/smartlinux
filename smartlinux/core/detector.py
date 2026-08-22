import json
import os
import shutil
import subprocess
from typing import List, Tuple, Optional
from .models import DiskInfo, HealthStatus
from .smart_parser import SmartParser


class LocalDiskDetector:
    """Discovers physical local drives and reads their SMART metrics."""

    @staticmethod
    def check_smartctl_available() -> Tuple[bool, str]:
        """
        Checks if smartctl is installed and executable without permission errors.
        Returns (is_ok, error_or_warning_message).
        """
        smartctl_path = shutil.which("smartctl")
        if not smartctl_path:
            # Check standard sbin locations
            for candidate in ["/usr/sbin/smartctl", "/sbin/smartctl", "/usr/local/sbin/smartctl"]:
                if os.path.exists(candidate) and os.access(candidate, os.X_OK):
                    smartctl_path = candidate
                    break
        
        if not smartctl_path:
            return False, (
                "El comando 'smartctl' no está instalado en el sistema.\n"
                "Por favor, instale el paquete 'smartmontools' ejecutando:\n"
                "sudo apt install smartmontools"
            )

        # Test running smartctl --version
        try:
            res = subprocess.run([smartctl_path, "--version"], capture_output=True, text=True, timeout=3)
            if res.returncode != 0:
                return False, f"Error al ejecutar smartctl: {res.stderr.strip()}"
        except Exception as e:
            return False, f"Excepción al ejecutar smartctl: {str(e)}"

        # Test running smartctl --scan -j to check permission (SUID or raw disk read)
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
    def get_smartctl_bin(cls) -> str:
        path = shutil.which("smartctl")
        if path:
            return path
        for candidate in ["/usr/sbin/smartctl", "/sbin/smartctl", "/usr/local/sbin/smartctl"]:
            if os.path.exists(candidate):
                return candidate
        return "smartctl"

    @classmethod
    def discover_drives(cls) -> List[DiskInfo]:
        """
        Discovers all real physical disks using lsblk, filtering out loops,
        virtual disks, and RAM disks.
        """
        drives: List[DiskInfo] = []
        smartctl_bin = cls.get_smartctl_bin()

        # Step 1: Use lsblk JSON output to discover block devices of type 'disk'
        try:
            cmd = ["lsblk", "-J", "-d", "-o", "NAME,PATH,MODEL,SIZE,TRAN,TYPE,ROTA"]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if proc.returncode == 0:
                data = json.loads(proc.stdout)
                devices = data.get("blockdevices", [])
                for dev in devices:
                    dev_name = dev.get("name", "")
                    dev_path = dev.get("path", f"/dev/{dev_name}")
                    dev_type = dev.get("type", "").lower()
                    
                    # Filter out non-disk or virtual devices
                    if dev_type != "disk":
                        continue
                    if any(dev_name.startswith(p) for p in ["loop", "ram", "zram", "dm-", "sr", "cdrom"]):
                        continue

                    # Create initial disk entry
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

        # Fallback/Complement with smartctl --scan -j if lsblk returned nothing
        if not drives:
            try:
                proc = subprocess.run([smartctl_bin, "--scan", "-j"], capture_output=True, text=True, timeout=5)
                if proc.returncode == 0:
                    data = json.loads(proc.stdout)
                    for dev in data.get("devices", []):
                        dev_name = dev.get("name", "")
                        if not dev_name:
                            continue
                        disk = DiskInfo(
                            device_path=dev_name,
                            name=dev_name.split("/")[-1],
                            model="Disco Detectado",
                            protocol=dev.get("protocol", "SATA")
                        )
                        drives.append(disk)
            except Exception as e:
                print(f"Error scanning with smartctl: {e}")

        return drives

    @classmethod
    def read_drive_smart(cls, device_path: str) -> DiskInfo:
        """Reads and parses full SMART data for a specific local drive."""
        smartctl_bin = cls.get_smartctl_bin()
        try:
            cmd = [smartctl_bin, "-j", "-a", device_path]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            
            # smartctl returns bitmask return codes where 0 means all passed,
            # but stdout contains valid JSON even if exit code is non-zero (e.g. smart errors)
            if proc.stdout:
                try:
                    data = json.loads(proc.stdout)
                    disk = SmartParser.parse_smart_json(data, device_path)
                    return disk
                except json.JSONDecodeError:
                    pass

            # Error reading
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
