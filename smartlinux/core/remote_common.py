"""Transport-independent half of the remote SMART clients.

``BaseRemoteClient`` implements everything that only needs "run this shell command on
the host": smartctl invocation (JSON or, for old smartctl, plain text), self-tests, drive
discovery and SMART reading. Concrete engines (paramiko, PuTTY plink) provide
``connect``/``disconnect``/``is_connected``/``_raw_exec``/``_exec_with_sudo``.

Remote shell snippets are kept BusyBox-ash compatible: no ``$((..))`` arithmetic, no
``which`` (``command -v``), no lsblk/python/udevadm assumptions.
"""
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from . import raid
from .models import DiskInfo, HealthStatus
from .smart_parser import SmartParser, SMART_DEVICE_TYPES, reading_quality, reading_rank, format_bytes_commercial
from .smart_text_parser import (
    is_unrecognized_option, looks_like_smartctl_text, parse_proc_partitions,
    parse_smart_text, sanitize_sys_value, text_reading_quality,
)


UNIVERSAL_PATH = (
    "export PATH=/opt/homebrew/bin:/opt/homebrew/sbin:"
    "/usr/local/sbin:/usr/local/bin:/usr/local/modules/bin:"
    "/usr/sbin:/usr/bin:/sbin:/bin:"
    "$PATH; "
)

_PERMISSION_TERMS = ("permission denied", "operation not permitted", "must be root", "access denied")


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end+1])
        except Exception:
            pass
    return None


def is_permission_denied_json(data: Optional[Dict[str, Any]]) -> bool:
    if not data:
        return False
    messages = data.get("smartctl", {}).get("messages", [])
    for msg in messages:
        str_msg = msg.get("string", "").lower()
        if any(term in str_msg for term in _PERMISSION_TERMS):
            return True
    return False


def is_permission_denied_text(text: str) -> bool:
    low = (text or "").lower()
    return any(term in low for term in _PERMISSION_TERMS)


def smartctl_reply_usable(out: str) -> bool:
    """True when ``out`` is a real smartctl answer (JSON or plain text) that is not a
    permission failure - i.e. retrying through sudo would not improve it."""
    data = extract_json_object(out)
    if data:
        return not is_permission_denied_json(data)
    return looks_like_smartctl_text(out) and not is_permission_denied_text(out)


def parse_scan_text(text: str) -> List[str]:
    """Device paths from plain ``smartctl --scan`` output ('/dev/sda -d ata # ...')."""
    devices = []
    for line in (text or "").splitlines():
        m = re.match(r"\s*(/dev/\S+)", line)
        if m:
            devices.append(m.group(1))
    return devices


class BaseRemoteClient:
    """Shared smartctl logic. Subclasses supply the transport."""

    def __init__(self, config):
        self.config = config
        self._remote_os: Optional[str] = None
        # None = not probed yet, False = smartctl understands -j, True = old smartctl (text only)
        self._text_mode: Optional[bool] = None
        self._device_types: Dict[str, Optional[str]] = {}

    # ---- transport interface (implemented by engines) -------------------------------
    def connect(self, timeout: int = 8) -> Tuple[bool, str]:
        raise NotImplementedError

    def disconnect(self):
        raise NotImplementedError

    def is_connected(self) -> bool:
        raise NotImplementedError

    def _raw_exec(self, cmd: str, timeout: int = 15) -> Tuple[int, str, str]:
        raise NotImplementedError

    def _exec_with_sudo(self, cmd: str, timeout: int = 15) -> Tuple[int, str, str]:
        raise NotImplementedError

    # ---- smartctl ------------------------------------------------------------------
    def _detect_os(self):
        try:
            _, out, _ = self._raw_exec("uname -s")
            self._remote_os = out.strip()
        except Exception:
            self._remote_os = "Linux"

    @property
    def uses_text_mode(self) -> bool:
        return bool(self._text_mode)

    def exec_smartctl(self, args: str, timeout: int = 15) -> Tuple[int, str, str]:
        cmd = f"smartctl {args}"

        if self.config.username != "root" and self.config.password:
            code, out, err = self._exec_with_sudo(cmd, timeout=timeout)
            if smartctl_reply_usable(out):
                return code, out, err

        code, out, err = self._raw_exec(cmd, timeout=timeout)
        if smartctl_reply_usable(out):
            return code, out, err

        if self.config.password or self.config.username != "root":
            code_s, out_s, err_s = self._exec_with_sudo(cmd, timeout=timeout)
            # root on BusyBox has no sudo: keep the original, more telling, answer
            if smartctl_reply_usable(out_s) or not (out or err):
                return code_s, out_s, err_s

        return code, out, err

    def run_self_test(self, device_path: str, test_type: str = "short",
                      dev_type: Optional[str] = None) -> Tuple[bool, str]:
        """Triggers the drive's own native self-test (smartctl -t short|long) remotely.

        Only queues the test; does not wait for completion. Results show up in a
        later scan under the SMART self-test log.

        Deliberately does NOT use exec_smartctl(): that helper retries across up
        to 3 auth paths based on detecting JSON output, which is meaningless for
        this plain-text, state-changing command and previously caused the same
        'smartctl -t' to fire 2-3 times per click - the 2nd/3rd call would then
        collide with the test the 1st call had just started, always reporting a
        false "test already in progress" even though the test did queue.
        """
        mode = "long" if test_type == "long" else "short"
        if raid.is_passthrough_type(dev_type):
            dev_type = self._device_types.get(f"{device_path}#{dev_type}", dev_type)
        else:
            dev_type = self._device_types.get(device_path)
        opt = f"-d {dev_type} " if dev_type else ""
        cmd = f"smartctl {opt}-t {mode} {device_path}"

        use_sudo_first = self.config.username != "root" and bool(self.config.password)
        if use_sudo_first:
            code, out, err = self._exec_with_sudo(cmd, timeout=15)
        else:
            code, out, err = self._raw_exec(cmd, timeout=15)
        output = ((out or "") + (err or "")).strip()

        if code == 0 or "has begun" in output.lower():
            return True, output

        if is_permission_denied_text(output) and not use_sudo_first and self.config.password:
            code, out, err = self._exec_with_sudo(cmd, timeout=15)
            output = ((out or "") + (err or "")).strip()
            if code == 0 or "has begun" in output.lower():
                return True, output

        return False, output or f"smartctl exited with code {code}"

    # ---- host identity ---------------------------------------------------------------
    def collect_system_info(self):
        """Server model, CPU, RAM, OS and storage controllers (read-only, one round trip;
        sudo only for ``dmidecode -t system`` when a non-root user gave a password)."""
        from . import system_info
        sudo = None
        if self.config.username != "root" and self.config.password:
            sudo = lambda cmd: self._exec_with_sudo(cmd, timeout=15)  # noqa: E731
        return system_info.collect(lambda cmd: self._raw_exec(cmd, timeout=25), sudo_run=sudo)

    # ---- discovery -----------------------------------------------------------------
    def _new_disk(self, device_path: str, **kwargs) -> DiskInfo:
        return DiskInfo(
            device_path=device_path,
            name=device_path.split("/")[-1],
            is_remote=True,
            server_id=self.config.id,
            server_name=self.config.name,
            **kwargs
        )

    def discover_remote_drives(self) -> List[DiskInfo]:
        drives: List[DiskInfo] = []
        is_darwin = (self._remote_os == "Darwin")

        code, out, err = self.exec_smartctl("--scan -j")
        json_scan = extract_json_object(out)
        if json_scan and "devices" in json_scan:
            skip = (lambda n: any(x in n for x in ["diskimage", "synthesized"])) if is_darwin else None
            for entry in raid.scan_entries(json_scan, skip=skip):
                drives.append(raid.disk_from_scan_entry(entry, self._new_disk))
            if not is_darwin:
                self._apply_sysfs_identity([d for d in drives if not raid.is_passthrough_type(d.smart_dev_type)])
        elif is_unrecognized_option(out):
            # smartctl 5.4x..6.x: --scan exists but -j does not. 5.38 has neither.
            self._text_mode = True
            code, out, err = self.exec_smartctl("--scan")
            for dev_name in parse_scan_text(out):
                if any(x in dev_name for x in ["/loop", "/ram", "/zram", "/dm-", "/sr"]):
                    continue
                drives.append(self._new_disk(dev_name, model="Physical Drive"))

        if not drives and not is_darwin and not self._text_mode:
            # skipped on old/embedded boxes (text-only smartctl): they have no lsblk
            drives = self._discover_lsblk()

        if not drives and is_darwin:
            code, out, err = self._raw_exec("diskutil list physical | grep -E '^/dev/disk'")
            if out.strip():
                for line in out.strip().splitlines():
                    match = re.search(r'(/dev/disk\d+)', line)
                    if match:
                        drives.append(self._new_disk(match.group(1), model="Mac Physical Drive"))

        if not drives and not is_darwin:
            drives = self._discover_proc_partitions()

        raid.harmonize_controller_labels(drives)
        return drives

    def _apply_sysfs_identity(self, drives: List[DiskInfo]):
        """Model/vendor/size of scanned block devices from /sys/block (BusyBox-safe, one
        round trip). Marks RAID virtual drives (e.g. model "PERC H730 Mini")."""
        by_name = {}
        for d in drives:
            name = d.device_path.split("/")[-1]
            if d.device_path.startswith("/dev/") and re.fullmatch(r"[A-Za-z0-9]+", name):
                by_name[name] = d
        if not by_name:
            return
        loop = " ".join(by_name)
        try:
            code, out, err = self._raw_exec(
                f'for d in {loop}; do echo "@@$d|$(cat /sys/block/$d/device/vendor 2>/dev/null)|'
                f'$(cat /sys/block/$d/device/model 2>/dev/null)|$(cat /sys/block/$d/size 2>/dev/null)"; done'
            )
        except Exception:
            return
        for line in (out or "").splitlines():
            if not line.startswith("@@"):
                continue
            parts = line[2:].split("|")
            if len(parts) != 4 or parts[0] not in by_name:
                continue
            disk = by_name[parts[0]]
            vendor, model = sanitize_sys_value(parts[1]), sanitize_sys_value(parts[2])
            if model:
                disk.model = model
            size_bytes = raid.sectors_to_bytes(parts[3])
            if size_bytes:
                disk.size_bytes = size_bytes
                disk.size_human = format_bytes_commercial(size_bytes)
            if raid.is_virtual_raid_model(model, vendor):
                raid.mark_virtual(disk)

    def _discover_lsblk(self) -> List[DiskInfo]:
        drives: List[DiskInfo] = []
        code, out, err = self._raw_exec("lsblk -J -d -o NAME,PATH,MODEL,SIZE,TRAN,TYPE,ROTA,VENDOR 2>/dev/null")
        json_lsblk = extract_json_object(out)
        if json_lsblk and "blockdevices" in json_lsblk:
            for dev in json_lsblk.get("blockdevices", []):
                dev_name = dev.get("name", "")
                dev_path = dev.get("path", f"/dev/{dev_name}")
                dev_type = dev.get("type", "").lower()

                if dev_type != "disk":
                    continue
                if any(dev_name.startswith(p) for p in ["loop", "ram", "zram", "dm-", "sr", "cdrom"]):
                    continue

                model = dev.get("model") or "Physical Drive"
                size_str = dev.get("size") or "Unknown"
                tran = (dev.get("tran") or "SATA").upper()
                disk = self._new_disk(
                    dev_path, model=model.strip(), size_human=size_str,
                    protocol=tran if tran else "ATA/SATA", is_usb=(tran == "USB")
                )
                if raid.is_virtual_raid_model(disk.model, dev.get("vendor")):
                    raid.mark_virtual(disk)
                drives.append(disk)
        return drives

    def _discover_proc_partitions(self) -> List[DiskInfo]:
        """BusyBox/embedded fallback: whole disks from /proc/partitions, model from /sys/block."""
        drives: List[DiskInfo] = []
        code, out, err = self._raw_exec("cat /proc/partitions 2>/dev/null")
        found = parse_proc_partitions(out)

        if not found:
            code, out, err = self._raw_exec("ls -d /sys/block/sd* /sys/block/hd* /sys/block/nvme* 2>/dev/null")
            found = [(line.strip().split("/")[-1], 0) for line in out.splitlines() if line.strip()]

        models: Dict[str, str] = {}
        names = [n for n, _ in found if re.fullmatch(r"[A-Za-z0-9]+", n)]
        if names:
            loop = " ".join(names)
            code, out, err = self._raw_exec(
                f'for n in {loop}; do echo "$n|$(cat /sys/block/$n/device/model 2>/dev/null)"; done'
            )
            for line in out.splitlines():
                name, _, model = line.partition("|")
                if model.strip():
                    models[name.strip()] = sanitize_sys_value(model)

        for name, size_kib in found:
            size_bytes = size_kib * 1024
            drives.append(self._new_disk(
                f"/dev/{name}",
                model=models.get(name, "Physical Drive"),
                size_bytes=size_bytes,
                size_human=format_bytes_commercial(size_bytes) if size_bytes else "Unknown",
                protocol="NVMe" if name.startswith("nvme") else "ATA/SATA",
            ))
        return drives

    # ---- SMART reading ---------------------------------------------------------------
    def _stamp(self, disk: DiskInfo, device_path: str) -> DiskInfo:
        disk.is_remote = True
        disk.server_id = self.config.id
        disk.server_name = self.config.name
        return disk

    def read_remote_drive_smart(self, device_path: str, dev_type: Optional[str] = None) -> DiskInfo:
        """``dev_type``: explicit -d type from the scan for a disk behind a RAID controller
        (e.g. "megaraid,3"); then only that type (and its SAT variant) is tried."""
        explicit = raid.is_passthrough_type(dev_type)
        types = raid.read_types_for(dev_type) if explicit else SMART_DEVICE_TYPES
        type_key = f"{device_path}#{dev_type}" if explicit else device_path

        def stamp(disk: DiskInfo) -> DiskInfo:
            disk = self._stamp(disk, device_path)
            if explicit:
                disk.smart_dev_type = dev_type
                disk.slot = raid.slot_from_type(dev_type)
                disk.name = raid.physical_name(dev_type)
            return disk

        best_disk = None
        best_quality = (False, 0)
        best_type: Optional[str] = None
        last_error = ""
        last_json = None
        last_code = 0
        denied_text = False

        for try_type in types:
            opt = f"-d {try_type} " if try_type else ""

            if not self._text_mode:
                code, out, err = self.exec_smartctl(f"-j {opt}-a {device_path}")
                last_code = code
                json_data = extract_json_object(out)
                if json_data:
                    last_json = json_data
                    if not is_permission_denied_json(json_data):
                        quality = reading_quality(json_data)
                        if quality:
                            try:
                                disk = stamp(SmartParser.parse_smart_json(json_data, device_path))
                                if quality == 2 and disk.health_status != HealthStatus.UNKNOWN and disk.size_bytes > 0:
                                    self._device_types[type_key] = try_type
                                    return disk
                                if best_disk is None or reading_rank(quality, disk.size_bytes) > best_quality:
                                    best_disk, best_quality, best_type = disk, reading_rank(quality, disk.size_bytes), try_type
                            except Exception as e:
                                print(f"Error parsing SMART JSON: {e}")
                elif is_unrecognized_option(out):
                    self._text_mode = True  # smartctl < 5.43: no -j, fall through to text
                if err:
                    last_error = err.strip()

            if self._text_mode:
                code, out, err = self.exec_smartctl(f"{opt}-a {device_path}")
                last_code = code
                if is_permission_denied_text(out + err):
                    denied_text = True
                quality = text_reading_quality(out)
                if quality:
                    try:
                        disk = stamp(parse_smart_text(out, device_path))
                        if quality == 2 and disk.health_status != HealthStatus.UNKNOWN and disk.size_bytes > 0:
                            self._device_types[type_key] = try_type
                            return disk
                        if best_disk is None or reading_rank(quality, disk.size_bytes) > best_quality:
                            best_disk, best_quality, best_type = disk, reading_rank(quality, disk.size_bytes), try_type
                    except Exception as e:
                        print(f"Error parsing SMART text: {e}")
                if err:
                    last_error = err.strip()
                elif out and not quality:
                    last_error = out.strip().splitlines()[-1] if out.strip() else last_error

        if best_disk and (best_disk.size_bytes > 0 or best_disk.model != "Generic Drive"):
            self._device_types[type_key] = best_type
            return best_disk

        error_detail = last_error
        if (last_json and is_permission_denied_json(last_json)) or denied_text:
            error_detail = "Permission denied: administrator / sudo privileges required."
        elif "not found" in error_detail.lower():
            if self._remote_os == "Darwin":
                error_detail = "smartctl not found. Install on macOS with: brew install smartmontools"
            else:
                error_detail = "smartctl not found. Install 'smartmontools' on the remote host."
        elif "permission denied" in error_detail.lower() or "operation not permitted" in error_detail.lower():
            error_detail = "Permission denied accessing block device. Check sudo or SUID permissions."
        elif not error_detail:
            error_detail = f"No valid SMART response from device (exit code {last_code})"

        return stamp(DiskInfo(
            device_path=device_path,
            name=device_path.split("/")[-1],
            health_status=HealthStatus.FAILED,
            health_summary="SMART read failure",
            error_message=error_detail
        ))
