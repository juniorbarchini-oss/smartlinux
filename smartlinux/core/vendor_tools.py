"""Optional "Vendor tool" mode for MegaRAID / PERC controllers: perccli / storcli, read-only.

What smartctl cannot tell (RAID state, which physical disks build each volume, the
controller's own error counters, the battery) comes from the vendor CLI, but ONLY when it is
already installed and ONLY through a fixed read-only whitelist:

- SmartLinux never installs anything (locally or over SSH); a missing tool is only reported.
- The executable is one of CANDIDATES (fixed). Nothing is taken from the user or from command
  output: output is only used to test membership in CANDIDATES.
- Every invocation is validated against ALLOWED_TEMPLATES before anything runs: fixed tokens,
  controller number ``^[0-9]{1,2}$``, no shell metacharacters, no write verbs. Locally argv is
  passed without a shell; over SSH the validated tokens are joined with spaces (they cannot
  contain anything a shell would interpret).
- It runs only when the user connects / refreshes a host with the controller set to
  "Vendor tool" (AppSettings, per host + controller; default "Standard read").
"""
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .models import DiskInfo, HealthStatus

# ---- policy -------------------------------------------------------------------------------

CANDIDATES: Tuple[str, ...] = (
    "perccli64", "perccli", "storcli64", "storcli",
    "/opt/MegaRAID/perccli/perccli64", "/opt/MegaRAID/storcli/storcli64",
)

MODE_STANDARD = "standard"
MODE_VENDOR = "vendor"
SETTINGS_KEY = "vendor_tool_modes"
SUPPORTED_DRIVERS = ("megaraid_sas",)

_CTRL = r"/c([0-9]{1,2})"
# Each template: first token (exact or regex on the /cN form), then exact tokens.
ALLOWED_TEMPLATES: Tuple[Tuple[str, ...], ...] = (
    ("-v",),
    ("show", "ctrlcount", "J"),
    (_CTRL, "show", "J"),
    (_CTRL, "show", "all", "J"),
    (_CTRL + "/vall", "show", "all", "J"),
    (_CTRL + "/eall/sall", "show", "all", "J"),
    (_CTRL + "/bbu", "show", "all", "J"),
    (_CTRL + "/dall", "show", "J"),
)

FORBIDDEN_WORDS = (
    "set", "start", "stop", "add", "del", "delete", "import", "clear", "flash", "secure",
    "foreign", "patrolread", "download", "erase", "pause", "resume", "suspend", "compare",
    "expand", "migrate", "insert", "spinup", "spindown", "transform", "restart", "reset",
)
_SAFE_TOKEN = re.compile(r"[A-Za-z0-9/._-]+", re.ASCII)
_CONTROLLER_ID = re.compile(r"[0-9]{1,2}", re.ASCII)


class CommandNotAllowed(ValueError):
    """Raised BEFORE execution for anything outside the read-only whitelist."""


def validate_controller(controller) -> str:
    text = str(controller)
    if isinstance(controller, bool) or not _CONTROLLER_ID.fullmatch(text):
        raise CommandNotAllowed(f"Invalid controller number: {text!r}")
    return text


def validate_args(args: Sequence[str]) -> List[str]:
    """Returns the arguments if they match one read-only template exactly, else raises."""
    if isinstance(args, (str, bytes)) or not isinstance(args, (list, tuple)):
        raise CommandNotAllowed("Arguments must be a list of tokens")
    tokens = list(args)
    if not tokens or not all(isinstance(t, str) for t in tokens):
        raise CommandNotAllowed("Arguments must be non-empty strings")
    for token in tokens:
        if not _SAFE_TOKEN.fullmatch(token):
            raise CommandNotAllowed(f"Forbidden characters in {token!r}")
        low = token.lower()
        for word in FORBIDDEN_WORDS:
            if re.search(rf"(^|[/_-]){word}($|[/_-])", low):
                raise CommandNotAllowed(f"Write operation {word!r} is not allowed")
    for template in ALLOWED_TEMPLATES:
        if len(template) != len(tokens):
            continue
        first, rest = template[0], template[1:]
        first_ok = (tokens[0] == first) if not first.startswith("/c") else \
            re.fullmatch(first, tokens[0], re.ASCII) is not None
        if first_ok and tuple(tokens[1:]) == rest:
            return tokens
    raise CommandNotAllowed(f"Not in the read-only whitelist: {' '.join(tokens)!r}")


def controller_args(controller, kind: str) -> List[str]:
    """Whitelisted arguments for controller ``controller`` (validated ``^[0-9]{1,2}$``)."""
    c = validate_controller(controller)
    forms = {
        "show": [f"/c{c}", "show", "J"],
        "show_all": [f"/c{c}", "show", "all", "J"],
        "volumes": [f"/c{c}/vall", "show", "all", "J"],
        "drives": [f"/c{c}/eall/sall", "show", "all", "J"],
        "battery": [f"/c{c}/bbu", "show", "all", "J"],
        "groups": [f"/c{c}/dall", "show", "J"],
    }
    if kind not in forms:
        raise CommandNotAllowed(f"Unknown read: {kind!r}")
    return validate_args(forms[kind])


def build_argv(binary: str, args: Sequence[str]) -> List[str]:
    if binary not in CANDIDATES:
        raise CommandNotAllowed(f"Executable not in the fixed candidate list: {binary!r}")
    return [binary] + validate_args(args)


def mode_key(server_id: Optional[str], pci_slot: str) -> str:
    return f"{server_id or 'local'}|{pci_slot}"


# ---- hosts (local / SSH) ------------------------------------------------------------------

Result = Tuple[int, str, str]


class ToolHost:
    """Where the tool runs. ``privilege``: "root", "sudo" or "none" (cannot read the controller)."""
    privilege = "none"

    def find(self) -> Optional[str]:
        raise NotImplementedError

    def _execute(self, argv: List[str]) -> Result:
        raise NotImplementedError

    def run(self, binary: str, args: Sequence[str]) -> Result:
        argv = build_argv(binary, args)  # raises before anything runs
        return self._execute(argv)


_LOCAL_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


class LocalHost(ToolHost):
    def __init__(self):
        geteuid = getattr(os, "geteuid", None)
        self.privilege = "root" if geteuid and geteuid() == 0 else "none"

    def find(self) -> Optional[str]:
        path = os.pathsep.join(filter(None, [os.environ.get("PATH", ""), _LOCAL_PATH]))
        for candidate in CANDIDATES:
            if candidate.startswith("/"):
                if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                    return candidate
            elif shutil.which(candidate, path=path):
                return candidate
        return None

    def _execute(self, argv: List[str]) -> Result:
        env = dict(os.environ)
        env["PATH"] = os.pathsep.join(filter(None, [env.get("PATH", ""), _LOCAL_PATH]))
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=60, shell=False, env=env)
        return proc.returncode, proc.stdout, proc.stderr


class RemoteHost(ToolHost):
    """Runs through an existing SSH client (paramiko or plink engine)."""

    def __init__(self, client):
        self.client = client
        config = client.config
        if config.username == "root":
            self.privilege = "root"
        elif config.password:
            self.privilege = "sudo"
        else:
            self.privilege = "none"

    def find(self) -> Optional[str]:
        script = "for p in " + " ".join(CANDIDATES) + \
                 '; do command -v "$p" >/dev/null 2>&1 && echo "@@found $p"; done'
        _, out, _ = self.client._raw_exec(script, timeout=15)
        found = {line[len("@@found "):].strip() for line in (out or "").splitlines() if line.startswith("@@found ")}
        for candidate in CANDIDATES:  # our constant, never the output itself
            if candidate in found:
                return candidate
        return None

    def _execute(self, argv: List[str]) -> Result:
        cmd = " ".join(argv)
        if self.privilege == "sudo":
            return self.client._exec_with_sudo(cmd, timeout=60)
        return self.client._raw_exec(cmd, timeout=60)


# ---- detection ----------------------------------------------------------------------------

@dataclass
class ToolStatus:
    binary: Optional[str] = None
    version: Optional[str] = None
    privilege: str = "none"
    error: Optional[str] = None

    @property
    def installed(self) -> bool:
        return self.binary is not None

    @property
    def name(self) -> str:
        return os.path.basename(self.binary) if self.binary else ""

    @property
    def usable(self) -> bool:
        return self.installed and self.privilege in ("root", "sudo")

    @property
    def found_text(self) -> str:
        if not self.installed:
            return "not installed"
        return f"{self.name} {self.version} found" if self.version else f"{self.name} found"


NOT_ROOT_TEXT = (
    "Reading the controller with {tool} needs administrator rights. This connection has neither "
    "root nor sudo: connect as root or as a user with sudo (password saved for this server)."
)
NOT_ROOT_LOCAL_TEXT = (
    "Reading the controller with {tool} needs administrator rights and SmartLinux is not running "
    "as root on this computer. Run it as root to use the vendor tool locally."
)


def parse_version(text: str) -> Optional[str]:
    m = re.search(r"Ver(?:sion)?\s+([0-9]+\.[0-9]+)", text or "")
    return m.group(1) if m else None


def detect(host: ToolHost) -> ToolStatus:
    """Which candidate is installed and its version (``-v``). Never installs anything."""
    status = ToolStatus(privilege=host.privilege)
    try:
        status.binary = host.find()
    except Exception as e:
        status.error = f"Could not look for perccli/storcli: {e}"
        return status
    if not status.binary:
        return status
    try:
        _, out, err = host.run(status.binary, ["-v"])
        status.version = parse_version((out or "") + "\n" + (err or ""))
    except CommandNotAllowed:
        raise
    except Exception as e:
        status.error = f"{status.name} -v failed: {e}"
    return status


# ---- JSON helpers -------------------------------------------------------------------------

def _json(text: str) -> Optional[dict]:
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except Exception:
                return None
    return None


@dataclass
class Reply:
    ok: bool
    data: dict = field(default_factory=dict)
    missing: bool = False           # "... doesn't exist": the component is not there
    message: str = ""


def parse_reply(text: str) -> Reply:
    """Response Data of controller 0 of a ``... J`` reply. A Failure whose message says that
    something "doesn't exist" means the component is absent, not an error."""
    doc = _json(text)
    try:
        ctrl = doc["Controllers"][0]
    except Exception:
        return Reply(ok=False, message="No JSON reply")
    status = ctrl.get("Command Status", {})
    if str(status.get("Status", "")).lower() == "success":
        return Reply(ok=True, data=ctrl.get("Response Data", {}) or {})
    messages = [str(d.get("ErrMsg", "")) for d in status.get("Detailed Status", []) or [] if isinstance(d, dict)]
    message = "; ".join(m for m in messages if m) or str(status.get("Description", "") or "Failure")
    missing = any(re.search(r"(doesn'?t|does not|not) exist|not present|not found", m, re.I) for m in messages)
    return Reply(ok=False, data=ctrl.get("Response Data", {}) or {}, missing=missing, message=message)


def _int(value) -> Optional[int]:
    m = re.match(r"\s*(-?[0-9]+)", str(value)) if value is not None else None
    return int(m.group(1)) if m else None


def _props(rows) -> Dict[str, str]:
    """[{"Property": k, "Value": v}, ...] -> {k: v}."""
    out: Dict[str, str] = {}
    for row in rows or []:
        if isinstance(row, dict) and "Property" in row:
            out.setdefault(str(row["Property"]), str(row.get("Value", "")).strip())
    return out


# ---- health -------------------------------------------------------------------------------

OK, ATTENTION, WARNING, FAILED = 0, 1, 2, 3
LEVEL_TEXT = {OK: "OK", ATTENTION: "ATTENTION", WARNING: "WARNING", FAILED: "FAILED"}
LEVEL_HEALTH = {OK: HealthStatus.HEALTHY, ATTENTION: HealthStatus.WARNING,
                WARNING: HealthStatus.WARNING, FAILED: HealthStatus.FAILED}

VD_STATES = {"Optl": "Optimal", "Dgrd": "Degraded", "Pdgd": "Partially degraded", "OfLn": "Offline",
             "Rec": "Recovery", "Cac": "CacheCade", "OfLnMis": "Offline (missing)"}
PD_STATES = {"Onln": "Online", "Offln": "Offline", "UGood": "Unconfigured good",
             "UBad": "Unconfigured bad", "Failed": "Failed", "Rbld": "Rebuild",
             "GHS": "Global hot spare", "DHS": "Dedicated hot spare", "JBOD": "JBOD", "Cpybck": "Copyback"}


@dataclass
class VendorDrive:
    eid_slot: str
    did: Optional[int]
    state: str
    dg: Optional[int] = None
    size: str = ""
    model: str = ""
    interface: str = ""
    media_errors: Optional[int] = None
    other_errors: Optional[int] = None
    predictive_failures: Optional[int] = None
    smart_alert: Optional[bool] = None
    temperature: Optional[str] = None
    link_speed: Optional[str] = None
    level: int = OK
    reasons: List[str] = field(default_factory=list)
    volume: Optional["VendorVolume"] = None

    @property
    def smartctl_type(self) -> Optional[str]:
        """DID == the N of ``smartctl -d megaraid,N``."""
        return f"megaraid,{self.did}" if self.did is not None else None

    @property
    def state_text(self) -> str:
        return PD_STATES.get(self.state, self.state)


@dataclass
class VendorVolume:
    dg_vd: str
    raid_type: str
    state: str
    cache: str = ""
    size: str = ""
    name: str = ""
    os_drive: Optional[str] = None
    active_operations: Optional[str] = None
    members: List[Dict[str, object]] = field(default_factory=list)   # EID:Slt, DID, State
    level: int = OK
    reasons: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def vd(self) -> Optional[int]:
        return _int(self.dg_vd.split("/")[-1]) if "/" in self.dg_vd else _int(self.dg_vd)

    @property
    def state_text(self) -> str:
        return VD_STATES.get(self.state, self.state)

    @property
    def single_disk_raid0(self) -> bool:
        return self.raid_type.upper() == "RAID0" and len(self.members) == 1

    def summary(self) -> str:
        """e.g. "VMDATA1 · RAID0 · 1 disk · no redundancy"."""
        n = len(self.members)
        parts = [self.name or f"VD {self.vd}", self.raid_type, f"{n} disk{'s' if n != 1 else ''}"]
        if self.level >= WARNING:
            parts.append(self.state_text.lower())
        if self.single_disk_raid0:
            parts.append("no redundancy")
        return " · ".join(parts)


@dataclass
class VendorBattery:
    present: bool = False
    kind: str = ""
    state: Optional[str] = None
    replacement_required: Optional[bool] = None
    pack_missing: Optional[bool] = None
    temperature: Optional[str] = None
    charge: Optional[str] = None
    note: str = ""
    level: int = OK
    reasons: List[str] = field(default_factory=list)


@dataclass
class VendorController:
    index: int
    product: str = ""
    fw_version: str = ""
    fw_package: str = ""
    bios_version: str = ""
    driver_name: str = ""
    driver_version: str = ""
    personality: str = ""
    status: Optional[str] = None
    pci_slot: Optional[str] = None
    volumes: List[VendorVolume] = field(default_factory=list)
    drives: List[VendorDrive] = field(default_factory=list)
    battery: VendorBattery = field(default_factory=VendorBattery)
    errors: List[str] = field(default_factory=list)
    level: int = OK
    reasons: List[str] = field(default_factory=list)

    @property
    def health(self) -> HealthStatus:
        return LEVEL_HEALTH[self.level]

    @property
    def summary(self) -> str:
        if self.level == OK:
            return "Optimal: all volumes, drives and battery OK"
        return f"{LEVEL_TEXT[self.level]}: " + "; ".join(self.reasons[:4])

    def details(self) -> List[Tuple[str, str]]:
        rows = [("Product", self.product), ("Controller status", self.status or ""),
                ("FW version", self.fw_version), ("FW package build", self.fw_package),
                ("BIOS version", self.bios_version),
                ("Driver", " ".join(x for x in (self.driver_name, self.driver_version) if x)),
                ("Personality", self.personality)]
        return [(k, v) for k, v in rows if v]


def _yes(value) -> Optional[bool]:
    if value is None:
        return None
    v = str(value).strip().lower()
    if v in ("yes", "true", "1"):
        return True
    if v in ("no", "false", "0"):
        return False
    return None


def evaluate_volume(v: VendorVolume):
    v.level, v.reasons = OK, []
    if v.state == "Optl":
        pass
    elif v.state in ("Dgrd", "Pdgd"):
        v.level = WARNING
        v.reasons.append(f"{v.name or v.dg_vd}: {v.state_text.lower()}, redundancy lost")
    elif v.state.startswith("OfLn"):
        v.level = FAILED
        v.reasons.append(f"{v.name or v.dg_vd}: offline")
    else:
        v.level = WARNING
        v.reasons.append(f"{v.name or v.dg_vd}: state {v.state_text}")
    v.notes = ["no redundancy (RAID0 of a single disk)"] if v.single_disk_raid0 else []


def evaluate_drive(d: VendorDrive):
    d.level, d.reasons = OK, []
    where = f"drive {d.eid_slot}"

    def bump(level, reason):
        d.level = max(d.level, level)
        d.reasons.append(reason)

    if d.state in ("Failed", "Offln", "UBad"):
        bump(FAILED, f"{where}: {d.state_text.lower()}")
    elif d.state == "Rbld":
        bump(ATTENTION, f"{where}: rebuilding")
    if d.predictive_failures:
        bump(WARNING, f"{where}: predictive failure count {d.predictive_failures}")
    if d.smart_alert:
        bump(WARNING, f"{where}: S.M.A.R.T. alert flagged by drive")
    if d.media_errors:
        bump(ATTENTION, f"{where}: {d.media_errors} media error{'s' if d.media_errors != 1 else ''}")
    # Other Error Count: informational only (link resets, aborted commands...)


def evaluate_battery(b: VendorBattery):
    b.level, b.reasons = OK, []
    if not b.present:
        return
    if b.state and b.state.strip().lower() != "optimal":
        b.level = WARNING
        b.reasons.append(f"battery state {b.state}")
    if b.replacement_required:
        b.level = WARNING
        b.reasons.append("battery replacement required")
    if b.pack_missing:
        b.level = WARNING
        b.reasons.append("battery pack missing")


def evaluate_controller(c: VendorController):
    for v in c.volumes:
        evaluate_volume(v)
    for d in c.drives:
        evaluate_drive(d)
    evaluate_battery(c.battery)
    c.level, c.reasons = OK, []
    if c.status and c.status.strip().lower() != "optimal":
        c.level = WARNING
        c.reasons.append(f"controller status {c.status}")
    for part in [*c.volumes, *c.drives, c.battery]:
        if part.level > c.level:
            c.level = part.level
        if part.level > OK:
            c.reasons.extend(part.reasons)
    if c.errors and c.level < ATTENTION:
        c.level = ATTENTION
        c.reasons.extend(c.errors)


# ---- parsers ------------------------------------------------------------------------------

def parse_controller_show(data: dict, c: VendorController):
    c.product = str(data.get("Product Name", "")).strip()
    c.fw_version = str(data.get("FW Version", "")).strip()
    c.fw_package = str(data.get("FW Package Build", "")).strip()
    c.bios_version = str(data.get("BIOS Version", "")).strip()
    c.driver_name = str(data.get("Driver Name", "")).strip()
    c.driver_version = str(data.get("Driver Version", "")).strip()
    c.personality = str(data.get("Current Personality", "")).strip()
    bus, dev, fn = data.get("Bus Number"), data.get("Device Number"), data.get("Function Number")
    if all(isinstance(x, int) for x in (bus, dev, fn)):
        domain = data.get("Domain ID") or 0
        slot = f"{bus:02x}:{dev:02x}.{fn:x}"
        c.pci_slot = slot if not domain else f"{int(domain):04x}:{slot}"
    if not c.volumes:
        c.volumes = [_volume_from_row(r) for r in data.get("VD LIST", []) or [] if isinstance(r, dict)]
    if not c.drives:
        c.drives = [_drive_from_row(r) for r in data.get("PD LIST", []) or [] if isinstance(r, dict)]
    bbu = data.get("BBU_Info")
    if bbu and not c.battery.present and isinstance(bbu, list) and isinstance(bbu[0], dict) and "State" in bbu[0]:
        c.battery = VendorBattery(present=True, kind=str(bbu[0].get("Model", "BBU")),
                                  state=str(bbu[0].get("State", "")).strip() or None,
                                  temperature=str(bbu[0].get("Temp", "")).strip() or None)


def parse_controller_show_all(data: dict, c: VendorController):
    status = data.get("Status", {}) or {}
    if status.get("Controller Status"):
        c.status = str(status["Controller Status"]).strip()
    basics = data.get("Basics", {}) or {}
    version = data.get("Version", {}) or {}
    c.product = c.product or str(basics.get("Model", "")).strip()
    c.fw_version = c.fw_version or str(version.get("Firmware Version", "")).strip()
    c.fw_package = c.fw_package or str(version.get("Firmware Package Build", "")).strip()
    c.bios_version = c.bios_version or str(version.get("Bios Version", "")).strip()
    c.driver_name = c.driver_name or str(version.get("Driver Name", "")).strip()
    c.driver_version = c.driver_version or str(version.get("Driver Version", "")).strip()
    c.personality = c.personality or str(status.get("Current Personality", "")).strip()


def _volume_from_row(row: dict) -> VendorVolume:
    return VendorVolume(
        dg_vd=str(row.get("DG/VD", "")), raid_type=str(row.get("TYPE", "")).strip(),
        state=str(row.get("State", "")).strip(), cache=str(row.get("Cache", "")).strip(),
        size=str(row.get("Size", "")).strip(), name=str(row.get("Name", "")).strip(),
    )


def _drive_from_row(row: dict) -> VendorDrive:
    return VendorDrive(
        eid_slot=str(row.get("EID:Slt", "")).strip(), did=_int(row.get("DID")),
        state=str(row.get("State", "")).strip(), dg=_int(row.get("DG")),
        size=str(row.get("Size", "")).strip(), model=str(row.get("Model", "")).strip(),
        interface=str(row.get("Intf", "")).strip(),
    )


def parse_volumes(data: dict) -> List[VendorVolume]:
    """``/cN/vall show all J``: "/cN/vX" rows, "PDs for VD X" and "VDX Properties"."""
    volumes: List[VendorVolume] = []
    for key, rows in data.items():
        m = re.fullmatch(r"/c[0-9]+/v([0-9]+)", key)
        if not m or not rows:
            continue
        vd = m.group(1)
        vol = _volume_from_row(rows[0])
        members = data.get(f"PDs for VD {vd}", []) or []
        vol.members = [{"EID:Slt": str(p.get("EID:Slt", "")).strip(), "DID": _int(p.get("DID")),
                        "State": str(p.get("State", "")).strip()} for p in members if isinstance(p, dict)]
        props = data.get(f"VD{vd} Properties", {}) or {}
        os_drive = str(props.get("OS Drive Name", "") or "").strip()
        vol.os_drive = os_drive if os_drive.startswith("/dev/") else None
        ops = str(props.get("Active Operations", "") or "").strip()
        vol.active_operations = None if ops.lower() in ("", "none", "n/a") else ops
        volumes.append(vol)
    return volumes


def parse_drives(data: dict) -> List[VendorDrive]:
    """``/cN/eall/sall show all J``: "Drive /cN/eE/sS" rows + "... - Detailed Information"."""
    drives: List[VendorDrive] = []
    for key, rows in data.items():
        m = re.fullmatch(r"Drive (/c[0-9]+/e[0-9]+/s[0-9]+)", key)
        if not m or not rows or not isinstance(rows, list):
            continue
        drive = _drive_from_row(rows[0])
        detail = data.get(f"{key} - Detailed Information", {}) or {}
        state = detail.get(f"{key} State", {}) or {}
        attrs = detail.get(f"{key} Device attributes", {}) or {}
        drive.media_errors = _int(state.get("Media Error Count"))
        drive.other_errors = _int(state.get("Other Error Count"))
        drive.predictive_failures = _int(state.get("Predictive Failure Count"))
        drive.smart_alert = _yes(state.get("S.M.A.R.T alert flagged by drive"))
        temp = str(state.get("Drive Temperature", "") or "").strip()
        tm = re.match(r"(-?[0-9]+)\s*C", temp)
        drive.temperature = f"{tm.group(1)} °C" if tm else (temp or None)
        drive.link_speed = str(attrs.get("Link Speed", "") or "").strip() or None
        drives.append(drive)
    return drives


def parse_battery(reply: Reply) -> VendorBattery:
    if not reply.ok:
        if reply.missing:
            return VendorBattery(present=False, note="No battery (BBU) on this controller")
        return VendorBattery(present=False, note=f"Battery not read: {reply.message}")
    info = _props(reply.data.get("BBU_Info"))
    fw = _props(reply.data.get("BBU_Firmware_Status"))
    cap = _props(reply.data.get("BBU_Capacity_Info")) or _props(reply.data.get("GasGaugeStatus"))
    return VendorBattery(
        present=True, kind=info.get("Type", "BBU"), state=info.get("Battery State") or None,
        replacement_required=_yes(fw.get("Replacement required")),
        pack_missing=_yes(fw.get("Battery Pack Missing")),
        temperature=(info.get("Temperature") or "").replace(" C", " °C") or None,
        charge=cap.get("Relative State of Charge") or None,
    )


def link_volumes_and_drives(c: VendorController):
    by_did = {d.did: d for d in c.drives if d.did is not None}
    for v in c.volumes:
        if not v.members:
            dg = _int(v.dg_vd.split("/")[0])
            v.members = [{"EID:Slt": d.eid_slot, "DID": d.did, "State": d.state}
                         for d in c.drives if dg is not None and d.dg == dg]
        for m in v.members:
            drive = by_did.get(m.get("DID"))
            if drive:
                drive.volume = v


# ---- collection ---------------------------------------------------------------------------

def read_controller(host: ToolHost, status: ToolStatus, index: int) -> VendorController:
    """Runs the whitelisted reads for /c<index> and evaluates health."""
    c = VendorController(index=int(validate_controller(index)))

    def read(kind: str) -> Reply:
        try:
            _, out, err = host.run(status.binary, controller_args(index, kind))
        except CommandNotAllowed:
            raise
        except Exception as e:
            return Reply(ok=False, message=str(e))
        reply = parse_reply(out)
        if not reply.ok and not reply.message:
            reply.message = (err or "").strip()
        return reply

    show = read("show")
    if not show.ok:
        c.errors.append(f"/c{index} show failed: {show.message}")
    else:
        parse_controller_show(show.data, c)
    show_all = read("show_all")
    if show_all.ok:
        parse_controller_show_all(show_all.data, c)
    volumes = read("volumes")
    if volumes.ok:
        c.volumes = parse_volumes(volumes.data) or c.volumes
    elif not volumes.missing:
        c.errors.append(f"volumes not read: {volumes.message}")
    drives = read("drives")
    if drives.ok:
        c.drives = parse_drives(drives.data) or c.drives
    elif not drives.missing:
        c.errors.append(f"drives not read: {drives.message}")
    battery = parse_battery(read("battery"))
    if battery.present or not c.battery.present:
        c.battery = battery
    link_volumes_and_drives(c)
    evaluate_controller(c)
    return c


def controller_count(host: ToolHost, status: ToolStatus) -> Optional[int]:
    _, out, _ = host.run(status.binary, ["show", "ctrlcount", "J"])
    reply = parse_reply(out)
    count = _int(reply.data.get("Controller Count")) if reply.ok else None
    return min(count, 16) if count is not None else None


@dataclass
class ControllerVendorState:
    """What the Server panel shows for one controller."""
    pci_slot: str
    mode: str = MODE_STANDARD
    tool: Optional[ToolStatus] = None       # None = not checked yet (no refresh in this session)
    report: Optional[VendorController] = None
    error: Optional[str] = None


def supported(controller) -> bool:
    return getattr(controller, "driver", None) in SUPPORTED_DRIVERS


def read_host(host: ToolHost, controllers: Sequence, modes: Dict[str, str],
              server_id: Optional[str]) -> Dict[str, ControllerVendorState]:
    """Detection for every MegaRAID controller and, for those set to "Vendor tool", the
    read-only reads. Called only from a user connect / refresh."""
    targets = [c for c in controllers if supported(c)]
    if not targets:
        return {}
    status = detect(host)
    states: Dict[str, ControllerVendorState] = {}
    index_by_slot: Optional[Dict[str, int]] = None
    for ctrl in targets:
        st = ControllerVendorState(pci_slot=ctrl.pci_slot, tool=status,
                                   mode=modes.get(mode_key(server_id, ctrl.pci_slot), MODE_STANDARD))
        states[ctrl.pci_slot] = st
        if st.mode != MODE_VENDOR or not status.usable:
            continue
        try:
            if index_by_slot is None:
                index_by_slot = map_controllers(host, status, targets)
            index = index_by_slot.get(ctrl.pci_slot)
            if index is None:
                st.error = f"{status.name} does not list a controller at PCI {ctrl.pci_slot}"
                continue
            st.report = read_controller(host, status, index)
        except CommandNotAllowed:
            raise
        except Exception as e:
            st.error = f"{status.name} read failed: {e}"
    return states


def map_controllers(host: ToolHost, status: ToolStatus, targets: Sequence) -> Dict[str, int]:
    """PCI slot -> /cN. One controller on both sides: /c0. Otherwise matched by PCI address."""
    count = controller_count(host, status)
    if count is None:
        raise RuntimeError("could not read the controller count (permissions?)")
    if count == 1 and len(targets) == 1:
        return {targets[0].pci_slot: 0}
    out: Dict[str, int] = {}
    for i in range(count):
        _, text, _ = host.run(status.binary, controller_args(i, "show"))
        reply = parse_reply(text)
        if reply.ok:
            probe = VendorController(index=i)
            parse_controller_show(reply.data, probe)
            if probe.pci_slot:
                out[probe.pci_slot] = i
    return out


# ---- sidebar / disk annotations -----------------------------------------------------------

def annotate_disks(states: Dict[str, ControllerVendorState], disks: List[DiskInfo]):
    """Volume name/type on each virtual disk (by OS drive name) and on each physical disk (by
    DID == megaraid,N). Only for controllers in Vendor tool mode with a report."""
    for disk in disks:
        disk.raid_volume = None
        disk.raid_detail = None
        disk.raid_health = None
    reports = [s.report for s in states.values() if s.mode == MODE_VENDOR and s.report]
    for report in reports:
        by_os = {v.os_drive: v for v in report.volumes if v.os_drive}
        by_type = {d.smartctl_type: d for d in report.drives if d.smartctl_type}
        for disk in disks:
            if disk.is_virtual and disk.device_path in by_os:
                v = by_os[disk.device_path]
                disk.raid_volume = v.summary()
                members = ", ".join(f"{m['EID:Slt']} (DID {m['DID']}, {PD_STATES.get(str(m['State']), m['State'])})"
                                    for m in v.members)
                disk.raid_detail = (f"Volume {v.dg_vd} {v.name} · {v.raid_type} · {v.state_text} · "
                                    f"cache {v.cache} · {v.size} · drives: {members or 'none'}")
                if v.active_operations:
                    disk.raid_detail += f" · active: {v.active_operations}"
                disk.raid_health = LEVEL_HEALTH[v.level]
            elif disk.smart_dev_type and disk.smart_dev_type.split("+")[-1] in by_type and \
                    disk.device_path.startswith("/dev/bus/"):
                d = by_type[disk.smart_dev_type.split("+")[-1]]
                vol = d.volume
                disk.raid_volume = (vol.name or vol.dg_vd) if vol else d.state_text
                if d.level > OK or (d.state and d.state != "Onln"):
                    disk.raid_volume += f" · {d.state_text}"
                counters = (f"media errors {d.media_errors}, other errors {d.other_errors}, "
                            f"predictive failures {d.predictive_failures}")
                disk.raid_detail = (f"Controller: {d.eid_slot} (DID {d.did}) · {d.state_text}"
                                    + (f" · volume {vol.name or vol.dg_vd} ({vol.raid_type})" if vol else "")
                                    + f" · {counters}")
                disk.raid_health = LEVEL_HEALTH[d.level]


def load_modes(settings_get: Callable[[str, object], object]) -> Dict[str, str]:
    raw = settings_get(SETTINGS_KEY, {}) or {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items() if v in (MODE_STANDARD, MODE_VENDOR)}


# ---- help text when the tool is missing ---------------------------------------------------

MISSING_TOOL_HELP = (
    "<b>Vendor tool not installed</b> (perccli / storcli). With it SmartLinux can show what "
    "smartctl cannot: the RAID state of each volume (Optimal / Degraded / Rebuild), which "
    "physical disks build each volume, the controller's own error counters and the battery.<br><br>"
    "<b>Where to get it:</b> Dell: support.dell.com, search for PERCCLI under Drivers &amp; "
    "Downloads, category SAS RAID. Broadcom: StorCLI from Broadcom's support site.<br>"
    "<b>Install it by hand</b> on the server: <code>sudo dpkg -i perccli_*.deb</code> or "
    "<code>sudo rpm -ivh perccli-*.rpm</code> (it goes to /opt/MegaRAID/...), then click Refresh.<br>"
    "<b>SmartLinux never installs it</b> and only runs read-only commands."
)
