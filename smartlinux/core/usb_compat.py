"""USB compatibility mode: read full SMART through adapters that hide it in UAS mode.

Some USB-SATA bridges (e.g. Seagate / DJI Fly Drive, 0bc2:a020) only answer plain SCSI
commands while bound to the `uas` driver: smartctl gets a health verdict but no ATA
attribute table, temperature or power-on hours. Bound to the older Bulk-Only driver
(`usb-storage`, forced with the kernel quirk "VID:PID:u") the same drive passes the ATA
commands through and smartctl reads everything.

This module holds the parts that do not need root:
  * detect_uas_smart_block(): read-only detection from sysfs + lsblk.
  * list_volumes() / check_guards(): what would be unmounted and why we may refuse.
  * run_compat_read(): the whole unmount -> set-bot -> read -> restore -> remount flow.
The privileged part lives in the packaged helper (packaging/common/usb-mode-helper),
started through pkexec with the org.smartlinux.usbmode polkit action.

sysfs root, command runner, sleep and clock are injectable so tests run without hardware.
"""
import glob
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, List, Optional, Tuple

from .models import DiskInfo
from .smart_parser import reading_quality

HELPER_PATH = "/usr/lib/smartlinux/usb-mode-helper"

BUSID_RE = re.compile(r"^\d+-\d+(\.\d+)*$")
HEX4_RE = re.compile(r"^[0-9a-f]{4}$")
BLOCK_NAME_RE = re.compile(r"^[a-z]+[0-9]*$")
DEVICE_PATH_RE = re.compile(r"^/dev/[a-z]+[0-9]*$")

UAS_DRIVER = "uas"
BOT_DRIVER = "usb-storage"

PROTECTED_MOUNTS = ("/", "/boot", "/home")
STACKED_TYPES = ("crypt", "lvm", "dm", "mpath")
STACKED_FSTYPES = ("crypto_LUKS", "LVM2_member", "linux_raid_member")

# pkexec: 126 = authentication dialog dismissed, 127 = not authorized / auth failed.
# In both cases the helper never ran, so there is nothing to restore.
PKEXEC_NOT_AUTHORIZED = (126, 127)


@dataclass
class CmdResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


Runner = Callable[[List[str], float], CmdResult]


def default_runner(cmd: List[str], timeout: float = 10) -> CmdResult:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return CmdResult(127, "", f"{cmd[0]}: command not found")
    return CmdResult(proc.returncode, proc.stdout or "", proc.stderr or "")


class CompatModeError(Exception):
    """Compatibility mode refused or failed; the message is shown to the user."""


@dataclass
class UasCandidate:
    """A local USB disk whose adapter hides SMART while bound to `uas`.

    Every field comes from sysfs and is validated; nothing is taken from user input.
    """
    bus_id: str        # USB device id, e.g. "2-1.2"
    vid: str           # "0bc2"
    pid: str           # "a020"
    name: str          # manufacturer + product as the adapter reports them
    device_path: str   # "/dev/sda" at detection time
    interface: str     # "2-1.2:1.0"

    def validate(self):
        if not BUSID_RE.match(self.bus_id or ""):
            raise CompatModeError(f"Invalid USB bus id from sysfs: {self.bus_id!r}")
        if not HEX4_RE.match(self.vid or "") or not HEX4_RE.match(self.pid or ""):
            raise CompatModeError(f"Invalid USB VID/PID from sysfs: {self.vid!r}:{self.pid!r}")
        if not DEVICE_PATH_RE.match(self.device_path or ""):
            raise CompatModeError(f"Unexpected device path: {self.device_path!r}")

    @property
    def usb_id(self) -> str:
        return f"{self.vid}:{self.pid}"


@dataclass
class Volume:
    path: str                      # /dev/sda1
    type: str = ""                 # part, disk, crypt, lvm...
    fstype: str = ""
    uuid: str = ""
    label: str = ""
    mountpoints: List[str] = field(default_factory=list)

    @property
    def is_mounted(self) -> bool:
        return any(m and m != "[SWAP]" for m in self.mountpoints)

    @property
    def is_swap(self) -> bool:
        return self.fstype == "swap" or "[SWAP]" in self.mountpoints

    def describe(self) -> str:
        where = ", ".join(m for m in self.mountpoints if m) or "not mounted"
        label = f"\"{self.label}\"" if self.label else "(no label)"
        return f"{self.path} {label} on {where}"


@dataclass
class CompatReadResult:
    disk: DiskInfo
    restored: bool                 # back on the uas driver
    restore_error: str = ""
    remount_warnings: List[str] = field(default_factory=list)


# ------------------------------------------------------------------ sysfs helpers

def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def _block_name(device_path: str) -> str:
    return os.path.basename(device_path or "")


def locate_usb_device(sysfs_root: str, block_name: str) -> Optional[Tuple[str, str, str]]:
    """Walks up from /sys/block/<name>/device to the USB device (the ancestor holding
    idVendor/idProduct). Returns (usb_dir, bus_id, interface_name) or None."""
    if not BLOCK_NAME_RE.match(block_name or ""):
        return None
    start = os.path.join(sysfs_root, "block", block_name, "device")
    if not os.path.exists(start):
        return None
    path = os.path.realpath(start)
    root = os.path.realpath(sysfs_root)
    child = None
    while path.startswith(root) and path != root:
        if os.path.isfile(os.path.join(path, "idVendor")) and os.path.isfile(os.path.join(path, "idProduct")):
            bus_id = os.path.basename(path)
            interface = os.path.basename(child) if child else ""
            return path, bus_id, interface
        child = path
        path = os.path.dirname(path)
    return None


def interface_driver(usb_dir: str, interface: str) -> Optional[str]:
    link = os.path.join(usb_dir, interface, "driver")
    if os.path.islink(link):
        return os.path.basename(os.readlink(link))
    return None


def find_block_for_usb(sysfs_root: str, bus_id: str, vid: str, pid: str) -> Optional[str]:
    """Current block device name (e.g. 'sdb') of the USB device at bus_id, if it is
    still the same VID:PID. The /dev/sdX node may change after re-enumeration."""
    usb_dir = os.path.join(sysfs_root, "bus", "usb", "devices", bus_id)
    if _read(os.path.join(usb_dir, "idVendor")).lower() != vid or \
            _read(os.path.join(usb_dir, "idProduct")).lower() != pid:
        return None
    pattern = os.path.join(glob.escape(usb_dir), f"{bus_id}:*", "host*", "target*", "*", "block", "*")
    names = sorted(os.path.basename(p) for p in glob.glob(pattern))
    return names[0] if names else None


def current_driver(sysfs_root: str, bus_id: str, interface: str) -> Optional[str]:
    return interface_driver(os.path.join(sysfs_root, "bus", "usb", "devices", bus_id), interface)


# ------------------------------------------------------------------ detection

def has_full_smart_table(disk: DiskInfo) -> bool:
    return bool(disk.raw_json) and reading_quality(disk.raw_json) >= 2


def usb_transport(device_path: str, runner: Runner) -> bool:
    res = runner(["lsblk", "-d", "-n", "-o", "TRAN", device_path], 5)
    return res.returncode == 0 and res.stdout.strip().lower() == "usb"


def detect_uas_smart_block(disk: DiskInfo, sysfs_root: str = "/sys",
                           runner: Runner = default_runner) -> Optional[UasCandidate]:
    """Read-only check: is this a local USB disk, bound to `uas`, whose reading came
    back without a SMART table (only SCSI identity / health)? Nothing is modified."""
    if disk is None or disk.is_remote or has_full_smart_table(disk):
        return None
    if not DEVICE_PATH_RE.match(disk.device_path or ""):
        return None
    block = _block_name(disk.device_path)
    located = locate_usb_device(sysfs_root, block)
    if not located:
        return None
    usb_dir, bus_id, interface = located
    if interface_driver(usb_dir, interface) != UAS_DRIVER:
        return None
    vid = _read(os.path.join(usb_dir, "idVendor")).lower()
    pid = _read(os.path.join(usb_dir, "idProduct")).lower()
    if not BUSID_RE.match(bus_id) or not HEX4_RE.match(vid) or not HEX4_RE.match(pid):
        return None
    if not usb_transport(disk.device_path, runner):
        return None
    name = " ".join(x for x in (_read(os.path.join(usb_dir, "manufacturer")),
                                _read(os.path.join(usb_dir, "product"))) if x) or f"USB adapter {vid}:{pid}"
    return UasCandidate(bus_id=bus_id, vid=vid, pid=pid, name=name,
                        device_path=disk.device_path, interface=interface)


# ------------------------------------------------------------------ volumes & guards

def _flatten(node: dict, out: List[Volume]):
    mps = node.get("mountpoints")
    if mps is None:
        mps = [node.get("mountpoint")]
    out.append(Volume(
        path=node.get("path") or node.get("name") or "",
        type=(node.get("type") or "").lower(),
        fstype=node.get("fstype") or "",
        uuid=node.get("uuid") or "",
        label=node.get("label") or "",
        mountpoints=[m for m in mps if m],
    ))
    for child in node.get("children") or []:
        _flatten(child, out)


def parse_lsblk_tree(text: str) -> List[Volume]:
    data = json.loads(text)
    nodes: List[Volume] = []
    for dev in data.get("blockdevices", []):
        _flatten(dev, nodes)
    return nodes


def list_volumes(device_path: str, runner: Runner = default_runner) -> List[Volume]:
    """Every block node on the disk (the disk itself, partitions, anything stacked)."""
    cols = "NAME,PATH,TYPE,FSTYPE,UUID,LABEL,"
    res = runner(["lsblk", "-J", "-p", "-o", cols + "MOUNTPOINTS", device_path], 5)
    if res.returncode != 0:  # util-linux < 2.37 has no MOUNTPOINTS
        res = runner(["lsblk", "-J", "-p", "-o", cols + "MOUNTPOINT", device_path], 5)
    if res.returncode != 0:
        raise CompatModeError(f"Could not list the volumes of {device_path}: {res.stderr.strip()}")
    return parse_lsblk_tree(res.stdout)


def processes_using(mountpoint: str, runner: Runner = default_runner) -> List[Tuple[int, str]]:
    """Processes with files open on the filesystem mounted at `mountpoint`."""
    res = runner(["lsof", "-w", "-F", "pc", "--", mountpoint], 15)
    if res.returncode != 127:
        procs, pid = [], None
        for line in res.stdout.splitlines():
            if line.startswith("p") and line[1:].isdigit():
                pid = int(line[1:])
            elif line.startswith("c") and pid is not None:
                procs.append((pid, line[1:]))
                pid = None
        return procs

    res = runner(["fuser", "-m", mountpoint], 15)
    if res.returncode == 127:
        return []  # neither tool installed: the unmount itself will fail if busy
    pids = sorted({int(m) for m in re.findall(r"(\d+)[a-zA-Z]*", res.stdout)})
    if not pids:
        return []
    ps = runner(["ps", "-o", "pid=,comm=", "-p", ",".join(map(str, pids))], 5)
    names = {}
    for line in ps.stdout.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            names[int(parts[0])] = parts[1].strip()
    return [(p, names.get(p, "?")) for p in pids]


def check_guards(device_path: str, volumes: List[Volume], runner: Runner = default_runner) -> List[str]:
    """Reasons to refuse compatibility mode for this disk (empty list = OK to proceed)."""
    problems: List[str] = []
    if not usb_transport(device_path, runner):
        problems.append(f"{device_path} is not connected over USB.")
        return problems

    for v in volumes:
        for mp in v.mountpoints:
            if mp in PROTECTED_MOUNTS or mp.startswith("/boot/"):
                problems.append(f"{v.path} is mounted on {mp}, which the system needs.")
        if v.is_swap:
            problems.append(f"{v.path} is in use as swap space.")
        if v.type in STACKED_TYPES or v.fstype in STACKED_FSTYPES or "/mapper/" in v.path:
            problems.append(f"{v.path} is an encrypted (LUKS), LVM or RAID volume ({v.fstype or v.type}).")

    for v in volumes:
        for mp in v.mountpoints:
            if mp == "[SWAP]":
                continue
            procs = processes_using(mp, runner)
            if procs:
                shown = ", ".join(f"{name} (PID {pid})" for pid, name in procs[:8])
                more = f" and {len(procs) - 8} more" if len(procs) > 8 else ""
                problems.append(f"{mp} is in use by: {shown}{more}. Close them and try again.")
    return problems


def verify_candidate(candidate: UasCandidate, sysfs_root: str = "/sys"):
    """The disk must still be the same adapter, at the same port, still on uas
    (it may have been unplugged or re-plugged since the scan)."""
    candidate.validate()
    located = locate_usb_device(sysfs_root, _block_name(candidate.device_path))
    if not located or located[1] != candidate.bus_id or located[2] != candidate.interface:
        raise CompatModeError(f"{candidate.device_path} is no longer the scanned USB drive. Scan it again.")
    usb_dir = located[0]
    if (_read(os.path.join(usb_dir, "idVendor")).lower(), _read(os.path.join(usb_dir, "idProduct")).lower()) != \
            (candidate.vid, candidate.pid):
        raise CompatModeError(f"{candidate.device_path} is no longer the scanned USB drive. Scan it again.")
    if interface_driver(usb_dir, candidate.interface) != UAS_DRIVER:
        raise CompatModeError("The adapter is not in UAS mode any more. Scan the drive again.")


def preflight(candidate: UasCandidate, runner: Runner = default_runner,
              helper_path: str = HELPER_PATH, sysfs_root: str = "/sys") -> Tuple[List[Volume], List[str]]:
    """What the confirmation dialog shows: volumes that will be unmounted + refusals."""
    verify_candidate(candidate, sysfs_root)
    volumes = list_volumes(candidate.device_path, runner)
    problems = check_guards(candidate.device_path, volumes, runner)
    if not os.path.isfile(helper_path):
        problems.append(
            f"The SmartLinux helper ({helper_path}) is not installed. Compatibility mode needs "
            "SmartLinux installed from its package (.deb / Arch package / install.sh)."
        )
    return volumes, problems


def describe_plan(candidate: UasCandidate, volumes: List[Volume]) -> List[Tuple[str, List[str]]]:
    """Exactly what will happen, for the confirmation dialog: [(step, [details])]."""
    mounted = [v for v in volumes if v.is_mounted]
    if mounted:
        unmount = ("These volumes will be unmounted (and mounted again at the end):",
                   [v.describe() for v in mounted])
    else:
        unmount = ("No volume of this drive is mounted, so nothing needs to be unmounted.", [])
    return [
        unmount,
        ("You will be asked for administrator permission (pkexec).", []),
        (f"The drive will be disconnected and reconnected by software for about 20-30 seconds "
         f"(adapter {candidate.name}, USB {candidate.usb_id}). Its /dev name "
         f"({candidate.device_path}) may change.", []),
        ("S.M.A.R.T. is read (read-only; no self-tests are run and nothing is written to the drive).", []),
        ("Normal (UAS) mode is restored and the volumes are mounted again.", []),
        ("Risks:", [
            "The drive runs at reduced speed while in compatibility mode.",
            "If SmartLinux is closed or crashes halfway, the adapter stays in compatibility mode "
            "until you reboot or unplug and replug the drive.",
            "The serial number the drive reports differs between modes, so this reading appears "
            "under a separate entry in the History tab (SmartLinux keeps history per serial).",
        ]),
    ]


# ------------------------------------------------------------------ flow

def compat_note(when: datetime, restored: bool) -> str:
    stamp = when.strftime("%Y-%m-%d %H:%M")
    if restored:
        return f"Read in compatibility mode on {stamp}; the drive is back in normal (UAS) mode."
    return (f"Read in compatibility mode on {stamp}; the drive could NOT be switched back to "
            "normal (UAS) mode. Unplug and replug it (or reboot) to restore it.")


def run_compat_read(candidate: UasCandidate,
                    read_smart: Callable[[str], DiskInfo],
                    runner: Runner = default_runner,
                    sysfs_root: str = "/sys",
                    helper_path: str = HELPER_PATH,
                    progress: Callable[[str], None] = lambda msg: None,
                    sleep: Callable[[float], None] = time.sleep,
                    clock: Callable[[], float] = time.monotonic,
                    now: Callable[[], datetime] = datetime.now,
                    node_timeout: float = 30.0,
                    remount_timeout: float = 20.0,
                    automount_grace: float = 3.0) -> CompatReadResult:
    """unmount -> set-bot -> find new node -> read SMART -> restore (always) -> remount."""
    verify_candidate(candidate, sysfs_root)

    progress("Checking that the drive is not in use...")
    volumes = list_volumes(candidate.device_path, runner)
    problems = check_guards(candidate.device_path, volumes, runner)
    if problems:
        raise CompatModeError("\n".join(problems))

    unmounted: List[Volume] = []
    for v in volumes:
        if not v.is_mounted:
            continue
        progress(f"Unmounting {v.describe()}...")
        res = runner(["udisksctl", "unmount", "-b", v.path], 30)
        if res.returncode != 0:
            warnings = _remount(unmounted, runner, sleep, clock, 0, 0)
            msg = f"Could not unmount {v.path}: {(res.stderr or res.stdout).strip()}"
            if warnings:
                msg += "\n" + "\n".join(warnings)
            raise CompatModeError(msg)
        unmounted.append(v)

    args = [candidate.bus_id, candidate.vid, candidate.pid]
    helper_may_have_run = False
    disk: Optional[DiskInfo] = None
    failure: Optional[BaseException] = None
    restore_error = ""
    restored = False
    remount_warnings: List[str] = []
    final_block: Optional[str] = None
    bot_block: Optional[str] = None
    try:
        progress("Asking for administrator permission...")
        helper_may_have_run = True
        res = runner(["pkexec", helper_path, "set-bot"] + args, 300)
        if res.returncode in PKEXEC_NOT_AUTHORIZED:
            helper_may_have_run = False
            raise CompatModeError("Administrator permission was not granted. Nothing was changed.")
        if res.returncode != 0:
            raise CompatModeError(f"Could not switch the adapter to compatibility mode:\n{res.stderr.strip()}")

        progress("Waiting for the drive to reconnect in compatibility mode...")
        block = _wait_for_block(sysfs_root, candidate, sleep, clock, node_timeout)
        bot_block = block
        if not block:
            raise CompatModeError("The drive did not reappear after switching modes.")

        progress(f"Reading S.M.A.R.T. from /dev/{block} (read-only)...")
        disk = read_smart(f"/dev/{block}")
    except Exception as e:
        failure = e
    finally:
        if bot_block:
            # The desktop may have auto-mounted the drive under its temporary node: never
            # re-attach the port under a mounted filesystem.
            remount_warnings.extend(_unmount_all(f"/dev/{bot_block}", runner, progress))
        if helper_may_have_run:
            progress("Restoring normal (UAS) mode...")
            try:
                res = runner(["pkexec", helper_path, "restore"] + args, 300)
                if res.returncode != 0:
                    restore_error = (res.stderr or res.stdout).strip() or f"helper exited with {res.returncode}"
            except Exception as e:  # never let a restore problem hide the read result
                restore_error = str(e)
            final_block = _wait_for_block(sysfs_root, candidate, sleep, clock, node_timeout)
            restored = not restore_error and \
                current_driver(sysfs_root, candidate.bus_id, candidate.interface) == UAS_DRIVER
            if not restored and not restore_error:
                restore_error = "The adapter did not come back on the UAS driver."
        else:
            final_block = _block_name(candidate.device_path)
            restored = True
        if unmounted:
            progress("Mounting the volumes again...")
            remount_warnings.extend(_remount(unmounted, runner, sleep, clock, remount_timeout, automount_grace))

    if failure is not None:
        msg = str(failure)
        if restore_error:
            msg += ("\n\nThe adapter could not be switched back to normal mode "
                    f"({restore_error}). Unplug and replug the drive (or reboot) to restore it.")
        if remount_warnings:
            msg += "\n\n" + "\n".join(remount_warnings)
        raise CompatModeError(msg) from failure

    final_path = f"/dev/{final_block}" if final_block else candidate.device_path
    disk.device_path = final_path
    disk.name = _block_name(final_path)
    disk.is_usb = True
    disk.compat_read_note = compat_note(now(), restored)
    return CompatReadResult(disk=disk, restored=restored, restore_error=restore_error,
                            remount_warnings=remount_warnings)


def _unmount_all(device_path: str, runner: Runner, progress) -> List[str]:
    try:  # runs right before restore: must never raise
        volumes = list_volumes(device_path, runner)
    except Exception as e:
        return [f"Could not check {device_path} for automatic mounts: {e}"]
    warnings = []
    for v in volumes:
        if v.is_mounted:
            progress(f"Unmounting {v.describe()} (mounted automatically)...")
            res = runner(["udisksctl", "unmount", "-b", v.path], 30)
            if res.returncode != 0:
                warnings.append(f"Could not unmount {v.path} before restoring normal mode: "
                                f"{(res.stderr or res.stdout).strip()}")
    return warnings


def _wait_for_block(sysfs_root, candidate, sleep, clock, timeout) -> Optional[str]:
    deadline = clock() + timeout
    while True:
        block = find_block_for_usb(sysfs_root, candidate.bus_id, candidate.vid, candidate.pid)
        if block or clock() >= deadline:
            return block
        sleep(0.5)


def _is_mounted_uuid(uuid: str, runner: Runner) -> bool:
    res = runner(["findmnt", "-rn", "-o", "TARGET", "-S", f"UUID={uuid}"], 5)
    return res.returncode == 0 and bool(res.stdout.strip())


def _remount(volumes: List[Volume], runner: Runner, sleep, clock,
             timeout: float, grace: float) -> List[str]:
    """Mounts again (by UUID, as the user, through udisks) what we unmounted, unless the
    desktop already auto-mounted it. Returns warnings for what could not be mounted."""
    warnings: List[str] = []
    pending = []
    for v in volumes:
        if v.uuid:
            pending.append(v)
        else:
            warnings.append(f"{v.path} has no UUID; mount it again from your file manager.")
    start = clock()
    deadline = start + timeout
    last_error = {}
    while pending:
        for v in list(pending):
            if _is_mounted_uuid(v.uuid, runner):
                pending.remove(v)
                continue
            if clock() - start < grace:
                continue  # give the desktop's automounter the first chance
            res = runner(["udisksctl", "mount", "-b", f"/dev/disk/by-uuid/{v.uuid}"], 30)
            if res.returncode == 0:
                pending.remove(v)
            else:
                last_error[v.uuid] = (res.stderr or res.stdout).strip()
        if not pending or clock() >= deadline:
            break
        sleep(1.0)
    for v in pending:
        reason = last_error.get(v.uuid, "timed out")
        warnings.append(f"Could not mount {v.describe()} again ({reason}); mount it from your file manager.")
    return warnings
