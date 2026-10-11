import json
import os
import platform
import shutil
import subprocess
from typing import List, Tuple, Optional
from .models import DiskInfo, HealthStatus
from .smart_parser import SmartParser, SMART_DEVICE_TYPES, reading_quality, reading_rank, format_bytes_commercial
from . import raid


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
                cmd = ["lsblk", "-J", "-d", "-o", "NAME,PATH,MODEL,SIZE,TRAN,TYPE,ROTA,HOTPLUG,RM,VENDOR"]
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
                        if raid.is_virtual_raid_model(disk.model, dev.get("vendor")):
                            raid.mark_virtual(disk)
                        drives.append(disk)
            except Exception as e:
                print(f"Error enumerating drives with lsblk: {e}")

        # Universal Tier 2: smartctl --scan -j. Also run next to lsblk: physical disks behind
        # a RAID controller (-d megaraid,N /dev/bus/0) are invisible to lsblk.
        scan_json = cls._scan_json(smartctl_bin)
        entries = raid.scan_entries(scan_json)
        if drives:
            for entry in entries:
                if raid.is_passthrough_type(entry["type"]):
                    drives.append(raid.disk_from_scan_entry(entry, cls._new_disk))
        else:
            for entry in entries:
                disk = raid.disk_from_scan_entry(entry, cls._new_disk)
                if not raid.is_passthrough_type(disk.smart_dev_type):
                    cls._apply_sysfs_identity(disk)
                drives.append(disk)

        raid.harmonize_controller_labels(drives)
        return drives

    @staticmethod
    def _new_disk(device_path: str, **kwargs) -> DiskInfo:
        return DiskInfo(device_path=device_path, name=device_path.split("/")[-1], **kwargs)

    @staticmethod
    def _scan_json(smartctl_bin: str) -> Optional[dict]:
        try:
            proc = subprocess.run([smartctl_bin, "--scan", "-j"], capture_output=True, text=True, timeout=5)
            if proc.returncode == 0 and proc.stdout.strip():
                return json.loads(proc.stdout)
        except Exception as e:
            print(f"Error scanning with smartctl: {e}")
        return None

    @staticmethod
    def _apply_sysfs_identity(disk: DiskInfo):
        """Model/vendor/size of a scanned block device from /sys/block (no lsblk)."""
        base = f"/sys/block/{disk.device_path.split('/')[-1]}"

        def read(rel: str) -> str:
            try:
                with open(os.path.join(base, rel)) as f:
                    return f.read().strip()
            except OSError:
                return ""

        model, vendor = read("device/model"), read("device/vendor")
        if model:
            disk.model = model
        size_bytes = raid.sectors_to_bytes(read("size"))
        if size_bytes:
            disk.size_bytes = size_bytes
            disk.size_human = format_bytes_commercial(size_bytes)
        if raid.is_virtual_raid_model(model, vendor):
            raid.mark_virtual(disk)

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
    def read_drive_smart(cls, device_path: str, dev_type: Optional[str] = None) -> DiskInfo:
        """Reads and parses full SMART data for a specific local drive with intelligent bridge fallback.

        ``dev_type`` is the explicit -d type from the scan for disks behind a RAID controller
        (e.g. "megaraid,3"); then only that type (and its SAT variant) is tried."""
        smartctl_bin = cls.get_smartctl_bin()
        explicit = raid.is_passthrough_type(dev_type)
        device_types = raid.read_types_for(dev_type) if explicit else SMART_DEVICE_TYPES
        is_usb = False if explicit else cls.check_is_usb(device_path)
        
        last_error = ""
        best_disk = None
        best_quality = (False, 0)

        for try_type in device_types:
            try:
                cmd = [smartctl_bin, "-j"]
                if try_type:
                    cmd.extend(["-d", try_type])
                cmd.extend(["-a", device_path])
                
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
                if proc.stdout:
                    try:
                        data = json.loads(proc.stdout)
                        quality = reading_quality(data)
                        if quality:
                            disk = SmartParser.parse_smart_json(data, device_path)
                            if is_usb:
                                disk.is_usb = True
                            if explicit:
                                cls._stamp_passthrough(disk, dev_type)
                            if quality == 2 and disk.health_status != HealthStatus.UNKNOWN and disk.size_bytes > 0:
                                return disk
                            if best_disk is None or reading_rank(quality, disk.size_bytes) > best_quality:
                                best_disk, best_quality = disk, reading_rank(quality, disk.size_bytes)
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
        if explicit:
            cls._stamp_passthrough(disk, dev_type)
        return disk

    @staticmethod
    def _stamp_passthrough(disk: DiskInfo, dev_type: str):
        """Keeps a RAID-attached disk addressable after a read: same -d type, slot and name
        as listed, whatever variant (sat+megaraid,N) actually answered."""
        disk.smart_dev_type = dev_type
        disk.slot = raid.slot_from_type(dev_type)
        disk.name = raid.physical_name(dev_type)

    @classmethod
    def run_self_test(cls, device_path: str, test_type: str = "short",
                      dev_type: Optional[str] = None) -> Tuple[bool, str]:
        """Triggers the drive's own native self-test (smartctl -t short|long).

        Only queues the test on the disk's own controller and returns immediately;
        it does not wait for completion (a long test can take hours). Results show
        up in a later 'Scan Now' under the SMART self-test log.
        """
        smartctl_bin = cls.get_smartctl_bin()
        mode = "long" if test_type == "long" else "short"
        try:
            opt = ["-d", dev_type] if raid.is_passthrough_type(dev_type) else []
            proc = subprocess.run(
                [smartctl_bin, *opt, "-t", mode, device_path],
                capture_output=True, text=True, timeout=15
            )
            output = (proc.stdout or "") + (proc.stderr or "")
            if proc.returncode == 0 or "has begun" in output.lower():
                return True, output.strip() or f"Self-test ({mode}) started."
            return False, output.strip() or f"smartctl exited with code {proc.returncode}"
        except Exception as e:
            return False, f"Error starting self-test: {str(e)}"

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
