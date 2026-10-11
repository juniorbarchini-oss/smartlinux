"""Read-only identity of a host (server model, CPU, RAM, OS, Proxmox) and of its storage
controllers, so the user can see that SmartLinux recognised the machine and its RAID card.

Everything is optional: each value comes from one small read-only source and simply stays
empty when that source is missing (BusyBox boxes, no lspci, no root). The same shell
snippet runs locally and over SSH (paramiko or plink) in one round trip; it sticks to
BusyBox-ash syntax (no bashisms, ``${x##*/}`` instead of basename, ``command -v``).

Only reads: /sys, /proc, /etc/os-release, lscpu, lspci, modinfo, pveversion and - when
already root - ``dmidecode -t system``. Never touches the controller (no storcli/perccli).
"""
import re
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from . import raid
from .models import DiskInfo

UNIVERSAL_PATH = (
    "export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH; "
)

_SCRIPT_LINES = [
    "echo @@@dmi",
    "for f in sys_vendor product_name product_version bios_vendor bios_version bios_date board_name; "
    "do echo \"$f=$(cat /sys/class/dmi/id/$f 2>/dev/null)\"; done",
    "echo @@@hostname", "uname -n 2>/dev/null",
    "echo @@@kernel", "uname -r 2>/dev/null",
    "echo @@@lscpu", "lscpu 2>/dev/null",
    "echo @@@cpuinfo", "grep -E '^(processor|model name|physical id)' /proc/cpuinfo 2>/dev/null",
    "echo @@@meminfo", "grep -E '^MemTotal' /proc/meminfo 2>/dev/null",
    "echo @@@os", "cat /etc/os-release 2>/dev/null",
    "echo @@@uptime", "cat /proc/uptime 2>/dev/null",
    "echo @@@pve", "pveversion 2>/dev/null",
    "echo @@@lspci", "lspci -vmm -nn -k 2>/dev/null || lspci -vmm -nn 2>/dev/null",
    "echo @@@scsi_host",
    "for h in /sys/class/scsi_host/host*; do [ -e \"$h\" ] || continue; "
    "echo \"${h##*/}: proc_name=$(cat $h/proc_name 2>/dev/null) "
    "fw=$(cat $h/fw_version 2>/dev/null || cat $h/version_fw 2>/dev/null) "
    "drv=$(cat $h/driver_version 2>/dev/null) pci=$(readlink -f $h 2>/dev/null)\"; done",
    "echo @@@modinfo",
    "for m in $(cat /sys/class/scsi_host/host*/proc_name 2>/dev/null | sort -u); do "
    "[ \"$m\" = \"(null)\" ] && continue; echo \"module=$m\"; "
    "modinfo $m 2>/dev/null | grep -E '^(filename|description|version):'; done",
    "echo @@@block",
    "for b in /sys/block/*; do [ -e \"$b\" ] || continue; echo \"${b##*/}=$(readlink -f $b 2>/dev/null)\"; done",
    "echo @@@dmidecode",
    "[ \"$(id -u 2>/dev/null)\" = 0 ] && dmidecode -t system 2>/dev/null",
    "echo @@@end",
]
SYSTEM_INFO_SCRIPT = "; ".join(_SCRIPT_LINES)

# lspci class codes kept as storage controllers
_STORAGE_CLASSES = {
    "0100": "SCSI", "0104": "RAID", "0106": "SATA", "0107": "SAS", "0108": "NVMe",
}
_CLASS_NAMES = (
    ("raid", "RAID"), ("serial attached scsi", "SAS"), ("sata", "SATA"),
    ("non-volatile memory", "NVMe"), ("scsi storage", "SCSI"),
)

_PLACEHOLDERS = {"", "not specified", "to be filled by o.e.m.", "default string", "none",
                 "system serial number", "0", "n/a", "unknown"}

_PCI_RE = re.compile(r"(?:([0-9a-f]{4}):)?([0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f])", re.I)

# driver -> words that identify its controllers in lspci text (used only to confirm a
# driver that the kernel reports as loaded, never to invent one)
_DRIVER_HINTS = {
    "megaraid_sas": ("megaraid", "perc"),
    "mpt3sas": ("fusion-mpt", "sas3008", "sas2008", "sas2308", "hba330"),
    "mpt2sas": ("fusion-mpt", "sas2008", "sas2308"),
    "ahci": ("ahci",),
    "hpsa": ("smart array",),
    "aacraid": ("adaptec", "aacraid"),
}


@dataclass
class StorageController:
    pci_slot: str = ""
    kind: str = ""                       # RAID, SAS, SATA, NVMe, SCSI
    vendor: str = ""
    device: str = ""
    subsystem: str = ""
    driver: Optional[str] = None
    driver_version: Optional[str] = None
    driver_description: Optional[str] = None
    firmware: Optional[str] = None       # only when the system reports it
    scsi_hosts: List[int] = field(default_factory=list)
    physical_disks: int = 0
    virtual_disks: int = 0
    counts_known: bool = False

    @property
    def name(self) -> str:
        sub = self.subsystem.strip()
        if sub and not re.fullmatch(r"(device|unknown device)?", sub, re.I):
            return sub
        vendor = _short_vendor(self.vendor)
        return f"{vendor} {self.device}".strip() if vendor and vendor not in self.device else self.device

    @property
    def label(self) -> str:
        """e.g. "PERC H730 Mini (MegaRAID)"."""
        name = self.name
        family = None
        text = f"{self.device} {self.driver or ''}".lower()
        if "megaraid" in text:
            family = "MegaRAID"
        if family and family.lower() not in name.lower():
            return f"{name} ({family})"
        return name or "Storage controller"


@dataclass
class SystemInfo:
    hostname: Optional[str] = None
    vendor: Optional[str] = None
    product: Optional[str] = None
    product_version: Optional[str] = None
    board: Optional[str] = None
    bios_vendor: Optional[str] = None
    bios_version: Optional[str] = None
    bios_date: Optional[str] = None
    service_tag: Optional[str] = None
    cpu_model: Optional[str] = None
    cpu_sockets: Optional[int] = None
    cpu_cores_per_socket: Optional[int] = None
    cpu_threads: Optional[int] = None
    mem_total_bytes: Optional[int] = None
    os_name: Optional[str] = None
    kernel: Optional[str] = None
    uptime_seconds: Optional[float] = None
    uptime_text: Optional[str] = None
    proxmox_version: Optional[str] = None
    controllers: List[StorageController] = field(default_factory=list)
    scsi_hosts: Dict[int, Dict[str, str]] = field(default_factory=dict)
    block_paths: Dict[str, str] = field(default_factory=dict)
    collected_at: Optional[float] = None
    error: Optional[str] = None

    # ---- presentation ---------------------------------------------------------------
    @property
    def model_line(self) -> Optional[str]:
        vendor = _short_vendor(self.vendor or "")
        product = (self.product or "").strip()
        if not product:
            return vendor or None
        if vendor and vendor.lower() not in product.lower():
            return f"{vendor} {product}"
        return product

    @property
    def cpu_line(self) -> Optional[str]:
        if not self.cpu_model:
            return None
        model = clean_cpu_model(self.cpu_model)
        if self.cpu_sockets and self.cpu_sockets > 1:
            return f"{self.cpu_sockets} × {model}"
        return model

    @property
    def ram_line(self) -> Optional[str]:
        if not self.mem_total_bytes:
            return None
        return f"{format_gib(self.mem_total_bytes)} RAM"

    @property
    def os_line(self) -> Optional[str]:
        if self.proxmox_version:
            return f"Proxmox VE {self.proxmox_version}"
        return self.os_name

    @property
    def uptime_line(self) -> Optional[str]:
        if self.uptime_seconds is not None:
            return f"up {format_duration(self.uptime_seconds)}"
        if self.uptime_text:
            return self.uptime_text if self.uptime_text.startswith("up") else f"up {self.uptime_text}"
        return None

    def identity_line(self) -> str:
        """e.g. "Dell PowerEdge R630 · 2 × Intel Xeon E5-2660 v3 · 503 GiB RAM · Proxmox VE
        9.2.21 · kernel 7.0.14-20-pve · up 10 days, 2 h"."""
        parts = [self.model_line, self.cpu_line, self.ram_line, self.os_line,
                 f"kernel {self.kernel}" if self.kernel else None, self.uptime_line]
        return " · ".join(p for p in parts if p)

    def details(self) -> List[Tuple[str, str]]:
        """(label, value) rows for the identity card; only values the system reported."""
        rows: List[Tuple[str, Optional[str]]] = [
            ("Hostname", self.hostname),
            ("Manufacturer", self.vendor),
            ("Model", self.product),
            ("Service tag", self.service_tag),
            ("Board", self.board),
            ("BIOS", " ".join(x for x in (self.bios_version, f"({self.bios_date})" if self.bios_date else None) if x) or None),
            ("CPU", self._cpu_detail()),
            ("Memory", format_gib(self.mem_total_bytes) if self.mem_total_bytes else None),
            ("Operating system", self.os_name),
            ("Proxmox VE", self.proxmox_version),
            ("Kernel", self.kernel),
            ("Uptime", self.uptime_line[3:] if self.uptime_line else None),
        ]
        return [(k, v) for k, v in rows if v]

    def _cpu_detail(self) -> Optional[str]:
        if not self.cpu_model:
            return None
        bits = [clean_cpu_model(self.cpu_model)]
        extra = []
        if self.cpu_sockets:
            extra.append(f"{self.cpu_sockets} socket{'s' if self.cpu_sockets != 1 else ''}")
        if self.cpu_sockets and self.cpu_cores_per_socket:
            extra.append(f"{self.cpu_sockets * self.cpu_cores_per_socket} cores")
        if self.cpu_threads:
            extra.append(f"{self.cpu_threads} threads")
        if extra:
            bits.append(f"({', '.join(extra)})")
        return " ".join(bits)


# ---- formatting helpers ----------------------------------------------------------------

def _short_vendor(vendor: str) -> str:
    v = (vendor or "").strip()
    v = re.sub(r",?\s+(inc\.?|corp\.?|corporation|co\.,?\s*ltd\.?|ltd\.?|gmbh)$", "", v, flags=re.I)
    return v.strip()


def clean_cpu_model(model: str) -> str:
    """"Intel(R) Xeon(R) CPU E5-2660 v3 @ 2.60GHz" -> "Intel Xeon E5-2660 v3"."""
    m = re.sub(r"\((R|TM|tm|r)\)", "", model)
    m = re.sub(r"\s+@\s+[\d.]+\s*[GM]Hz", "", m)
    m = re.sub(r"\bCPU\b", "", m)
    m = re.sub(r"\b\d+-Core Processor\b", "", m)
    return re.sub(r"\s+", " ", m).strip()


def format_gib(n_bytes: int) -> str:
    gib = n_bytes / float(1 << 30)
    if gib >= 10:
        return f"{int(gib)} GiB"
    return f"{gib:.1f} GiB"


def format_duration(seconds: float) -> str:
    total_min = int(seconds // 60)
    days, rem = divmod(total_min, 1440)
    hours, minutes = divmod(rem, 60)
    if days:
        return f"{days} day{'s' if days != 1 else ''}, {hours} h"
    if hours:
        return f"{hours} h {minutes} min"
    return f"{minutes} min"


def _to_int(value: str) -> Optional[int]:
    m = re.match(r"\s*(\d+)", value or "")
    return int(m.group(1)) if m else None


def _clean(value: Optional[str]) -> Optional[str]:
    v = (value or "").strip()
    return None if v.lower() in _PLACEHOLDERS else v


def _strip_ids(text: str) -> str:
    """lspci -nn appends numeric ids: "MegaRAID SAS-3 3108 [Invader] [005d]" -> without [005d]."""
    return re.sub(r"\s*\[[0-9a-f]{4}\]\s*$", "", (text or "").strip(), flags=re.I).strip()


def normalize_pci(addr: str) -> str:
    m = _PCI_RE.search(addr or "")
    if not m:
        return ""
    domain, rest = m.group(1), m.group(2).lower()
    return rest if not domain or domain == "0000" else f"{domain.lower()}:{rest}"


def last_pci_in_path(path: str) -> str:
    """PCI function a sysfs path hangs from (the deepest PCI address in it)."""
    matches = re.findall(r"([0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f])", path or "", re.I)
    return normalize_pci(matches[-1]) if matches else ""


# ---- parsers (pure: fed by the collector or by test fixtures) ---------------------------

def split_sections(text: str) -> Dict[str, str]:
    sections: Dict[str, List[str]] = {}
    current = None
    for line in (text or "").splitlines():
        m = re.fullmatch(r"@@@(\w+)", line.strip())
        if m:
            current = m.group(1)
            sections.setdefault(current, [])
            continue
        if current:
            sections[current].append(line)
    return {k: "\n".join(v) for k, v in sections.items()}


def parse_key_values(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in (text or "").splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def parse_dmidecode_system(text: str) -> Dict[str, str]:
    """Fields of the "System Information" block of ``dmidecode -t system``."""
    out: Dict[str, str] = {}
    in_block = False
    for line in (text or "").splitlines():
        if not line.startswith(("\t", " ")):
            in_block = line.strip() == "System Information"
            continue
        if in_block and ":" in line:
            k, _, v = line.strip().partition(":")
            out[k.strip()] = v.strip()
    return out


def parse_cpu(lscpu_text: str = "", cpuinfo_text: str = "") -> Tuple[Optional[str], Optional[int], Optional[int], Optional[int]]:
    """(model, sockets, cores per socket, threads) from lscpu, else /proc/cpuinfo."""
    model = sockets = cores = threads = None
    for line in (lscpu_text or "").splitlines():
        k, sep, v = line.partition(":")
        if not sep:
            continue
        k, v = k.strip(), v.strip()
        if k == "Model name" and not model:
            model = v
        elif k == "Socket(s)":
            sockets = _to_int(v)
        elif k == "Core(s) per socket":
            cores = _to_int(v)
        elif k == "CPU(s)" and threads is None:
            threads = _to_int(v)
    if not model and cpuinfo_text:
        phys = set()
        procs = 0
        for line in cpuinfo_text.splitlines():
            k, sep, v = line.partition(":")
            k, v = k.strip(), v.strip()
            if k == "processor":
                procs += 1
            elif k == "model name" and not model:
                model = v
            elif k == "physical id":
                phys.add(v)
        threads = threads or (procs or None)
        sockets = sockets or (len(phys) or None)
    return model, sockets, cores, threads


def parse_mem_total(text: str) -> Optional[int]:
    """Bytes from "MemTotal: N kB" (/proc/meminfo) or "Mem: N ..." (free -b)."""
    m = re.search(r"^MemTotal:\s*(\d+)\s*kB", text or "", re.M)
    if m:
        return int(m.group(1)) * 1024
    m = re.search(r"^Mem:\s+(\d+)", text or "", re.M)
    if m:
        return int(m.group(1))
    return None


def parse_os_release(text: str) -> Optional[str]:
    m = re.search(r'^PRETTY_NAME="?([^"\n]*)"?', text or "", re.M)
    return m.group(1).strip() if m and m.group(1).strip() else None


def parse_pveversion(text: str) -> Optional[str]:
    m = re.search(r"pve-manager/(\d[\w.\-]*)", text or "")
    return m.group(1) if m else None


def parse_uptime(text: str) -> Tuple[Optional[float], Optional[str]]:
    """(seconds, None) from /proc/uptime, or (None, "up 1 week, ...") from ``uptime -p``."""
    for line in (text or "").splitlines():
        line = line.strip()
        m = re.fullmatch(r"(\d+(?:\.\d+)?)\s+\d+(?:\.\d+)?", line)
        if m:
            return float(m.group(1)), None
        if line.startswith("up "):
            return None, line
    return None, None


def parse_lspci_vmm(text: str) -> List[Dict[str, str]]:
    """Records of ``lspci -vmm [-nn] [-k]``; a new record starts at each "Slot:" line
    (blank separators are optional)."""
    records: List[Dict[str, str]] = []
    current: Optional[Dict[str, str]] = None
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        k, sep, v = line.partition(":")
        if not sep:
            continue
        k, v = k.strip(), v.strip()
        if k == "Slot":
            current = {"Slot": v}
            records.append(current)
        elif current is not None and k not in current:
            current[k] = v
    return records


def storage_kind(class_text: str) -> Optional[str]:
    m = re.search(r"\[([0-9a-f]{4})\]", class_text or "", re.I)
    if m and m.group(1).lower() in _STORAGE_CLASSES:
        return _STORAGE_CLASSES[m.group(1).lower()]
    low = (class_text or "").lower()
    if m:  # numeric class present and not a storage one
        return None
    for term, kind in _CLASS_NAMES:
        if term in low:
            return kind
    return None


def parse_storage_controllers(lspci_text: str) -> List[StorageController]:
    out = []
    for rec in parse_lspci_vmm(lspci_text):
        kind = storage_kind(rec.get("Class", ""))
        if not kind:
            continue
        out.append(StorageController(
            pci_slot=normalize_pci(rec.get("Slot", "")) or rec.get("Slot", ""),
            kind=kind,
            vendor=_strip_ids(rec.get("Vendor", "")),
            device=_strip_ids(rec.get("Device", "")),
            subsystem=_strip_ids(rec.get("SDevice", "")),
            driver=rec.get("Driver") or None,
        ))
    return out


def parse_scsi_hosts(text: str) -> Dict[int, Dict[str, str]]:
    hosts: Dict[int, Dict[str, str]] = {}
    for line in (text or "").splitlines():
        m = re.match(r"\s*host(\d+):\s*(.*)$", line)
        if not m:
            continue
        fields = dict(re.findall(r"(\w+)=(\S*)", m.group(2)))
        hosts[int(m.group(1))] = fields
    return hosts


def parse_modinfo(text: str) -> Dict[str, Dict[str, str]]:
    """{module: {description, version}} from "module=X" headers + modinfo lines (the module
    name falls back to the .ko filename)."""
    modules: Dict[str, Dict[str, str]] = {}
    current: Optional[str] = None
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("module="):
            current = line.split("=", 1)[1].strip()
            modules.setdefault(current, {})
            continue
        k, sep, v = line.partition(":")
        if not sep:
            continue
        k, v = k.strip(), v.strip()
        if k == "filename":
            m = re.search(r"([\w\-]+)\.ko(?:\.\w+)?$", v)
            if m and current is None:
                current = m.group(1)
                modules.setdefault(current, {})
        if current and k in ("description", "version"):
            modules[current].setdefault(k, v)
    return modules


def parse_block_paths(text: str) -> Dict[str, str]:
    return {k: v for k, v in parse_key_values(text).items() if k and v}


# ---- assembly --------------------------------------------------------------------------

def build_system_info(sections: Dict[str, str]) -> SystemInfo:
    info = SystemInfo()
    steps: List[Callable[[], None]] = []

    def dmi():
        d = parse_key_values(sections.get("dmi", ""))
        info.vendor = _clean(d.get("sys_vendor"))
        info.product = _clean(d.get("product_name"))
        info.product_version = _clean(d.get("product_version"))
        info.board = _clean(d.get("board_name"))
        info.bios_vendor = _clean(d.get("bios_vendor"))
        info.bios_version = _clean(d.get("bios_version"))
        info.bios_date = _clean(d.get("bios_date"))

    def dmidecode():
        d = parse_dmidecode_system(sections.get("dmidecode", ""))
        info.service_tag = _clean(d.get("Serial Number"))
        info.vendor = info.vendor or _clean(d.get("Manufacturer"))
        info.product = info.product or _clean(d.get("Product Name"))

    def basics():
        info.hostname = _clean(sections.get("hostname", "").strip().splitlines()[0]) if sections.get("hostname", "").strip() else None
        info.kernel = _clean(sections.get("kernel", "").strip().splitlines()[0]) if sections.get("kernel", "").strip() else None

    def cpu():
        info.cpu_model, info.cpu_sockets, info.cpu_cores_per_socket, info.cpu_threads = parse_cpu(
            sections.get("lscpu", ""), sections.get("cpuinfo", ""))

    def mem():
        info.mem_total_bytes = parse_mem_total(sections.get("meminfo", ""))

    def os_():
        info.os_name = parse_os_release(sections.get("os", ""))
        info.proxmox_version = parse_pveversion(sections.get("pve", ""))

    def uptime():
        info.uptime_seconds, info.uptime_text = parse_uptime(sections.get("uptime", ""))

    def controllers():
        info.controllers = parse_storage_controllers(sections.get("lspci", ""))
        info.scsi_hosts = parse_scsi_hosts(sections.get("scsi_host", ""))
        info.block_paths = parse_block_paths(sections.get("block", ""))
        attach_drivers(info, parse_modinfo(sections.get("modinfo", "")))

    steps.extend([dmi, dmidecode, basics, cpu, mem, os_, uptime, controllers])
    for step in steps:
        try:
            step()
        except Exception as e:  # one broken source never hides the others
            print(f"system info: {step.__name__} failed: {e}")
    return info


def attach_drivers(info: SystemInfo, modules: Dict[str, Dict[str, str]]):
    """Driver, driver version and SCSI hosts of each controller (only what the system says)."""
    loaded = {h.get("proc_name") for h in info.scsi_hosts.values() if h.get("proc_name") not in (None, "", "(null)")}
    by_slot = {c.pci_slot: c for c in info.controllers}

    for host, fields in info.scsi_hosts.items():
        ctrl = by_slot.get(last_pci_in_path(fields.get("pci", "")))
        if ctrl:
            ctrl.scsi_hosts.append(host)
            if not ctrl.driver and fields.get("proc_name") not in (None, "", "(null)"):
                ctrl.driver = fields["proc_name"]

    for ctrl in info.controllers:
        if not ctrl.driver:
            text = f"{ctrl.device} {ctrl.subsystem}".lower()
            candidates = [d for d, words in _DRIVER_HINTS.items()
                          if d in loaded and any(w in text for w in words)]
            if len(candidates) == 1:
                ctrl.driver = candidates[0]
    for ctrl in info.controllers:
        # no sysfs PCI link: the hosts of a driver belong to its controller when it is the only one
        if ctrl.driver and not ctrl.scsi_hosts and \
                sum(1 for c in info.controllers if c.driver == ctrl.driver) == 1:
            ctrl.scsi_hosts = sorted(h for h, f in info.scsi_hosts.items() if f.get("proc_name") == ctrl.driver)
        mod = modules.get(ctrl.driver or "")
        if mod:
            ctrl.driver_version = mod.get("version")
            ctrl.driver_description = mod.get("description")
        fws = {info.scsi_hosts[h].get("fw") for h in ctrl.scsi_hosts if info.scsi_hosts.get(h, {}).get("fw")}
        if len(fws) == 1:
            ctrl.firmware = fws.pop()


def _bus_host(device_path: str) -> Optional[int]:
    m = re.fullmatch(r"/dev/bus/(\d+)", device_path or "")
    return int(m.group(1)) if m else None


def controller_for_disk(info: SystemInfo, disk: DiskInfo) -> Optional[StorageController]:
    ctrls = info.controllers
    if not ctrls:
        return None
    family = raid.passthrough_family(disk.smart_dev_type)
    if family:
        host = _bus_host(disk.device_path)
        if host is not None:
            for c in ctrls:
                if host in c.scsi_hosts:
                    return c
        driver = {"megaraid": "megaraid_sas", "cciss": "hpsa", "aacraid": "aacraid"}.get(family)
        same = [c for c in ctrls if driver and c.driver == driver] or \
               [c for c in ctrls if family in f"{c.device} {c.subsystem}".lower()]
        return same[0] if len(same) == 1 else None

    name = disk.device_path.split("/")[-1]
    path = info.block_paths.get(name)
    if path:
        slot = last_pci_in_path(path)
        for c in ctrls:
            if c.pci_slot == slot:
                return c
    if disk.is_virtual:
        model = (disk.model or "").strip().lower()
        same = [c for c in ctrls if model and model in f"{c.subsystem} {c.device}".lower()]
        if len(same) == 1:
            return same[0]
        raid_ctrls = [c for c in ctrls if c.kind == "RAID"]
        if len(raid_ctrls) == 1:
            return raid_ctrls[0]
    return None


def assign_controllers(info: Optional[SystemInfo], disks: List[DiskInfo]):
    """Counts physical/virtual disks per controller and labels RAID-attached disks with
    their controller's name (other disks keep no group, so non-RAID hosts look as before)."""
    if info is None:
        return
    for c in info.controllers:
        c.physical_disks = c.virtual_disks = 0
        c.counts_known = bool(info.block_paths)
    for disk in disks:
        ctrl = controller_for_disk(info, disk)
        if not ctrl:
            continue
        ctrl.counts_known = True
        if disk.is_virtual:
            ctrl.virtual_disks += 1
        else:
            ctrl.physical_disks += 1
        if disk.is_virtual or raid.is_passthrough_type(disk.smart_dev_type):
            disk.controller = ctrl.label


# ---- collection ------------------------------------------------------------------------

Runner = Callable[[str], Tuple[int, str, str]]


def collect(run: Runner, sudo_run: Optional[Runner] = None) -> SystemInfo:
    """Runs the read-only snippet through ``run`` (local shell or SSH) and parses it.
    ``sudo_run`` (non-root SSH user with a password) is only used for ``dmidecode -t
    system`` to get the service tag; nothing else is run with privileges."""
    import time
    try:
        code, out, err = run(SYSTEM_INFO_SCRIPT)
    except Exception as e:
        return SystemInfo(error=str(e), collected_at=time.time())
    sections = split_sections(out)
    if not sections:
        return SystemInfo(error=(err or "").strip() or "No system information returned",
                          collected_at=time.time())
    if sudo_run and not sections.get("dmidecode", "").strip():
        try:
            _, dmi_out, _ = sudo_run("dmidecode -t system 2>/dev/null")
            if "System Information" in (dmi_out or ""):
                sections["dmidecode"] = dmi_out
        except Exception:
            pass
    info = build_system_info(sections)
    info.collected_at = time.time()
    return info


def run_local(script: str, timeout: int = 20) -> Tuple[int, str, str]:
    proc = subprocess.run(["sh", "-c", UNIVERSAL_PATH + script], capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def collect_local() -> SystemInfo:
    return collect(run_local)
