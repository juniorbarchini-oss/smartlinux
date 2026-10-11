"""Diagnostic bundle: runs a FIXED list of read-only commands, sanitizes their output and writes
a local .zip the user can hand to the developer. SmartLinux sends nothing and opens no network
connection of its own: it only saves a file where the user chooses.

- Commands are fixed templates (``validate_command``): no shell, no user input. Device paths and
  -d types come from the app's own discovery and are checked against strict patterns. Locally
  argv runs without a shell; over SSH (paramiko or plink, through the existing client) the
  validated tokens are joined with spaces and cannot contain anything a shell interprets.
- Every command is optional: a failure (missing tool on BusyBox, no root...) is recorded in
  the manifest and the bundle goes on. Timeout 30 s per command, 2 MB per output (truncated
  outputs are marked). Nothing is written on the inspected system and nothing is installed.
- The vendor tool (perccli/storcli) is included only if it is already installed and only
  through core/vendor_tools.py's own whitelist.
- Everything goes through core/sanitizer.py before it can be previewed or saved.
"""
import hashlib
import io
import json
import os
import re
import subprocess
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from . import raid
from . import sanitizer as san
from .models import DiskInfo

try:  # optional: present since the "Vendor tool" feature
    from . import vendor_tools
except ImportError:  # pragma: no cover - extension point: without it no vendor tool output is added
    vendor_tools = None

TIMEOUT_S = 30
MAX_OUTPUT_BYTES = 2 * 1024 * 1024

DMI_FIELDS = ("sys_vendor", "product_name", "product_version", "bios_vendor", "bios_version",
              "bios_date", "board_name", "chassis_type")
MODULES = ("megaraid_sas", "hpsa", "smartpqi", "aacraid", "mpt3sas", "nvme")
LSBLK_COLUMNS = "NAME,PATH,MODEL,SIZE,TYPE,TRAN,ROTA,SERIAL,VENDOR,WWN"
MAX_SCSI_HOSTS = 64


class CommandNotAllowed(ValueError):
    """Raised before execution for anything outside the fixed read-only list."""


class Cancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class DiagCommand:
    argv: Tuple[str, ...]
    filename: str
    needs_root: bool = False
    keep: Optional[str] = None   # post-filter: "modinfo" keeps only version/description lines

    @property
    def display(self) -> str:
        return " ".join(self.argv)


# ---- the fixed list ------------------------------------------------------------------------

BASE_COMMANDS: Tuple[DiagCommand, ...] = (
    DiagCommand(("cat", "/etc/os-release"), "system/os-release.txt"),
    DiagCommand(("uname", "-srm"), "system/uname.txt"),
    DiagCommand(("lsblk", "-J", "-d", "-o", LSBLK_COLUMNS), "storage/lsblk.json"),
    DiagCommand(("lspci", "-vmm", "-nn"), "storage/lspci.txt"),
    DiagCommand(("smartctl", "--version"), "smartctl/version.txt"),
    DiagCommand(("smartctl", "--scan", "-j"), "smartctl/scan.json"),
    DiagCommand(("smartctl", "--scan"), "smartctl/scan.txt"),
    *(DiagCommand(("cat", f"/sys/class/dmi/id/{f}"), f"system/dmi_{f}.txt") for f in DMI_FIELDS),
    DiagCommand(("dmidecode", "-t", "system", "-t", "bios"), "system/dmidecode.txt", needs_root=True),
    DiagCommand(("lscpu",), "system/lscpu.txt"),
    DiagCommand(("grep", "MemTotal", "/proc/meminfo"), "system/meminfo.txt"),
    DiagCommand(("ls", "/sys/class/scsi_host"), "storage/scsi_hosts.txt"),
    *(DiagCommand(("modinfo", m), f"storage/modinfo_{m}.txt", keep="modinfo") for m in MODULES),
)
_EXACT = {c.argv for c in BASE_COMMANDS}

_SAFE_TOKEN = re.compile(r"[A-Za-z0-9/._,:+=-]+", re.ASCII)
_DEVICE = re.compile(r"/dev/[A-Za-z0-9][A-Za-z0-9/_.+-]{0,63}", re.ASCII)
_DEV_TYPE = re.compile(r"[a-z0-9]{1,16}(?:[+,][a-z0-9]{1,16}){0,3}", re.ASCII)
_SCSI_HOST = re.compile(r"host[0-9]{1,4}", re.ASCII)


def validate_command(argv: Sequence[str]) -> Tuple[str, ...]:
    """The argv if it is one of the fixed read-only templates, else CommandNotAllowed."""
    if isinstance(argv, (str, bytes)) or not isinstance(argv, (list, tuple)) or not argv:
        raise CommandNotAllowed("A command must be a non-empty list of tokens")
    tokens = tuple(argv)
    for t in tokens:
        if not isinstance(t, str) or not _SAFE_TOKEN.fullmatch(t) or ".." in t:
            raise CommandNotAllowed(f"Forbidden token {t!r}")
    if tokens in _EXACT:
        return tokens
    if len(tokens) == 2 and tokens[0] == "cat":
        m = re.fullmatch(r"/sys/class/scsi_host/(host[0-9]{1,4})/proc_name", tokens[1])
        if m:
            return tokens
    if tokens[0] == "smartctl" and _valid_disk_read(tokens[1:]):
        return tokens
    raise CommandNotAllowed(f"Not in the diagnostic bundle's read-only list: {' '.join(tokens)!r}")


def _valid_disk_read(args: Tuple[str, ...]) -> bool:
    if not args or not _DEVICE.fullmatch(args[-1]):
        return False
    head, rest = args[:-1], ()
    for prefix in (("-j", "-a"), ("-a",), ("-i",)):
        if head[:len(prefix)] == prefix:
            rest = head[len(prefix):]
            break
    else:
        return False
    return rest == () or (len(rest) == 2 and rest[0] == "-d" and bool(_DEV_TYPE.fullmatch(rest[1])))


def _safe_name(device_path: str, dev_type: Optional[str]) -> str:
    name = device_path.strip("/").replace("/", "_")
    if dev_type:
        name += "__" + re.sub(r"[^a-z0-9]", "_", dev_type)
    return name


def disk_commands(disks: Iterable[DiskInfo], device_types: Optional[Dict[str, Optional[str]]] = None,
                  text_mode: bool = False) -> List[DiagCommand]:
    """The same read the app uses for each discovered disk: ``smartctl -j -a [-d T]`` (text
    ``-a`` on old smartctl), ``-d`` from the scan for RAID-attached disks or the type that worked
    last; RAID virtual drives only get ``smartctl -i``."""
    device_types = device_types or {}
    out: List[DiagCommand] = []
    seen = set()
    for disk in disks:
        path = disk.device_path
        if not _DEVICE.fullmatch(path or ""):
            continue
        if raid.is_passthrough_type(disk.smart_dev_type):
            dev_type = disk.smart_dev_type
        else:
            dev_type = device_types.get(path)
        if dev_type and not _DEV_TYPE.fullmatch(dev_type):
            dev_type = None
        opt = ("-d", dev_type) if dev_type else ()
        if disk.is_virtual:
            argv = ("smartctl", "-i", path)
            filename = f"smart/{_safe_name(path, None)}_info.txt"
        elif text_mode:
            argv = ("smartctl", "-a", *opt, path)
            filename = f"smart/{_safe_name(path, dev_type)}.txt"
        else:
            argv = ("smartctl", "-j", "-a", *opt, path)
            filename = f"smart/{_safe_name(path, dev_type)}.json"
        if argv in seen:
            continue
        seen.add(argv)
        out.append(DiagCommand(validate_command(argv), filename))
    return out


def planned_commands(disks: Iterable[DiskInfo], device_types=None, text_mode=False) -> List[str]:
    """What the dialog shows BEFORE anything runs."""
    lines = [c.display + ("   (only if already root)" if c.needs_root else "") for c in BASE_COMMANDS]
    lines.append("cat /sys/class/scsi_host/<hostN>/proc_name   (for each SCSI host listed above)")
    lines += [c.display for c in disk_commands(disks, device_types, text_mode)]
    if vendor_tools is not None:
        lines.append("Vendor tool (perccli / storcli), only if already installed, read-only whitelist:")
        for template in vendor_tools.ALLOWED_TEMPLATES:
            lines.append("    " + " ".join(t.replace(vendor_tools._CTRL, "/cN") for t in template))
    return lines


# ---- hosts --------------------------------------------------------------------------------

Result = Tuple[int, str, str]
_LOCAL_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


class DiagHost:
    engine = "local"
    is_root = False

    def execute(self, argv: Sequence[str], timeout: int, sudo: bool = False) -> Result:
        raise NotImplementedError

    def vendor_host(self):
        return None


class LocalDiagHost(DiagHost):
    engine = "local"

    def __init__(self):
        geteuid = getattr(os, "geteuid", None)
        self.is_root = bool(geteuid and geteuid() == 0)

    def execute(self, argv, timeout, sudo=False):
        env = dict(os.environ)
        env["PATH"] = os.pathsep.join(filter(None, [env.get("PATH", ""), _LOCAL_PATH]))
        env["LC_ALL"] = "C"
        try:
            proc = subprocess.run(list(argv), capture_output=True, timeout=timeout, shell=False, env=env)
        except FileNotFoundError:
            return 127, "", f"{argv[0]}: not found"
        except subprocess.TimeoutExpired:
            return -1, "", f"Timed out after {timeout}s"
        return proc.returncode, _decode(proc.stdout), _decode(proc.stderr)

    def vendor_host(self):
        return vendor_tools.LocalHost() if vendor_tools else None


class RemoteDiagHost(DiagHost):
    """Through the SSH engine the app already uses (paramiko or plink)."""

    def __init__(self, engine_client, engine_name: str = "paramiko"):
        self.client = engine_client
        self.engine = engine_name
        self.is_root = engine_client.config.username == "root"

    def execute(self, argv, timeout, sudo=False):
        cmd = " ".join(argv)
        if sudo:
            return self.client._exec_with_sudo(cmd, timeout=timeout)
        return self.client._raw_exec(cmd, timeout=timeout)

    def vendor_host(self):
        return vendor_tools.RemoteHost(self.client) if vendor_tools else None


def _decode(data) -> str:
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data or ""


# ---- collection ---------------------------------------------------------------------------

@dataclass
class CommandResult:
    command: str
    filename: str
    exit_code: Optional[int] = None
    duration_s: float = 0.0
    content: Optional[str] = None
    truncated: bool = False
    error: Optional[str] = None
    skipped: Optional[str] = None


def _cap(text: str) -> Tuple[str, bool]:
    data = text.encode("utf-8", errors="replace")
    if len(data) <= MAX_OUTPUT_BYTES:
        return text, False
    cut = data[:MAX_OUTPUT_BYTES]
    nl = cut.rfind(b"\n")   # never cut a line (a value) in half
    if nl > 0:
        cut = cut[:nl + 1]
    return cut.decode("utf-8", errors="ignore") + "\n[truncated by SmartLinux: output larger than 2 MB]\n", True


def _filter(text: str, keep: Optional[str]) -> str:
    if keep == "modinfo":
        return "\n".join(l for l in text.splitlines() if l.startswith(("version:", "description:"))) + "\n"
    return text


class Collector:
    def __init__(self, host: DiagHost, progress: Optional[Callable[[str], None]] = None,
                 cancelled: Optional[Callable[[], bool]] = None):
        self.host = host
        self.progress = progress or (lambda msg: None)
        self.cancelled = cancelled or (lambda: False)
        self.results: List[CommandResult] = []

    def _check_cancel(self):
        if self.cancelled():
            raise Cancelled("Cancelled by the user")

    def run(self, cmd: DiagCommand) -> CommandResult:
        self._check_cancel()
        argv = validate_command(cmd.argv)          # raises before anything runs
        result = CommandResult(command=cmd.display, filename=cmd.filename)
        if cmd.needs_root and not self.host.is_root:
            result.skipped = "needs root (not run)"
            self.results.append(result)
            return result
        self.progress(f"Running: {cmd.display}")
        self._execute(argv, result, keep=cmd.keep)
        return result

    def _execute(self, argv, result: CommandResult, keep=None, sudo=False):
        start = time.monotonic()
        try:
            code, out, err = self.host.execute(argv, TIMEOUT_S, sudo=sudo)
        except Exception as e:  # one failing command never aborts the bundle
            code, out, err = None, "", ""
            result.error = str(e) or e.__class__.__name__
        result.duration_s = round(time.monotonic() - start, 3)
        result.exit_code = code
        text = _filter(out or "", keep)
        if err and err.strip():
            text += ("\n" if text and not text.endswith("\n") else "") + "--- stderr ---\n" + err
        result.content, result.truncated = _cap(text)
        if code not in (0, None) and not result.error and not (out or "").strip():
            result.error = (err or "").strip().splitlines()[-1][:200] if (err or "").strip() else f"exit code {code}"
        self.results.append(result)

    def collect(self, disks: Sequence[DiskInfo], device_types=None, text_mode=False):
        for cmd in BASE_COMMANDS:
            self.run(cmd)
        hosts_listing = next((r for r in self.results if r.filename == "storage/scsi_hosts.txt"), None)
        names = sorted({t for t in re.split(r"\s+", (hosts_listing.content or "") if hosts_listing else "")
                        if _SCSI_HOST.fullmatch(t)}, key=lambda h: int(h[4:]))[:MAX_SCSI_HOSTS]
        for name in names:
            self.run(DiagCommand(("cat", f"/sys/class/scsi_host/{name}/proc_name"),
                                 f"storage/scsi_host_{name}_proc_name.txt"))
        for cmd in disk_commands(disks, device_types, text_mode):
            self.run(cmd)
        self.collect_vendor_tool()
        return self.results

    def collect_vendor_tool(self):
        """Extension point: core/vendor_tools.py (perccli/storcli), only its own whitelist."""
        if vendor_tools is None:
            return
        self._check_cancel()
        vh = self.host.vendor_host()
        if vh is None:
            return
        status = CommandResult(command="(look for perccli / storcli)", filename="vendor/detection.txt")
        try:
            binary = vh.find()
        except Exception as e:
            binary, status.error = None, str(e)
        sudo = vh.privilege == "sudo"
        status.content = (f"tool: {binary or 'not installed'}\nprivilege: {vh.privilege}\n"
                          "SmartLinux never installs it; only whitelisted read-only commands are run.\n")
        self.results.append(status)
        if not binary or vh.privilege not in ("root", "sudo"):
            return
        self._vendor(binary, ["-v"], sudo)
        count_res = self._vendor(binary, ["show", "ctrlcount", "J"], sudo)
        reply = vendor_tools.parse_reply(count_res.content or "") if count_res else None
        count = vendor_tools._int(reply.data.get("Controller Count")) if reply and reply.ok else 0
        for index in range(min(count or 0, 16)):
            for kind in ("show", "show_all", "volumes", "drives", "battery", "groups"):
                self._vendor(binary, vendor_tools.controller_args(index, kind), sudo)

    def _vendor(self, binary: str, args: List[str], sudo: bool) -> Optional[CommandResult]:
        self._check_cancel()
        argv = vendor_tools.build_argv(binary, args)       # vendor whitelist: raises before running
        display = " ".join(argv)
        name = os.path.basename(binary) + "_" + "_".join(re.sub(r"[^A-Za-z0-9]", "", a) or "x" for a in args)
        result = CommandResult(command=display, filename=f"vendor/{name}.txt")
        self.progress(f"Running: {display}")
        self._execute(argv, result, sudo=sudo)
        return result


# ---- app view -----------------------------------------------------------------------------

def build_app_view(version: str, engine: str, disks: Sequence[DiskInfo], info=None,
                   vendor_states=None) -> dict:
    """What SmartLinux interpreted (sanitized like everything else before it is saved)."""
    view = {
        "smartlinux_version": version,
        "ssh_engine": engine,
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "disks": [],
        "controllers": [],
    }
    if info is not None:
        view["system"] = {
            "model": info.model_line, "cpu": info.cpu_line, "ram": info.ram_line,
            "os": info.os_name, "proxmox_ve": info.proxmox_version, "kernel": info.kernel,
            "bios_version": info.bios_version, "board": info.board,
        }
        for c in info.controllers:
            view["controllers"].append({
                "label": c.label, "kind": c.kind, "pci_slot": c.pci_slot, "vendor": c.vendor,
                "device": c.device, "subsystem": c.subsystem, "driver": c.driver,
                "driver_version": c.driver_version, "firmware": c.firmware,
                "physical_disks": c.physical_disks if c.counts_known else None,
                "virtual_disks": c.virtual_disks if c.counts_known else None,
            })
    for d in disks:
        view["disks"].append({
            "name": d.name, "device_path": d.device_path, "smart_dev_type": d.smart_dev_type,
            "classification": "virtual (RAID volume)" if d.is_virtual else (
                "physical behind RAID controller" if raid.is_passthrough_type(d.smart_dev_type) else
                ("usb" if d.is_usb else "physical")),
            "controller": d.controller, "slot": d.slot, "model": d.model, "serial": d.serial,
            "firmware": d.firmware, "protocol": d.protocol, "transport": d.transport,
            "rotation": d.rotation_rate, "size": d.size_human, "health": d.health_status.value,
            "health_summary": d.health_summary, "scanned": d.last_scanned is not None,
            "raid_volume": d.raid_volume, "error": d.error_message,
        })
    if vendor_states:
        view["vendor_tool"] = {
            slot: {
                "mode": st.mode,
                "tool": st.tool.found_text if st.tool else "not checked",
                "status": st.report.summary if st.report else None,
                "volumes": [v.summary() for v in st.report.volumes] if st.report else [],
            } for slot, st in vendor_states.items()
        }
    return view


# ---- bundle -------------------------------------------------------------------------------

@dataclass
class Bundle:
    files: Dict[str, str]                 # sanitized: name -> text (README, MANIFEST, app_view, outputs)
    zip_name: str
    replacements: Dict[str, int] = field(default_factory=dict)


def _slug(text: Optional[str]) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:48].strip("-") or "unknown-model"


README_TEMPLATE = """SmartLinux diagnostic bundle
============================

What this is
  The output of a fixed list of READ-ONLY commands (listed in MANIFEST.json) that SmartLinux ran
  on one computer, plus what SmartLinux understood from it (app_view.json). It helps the
  SmartLinux developer support hardware (disks, RAID controllers, USB bridges) they do not have.

SmartLinux version: {version}
Created (UTC):      {created}
Connection:         {engine}

What was sanitized (replaced by consistent fake values, the same original always gets the
same fake value so the files still match each other):
{sanitized}
  Personal values are replaced everywhere they appear: serial numbers, WWN / SAS addresses /
  logical unit ids, UUIDs, service and asset tags, MAC and IP addresses, host and computer
  names, filesystem labels and RAID volume names, user names in paths. SCSI inquiry data
  (which embeds serial numbers) is removed. Before saving, SmartLinux checked that none of the
  original values is left in any file.

What was kept (needed to fix bugs): models, vendors, firmware and software versions, smartctl
device types, sizes and SMART counters.

What this bundle does NOT contain: passwords, SSH keys, the contents of any disk or file system.

How to share it
  SmartLinux did not send it anywhere. Read the files first if you like (they are plain text and
  JSON), then attach the .zip to an e-mail or an issue for the person who asked for it.
"""


def build_bundle(results: Sequence[CommandResult], app_view: dict, version: str, engine: str,
                 model: Optional[str], known: Iterable[Tuple[str, object]] = (),
                 now: Optional[datetime] = None) -> Bundle:
    """Sanitizes everything and assembles README.txt, MANIFEST.json, app_view.json and one file
    per command. Raises sanitizer.SanitizationError if any original value would remain."""
    now = now or datetime.now(timezone.utc)
    raw: Dict[str, str] = {}
    for r in results:
        if r.content is not None:
            raw[r.filename] = r.content
    raw["app_view.json"] = json.dumps(app_view, indent=2, ensure_ascii=False)

    s = san.Sanitizer()
    for category, value in known:
        s.add(category, value)
    for text in raw.values():
        s.collect(text)
    files = {name: s.apply(text) for name, text in raw.items()}
    replacements = s.summary()

    manifest = {
        "format": 1,
        "smartlinux_version": version,
        "created_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "connection": engine,
        "sanitized_replacements": replacements,
        "limits": {"timeout_s": TIMEOUT_S, "max_output_bytes": MAX_OUTPUT_BYTES},
        "commands": [{
            "command": s.apply(r.command),
            "exit_code": r.exit_code,
            "duration_s": r.duration_s,
            "file": r.filename if r.content is not None else None,
            "sha256": hashlib.sha256(files[r.filename].encode("utf-8")).hexdigest() if r.content is not None else None,
            "truncated": r.truncated,
            "error": s.apply(r.error) if r.error else None,
            "skipped": r.skipped,
        } for r in results],
    }
    sanitized_lines = "\n".join(f"  - {san.CATEGORY_TEXT.get(k, k)}: {v} replacement(s)"
                                for k, v in replacements.items()) or "  - (no personal values found)"
    readme = README_TEMPLATE.format(version=version, created=manifest["created_utc"], engine=engine,
                                    sanitized=sanitized_lines)
    files["README.txt"] = s.apply(readme)
    files["MANIFEST.json"] = s.apply(json.dumps(manifest, indent=2, ensure_ascii=False))
    zip_name = s.apply(f"smartlinux-diag-{now.strftime('%Y-%m-%d')}-{_slug(model)}.zip")
    s.verify({**files, "(file name)": zip_name})      # defense in depth: raises, nothing is written
    return Bundle(files=files, zip_name=zip_name, replacements=replacements)


def write_zip(bundle: Bundle, path: str):
    """Writes the bundle (already verified). The zip is built in memory first, so a failure
    never leaves a half-written file behind."""
    order = ["README.txt", "MANIFEST.json", "app_view.json"]
    names = order + sorted(n for n in bundle.files if n not in order)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in names:
            info = zipfile.ZipInfo(name, date_time=time.localtime()[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, bundle.files[name])
    tmp = f"{path}.part"
    with open(tmp, "wb") as f:
        f.write(buf.getvalue())
    os.replace(tmp, path)


def known_identifiers(hostnames: Iterable[Optional[str]] = (), usernames: Iterable[Optional[str]] = (),
                      disks: Iterable[DiskInfo] = ()) -> List[Tuple[str, object]]:
    """Values the app already knows to be personal: host / server names and addresses, the SSH
    and local user names, and disk serials."""
    known: List[Tuple[str, object]] = []
    for h in hostnames:
        if h:
            category = san.IPV4 if re.fullmatch(r"[0-9.]+", h) else (san.IPV6 if ":" in h else san.HOSTNAME)
            known.append((category, h))
    for u in usernames:
        if u:
            known.append((san.USERNAME, u))
    for d in disks:
        if d.serial and d.serial != "N/A":
            known.append((san.SERIAL, d.serial))
        if d.raid_volume:
            known.append((san.LABEL, d.raid_volume.split(" · ")[0]))
    return known
