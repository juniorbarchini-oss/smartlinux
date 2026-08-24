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
                    "Command 'smartctl' is not installed on this system.\n"
                    "On macOS, please install it via Homebrew:\n"
                    "brew install smartmontools"
                )
            else:
                return False, (
                    "Command 'smartctl' is not installed on this system.\n"
                    "Please install the 'smartmontools' package via your package manager:\n"
                    "sudo apt install smartmontools"
                )

        try:
            res = subprocess.run([smartctl_path, "--version"], capture_output=True, text=True, timeout=3)
            if res.returncode != 0:
                return False, f"Error executing smartctl: {res.stderr.strip()}"
        except Exception as e:
            return False, f"Exception executing smartctl: {str(e)}"

        try:
            scan_res = subprocess.run([smartctl_path, "--scan", "-j"], capture_output=True, text=True, timeout=5)
            if scan_res.returncode == 0:
                return True, "smartctl is available with configured permissions."
            else:
                return False, (
                    "smartctl requires permissions to access block storage devices.\n"
                    "Set the SUID bit by running:\n"
                    f"sudo chmod u+s {smartctl_path}"
                )
        except Exception as e:
            return False, f"Error checking smartctl permissions: {str(e)}"

    @classmethod
    def discover_drives(cls) -> List[DiskInfo]:
        """
        Universally discovers real physical disks on local machine (Linux & macOS).
        """
        drives: List[DiskInfo] = []
        smartctl_bin = cls.get_smartctl_bin()
        is_darwin = (platform.system() == "Darwin")

        # Linux Specific Tier 1: lsblk (Rich metadata including TRAN=usb)
        if not is_darwin:
            try:
                cmd = ["lsblk", "-J", "-d", "-o", "NAME,PATH,MODEL,SIZE,TRAN,TYPE,ROTA,HOTPLUG,RM"]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                if proc.returncode == 0 and proc.stdout.strip():
                    data = json.loads(proc.stdout)
                    for dev in data.get("blockdevices", []):
                        dev_name = dev.get("name", "")
                        dev_path = dev.get("path", f"/dev/{dev_name}")
                        dev_type = dev.get("type", "").lower()
                        
                        if dev_type != "disk":
                            continue
                        if any(dev_name.startswith(p) for p in ["loop", "ram", "zram", "dm-", "sr", "cdrom"]):
                            continue

                        model = dev.get("model") or "Physical Drive"
                        size_str = dev.get("size") or "Unknown"
                        if size_str in ("0B", "0", ""):
                            continue
                        tran = (dev.get("tran") or "").upper()
                        is_rm = dev.get("rm") in (True, "1", 1) or dev.get("hotplug") in (True, "1", 1) or (tran == "USB")

                        disk = DiskInfo(
                            device_path=dev_path,
                            name=dev_name,
                            model=model.strip(),
                            size_human=size_str,
                            protocol=tran if tran else "ATA/SATA",
                            is_usb=(tran == "USB" or is_rm)
                        )
                        drives.append(disk)
            except Exception as e:
                print(f"Error enumerating drives with lsblk: {e}")

        # Universal Tier 2: smartctl --scan -j
        if not drives:
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
                        
                        protocol = dev.get("protocol", "SATA").upper()
                        is_usb = ("USB" in protocol or "usb" in dev_name)

                        disk = DiskInfo(
                            device_path=dev_name,
                            name=dev_name.split("/")[-1],
                            model="Physical Drive",
                            protocol=protocol,
                            is_usb=is_usb
                        )
                        drives.append(disk)
            except Exception as e:
                print(f"Error scanning with smartctl: {e}")

        return drives

    @classmethod
    def check_is_usb(cls, device_path: str) -> bool:
        """Checks whether a block device is connected via USB bus."""
        dev_name = device_path.split("/")[-1]
        sys_block_path = f"/sys/block/{dev_name}"
        if os.path.exists(sys_block_path):
            try:
                real = os.path.realpath(sys_block_path).lower()
                return "usb" in real
            except Exception:
                pass
        return False

    @classmethod
    def read_drive_smart(cls, device_path: str) -> DiskInfo:
        """Reads and parses full SMART data for a specific local drive with intelligent bridge fallback."""
        smartctl_bin = cls.get_smartctl_bin()
        device_types = [None, "scsi", "sat,auto", "sntrealtek", "sntjmicron", "sntasmedia", "usbjmicron", "usbsunplus"]
        is_usb = cls.check_is_usb(device_path)
        
        last_error = ""
        best_disk = None

        for dev_type in device_types:
            try:
                cmd = [smartctl_bin, "-j"]
                if dev_type:
                    cmd.extend(["-d", dev_type])
                cmd.extend(["-a", device_path])
                
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
                if proc.stdout:
                    try:
                        data = json.loads(proc.stdout)
                        has_smart = (
                            "smart_status" in data or 
                            "ata_smart_attributes" in data or 
                            "nvme_smart_health_information_log" in data
                        )
                        has_identity = (
                            "model_name" in data or 
                            "scsi_product" in data or 
                            "user_capacity" in data or
                            "serial_number" in data
                        )

                        if has_smart or has_identity:
                            disk = SmartParser.parse_smart_json(data, device_path)
                            if is_usb:
                                disk.is_usb = True
                            if disk.health_status != HealthStatus.UNKNOWN and disk.size_bytes > 0:
                                return disk
                            if best_disk is None or (disk.size_bytes > 0 and best_disk.size_bytes == 0):
                                best_disk = disk
                    except json.JSONDecodeError:
                        pass
                
                if proc.stderr:
                    last_error = proc.stderr.strip()
            except Exception as e:
                last_error = str(e)

        if best_disk and (best_disk.size_bytes > 0 or best_disk.model != "Generic Drive"):
            if is_usb:
                best_disk.is_usb = True
            return best_disk

        disk = DiskInfo(
            device_path=device_path,
            name=device_path.split("/")[-1],
            is_usb=is_usb,
            health_status=HealthStatus.UNKNOWN,
            health_summary="SMART telemetry not available",
            error_message=last_error or "Unsupported device bridge or command failed"
        )
        return disk

    @classmethod
    def eject_drive(cls, device_path: str) -> Tuple[bool, str]:
        """
        Safely unmounts and powers off an external removable/USB drive.
        """
        try:
            # 1. Unmount partitions via udisksctl
            unmount_proc = subprocess.run(
                ["udisksctl", "unmount", "-b", device_path],
                capture_output=True, text=True, timeout=6
            )
            
            # 2. Power off device cleanly
            poweroff_proc = subprocess.run(
                ["udisksctl", "power-off", "-b", device_path],
                capture_output=True, text=True, timeout=6
            )
            
            if poweroff_proc.returncode == 0:
                return True, f"Drive '{device_path}' safely disconnected and powered off."
            
            # Fallback to standard eject
            eject_proc = subprocess.run(
                ["eject", device_path],
                capture_output=True, text=True, timeout=6
            )
            if eject_proc.returncode == 0:
                return True, f"Drive '{device_path}' safely ejected."
            
            err = poweroff_proc.stderr.strip() or eject_proc.stderr.strip() or unmount_proc.stderr.strip()
            return False, f"Could not eject drive: {err}"
        except Exception as e:
            return False, f"Error ejecting drive: {str(e)}"
