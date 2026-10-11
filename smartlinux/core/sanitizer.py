"""Sanitizer for the diagnostic bundle: replaces personal / identifying values with fake ones.

Two passes over the whole bundle:

1. ``collect``: find sensitive values in every file (structured JSON keys, "Key: value" lines,
   dmidecode blocks, generic patterns such as MAC / IP / UUID / user names in paths) plus the
   values the caller already knows (hostnames, server names, SSH user, disk serials...).
2. ``apply``: replace EVERY occurrence of every collected value, in every file, with a fake value.
   The mapping is deterministic and consistent inside one bundle: the same serial always gets
   the same fake serial, so correlations between files survive.

Defense in depth: ``verify`` searches the final files for every original value (and the hex
encoding of serials, as in SCSI inquiry data). Any residue raises ``SanitizationError``; the
caller must then not write the bundle.

Kept on purpose (needed for development): model, vendor, firmware, versions, -d types, sizes,
SMART counters. Values shorter than 3 characters and obvious placeholders ("Not Specified",
"0000...") are not treated as identifying. Originals live only in memory; the manifest only
has counts per category.
"""
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

# ---- categories -------------------------------------------------------------------------

SERIAL = "serial"
WWN = "wwn"                # WWN, SAS address, logical_unit_id, NAA ids (hex)
UUID = "uuid"
SERVICE_TAG = "service_tag"
ASSET_TAG = "asset_tag"
MAC = "mac"
IPV4 = "ipv4"
IPV6 = "ipv6"
HOSTNAME = "hostname"
LABEL = "label"            # filesystem labels, RAID volume names
USERNAME = "username"

CATEGORY_TEXT = {
    SERIAL: "serial numbers", WWN: "WWN / SAS addresses / logical unit ids", UUID: "UUIDs",
    SERVICE_TAG: "service tags / SKU numbers", ASSET_TAG: "asset tags", MAC: "MAC addresses",
    IPV4: "IPv4 addresses", IPV6: "IPv6 addresses", HOSTNAME: "host and computer names",
    LABEL: "filesystem labels and RAID volume names", USERNAME: "user names",
}

# Values that identify nothing (placeholders, defaults) are left alone.
_PLACEHOLDERS = {
    "", "n/a", "na", "none", "null", "unknown", "not specified", "not available", "not present",
    "to be filled by o.e.m.", "default string", "system serial number", "chassis serial number",
    "base board serial number", "0123456789", "123456789", "no asset tag", "no asset information",
    "asset-1234567890", "empty", "-", "--", "default", "o.e.m.", "oem", "localhost",
    "localhost.localdomain", "(none)",
}
_SYSTEM_USERS = {"root", "nobody", "daemon", "bin", "sys", "admin", "user", "users", "shared",
                 "media", "public", "guest", "default"}

# Keys (case-insensitive unless noted) whose values are sensitive in JSON output
_JSON_KEYS = {
    "serial_number": SERIAL, "serial": SERIAL, "sn": SERIAL, "serial number": SERIAL,
    "scsi_serial_number": SERIAL, "device serial number": SERIAL, "serialnumber": SERIAL,
    "wwn": WWN, "logical_unit_id": WWN, "sas address": WWN, "sas_address": WWN,
    "scsi naa id": WWN, "naa id": WWN, "eui64": WWN, "nguid": WWN,
    "uuid": UUID, "label": LABEL, "partlabel": LABEL, "fslabel": LABEL,
    "hostname": HOSTNAME, "nodename": HOSTNAME, "asset tag": ASSET_TAG, "service tag": SERVICE_TAG,
}
# perccli/storcli: "Name" (capital N, exact) in VD rows is the volume name
_JSON_EXACT_KEYS = {"Name": LABEL}

# "Key: value" / "Key = value" lines in text output
_LINE_KEYS = [
    (re.compile(r"^\s*(?:Serial Number|Serial number|Serial No\.?|SerialNo|SN|"
                r"Device Serial Number|Unit serial number)\s*[:=]\s*(.+?)\s*$", re.M | re.I), SERIAL),
    (re.compile(r"^\s*(?:LU WWN Device Id|WWN|NAA Id|SCSI NAA Id)\s*[:=]\s*(.+?)\s*$", re.M | re.I), WWN),
    (re.compile(r"^\s*(?:Logical Unit id|SAS address|SAS Address)\s*[:=]\s*(.+?)\s*$", re.M | re.I), WWN),
    (re.compile(r"^\s*(?:UUID)\s*[:=]\s*(.+?)\s*$", re.M | re.I), UUID),
    (re.compile(r"^\s*(?:Asset Tag)\s*[:=]\s*(.+?)\s*$", re.M | re.I), ASSET_TAG),
    (re.compile(r"^\s*(?:Service Tag)\s*[:=]\s*(.+?)\s*$", re.M | re.I), SERVICE_TAG),
    (re.compile(r"^\s*(?:Hostname|Host name|Nodename|Static hostname)\s*[:=]\s*(.+?)\s*$", re.M | re.I), HOSTNAME),
]

_MAC_RE = re.compile(r"(?<![0-9A-Fa-f:-])((?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2})(?![0-9A-Fa-f:-])")
_UUID_RE = re.compile(r"(?<![0-9A-Fa-f-])([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
                      r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})(?![0-9A-Fa-f-])")
_IPV4_RE = re.compile(r"(?<![\w.])((?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])"
                      r"(?:\.(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])){3})(?![\w.])")
_IPV6_RE = re.compile(r"(?<![\w:.])((?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}|"
                      r"(?:[0-9A-Fa-f]{1,4}:){1,6}:(?:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4}){0,5})?|"
                      r"::(?:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4}){0,6}))(?![\w:.])")
_WWN_HEX_RE = re.compile(r"(?<![0-9A-Za-z])((?:0x)?[56][0-9A-Fa-f]{15})(?![0-9A-Za-z])")
_HOME_RE = re.compile(r"/(?:home|Users|var/home)/([A-Za-z0-9._-]+)")
_MEDIA_RE = re.compile(r"/(?:run/media|media)/([A-Za-z0-9._-]+)(?:/([^/\s\"',:]+))?")
_FS_LABEL_RE = re.compile(r"\b(?:PART)?LABEL=\"([^\"]+)\"")
# perccli/storcli "Inquiry Data": hex bytes that embed vendor, product, firmware AND serial
_INQUIRY_RE = re.compile(r"(\"Inquiry Data\"\s*:\s*\")([0-9A-Fa-f ]+)(\")")

_IPV4_KEEP = {"0.0.0.0", "127.0.0.1", "255.255.255.255", "255.255.255.0", "255.255.0.0", "255.0.0.0"}

# dmidecode: which "Serial Number" etc. means what depends on the block
_DMI_SYSTEM = {"serial number": SERVICE_TAG, "sku number": SERVICE_TAG, "uuid": UUID, "asset tag": ASSET_TAG}
_DMI_OTHER = {"serial number": SERIAL, "uuid": UUID, "asset tag": ASSET_TAG}


class SanitizationError(RuntimeError):
    """A sensitive value is still present after sanitizing: the bundle must not be written."""

    def __init__(self, category: str, filename: str):
        self.category = category
        self.filename = filename
        super().__init__(
            f"Export stopped: a value of category '{CATEGORY_TEXT.get(category, category)}' is still "
            f"present in {filename} after sanitizing. Nothing was saved. Please report this "
            f"(without the bundle) so the sanitizer can be fixed."
        )


def _is_placeholder(value: str) -> bool:
    v = value.strip().strip('"').strip()
    low = v.lower()
    if low in _PLACEHOLDERS or len(v) < 3:
        return True
    core = re.sub(r"[\s:._-]", "", low)
    if core.startswith("0x") and len(core) > 2:
        core = core[2:]
    if core and len(set(core)) == 1 and core[0] in "0fx*":   # 0000..., XXXX..., ffff..., 0x0000...
        return True
    if not re.search(r"[A-Za-z0-9]", v):
        return True
    return False


@dataclass
class Sanitizer:
    """Collects, replaces and verifies. One instance per bundle."""
    mapping: Dict[str, Tuple[str, str]] = field(default_factory=dict)  # lower(original) -> (category, fake)
    originals: Dict[str, str] = field(default_factory=dict)            # lower(original) -> original
    counts: Counter = field(default_factory=Counter)                   # replacements per category
    _next: Counter = field(default_factory=Counter)
    _pattern: Optional[re.Pattern] = None

    # ---- registration ------------------------------------------------------------------
    def add(self, category: str, value, min_len: int = 3):
        """Registers a sensitive value (no-op for placeholders / too-short values)."""
        if value is None or isinstance(value, bool):
            return
        value = str(value).strip().strip('"').strip()
        if len(value) < min_len or _is_placeholder(value):
            return
        if category == USERNAME and value.lower() in _SYSTEM_USERS:
            return
        if category == IPV4 and value in _IPV4_KEEP:
            return
        key = value.lower()
        if key in self.mapping:
            return
        self.mapping[key] = (category, self._fake(category, value))
        self.originals[key] = value
        self._pattern = None
        # a host's short name ("pve-r630" of "pve-r630.example.lan") is the same identity
        if category == HOSTNAME and "." in value and not _IPV4_RE.fullmatch(value):
            self.add(HOSTNAME, value.split(".")[0], min_len)

    def _fake(self, category: str, original: str) -> str:
        self._next[category] += 1
        n = self._next[category]
        if category == SERIAL:
            width = max(len(original), 6)
            body = f"{n:0{max(width - 4, 2)}d}"
            return ("FAKE" + body)[:width] if width >= 6 else f"FK{n:04d}"
        if category == WWN:
            prefix = "0x" if original.lower().startswith("0x") else ""
            digits = re.sub(r"[^0-9A-Fa-f]", "", original[len(prefix):])
            if digits and digits.isdigit() and len(digits) == len(original):
                return f"9{n:0{max(len(original) - 1, 1)}d}"           # numeric id (smartctl JSON)
            width = len(digits) or 16
            return prefix + ("fa1e" + f"{n:0{max(width - 4, 1)}x}")[:width]
        if category == UUID:
            return f"00000000-0000-4000-8000-{n:012x}"
        if category == SERVICE_TAG:
            return f"TAG{n:04d}"
        if category == ASSET_TAG:
            return f"ASSET-{n:02d}"
        if category == MAC:
            sep = "-" if "-" in original else ":"
            return sep.join(["02", "00", "00", "00", f"{(n >> 8) & 0xff:02x}", f"{n & 0xff:02x}"])
        if category == IPV4:
            return f"192.0.2.{n % 254 + 1}" if n < 254 else f"198.51.100.{n % 254 + 1}"
        if category == IPV6:
            return f"2001:db8::{n:x}"
        if category == HOSTNAME:
            return f"host-{n:02d}"
        if category == LABEL:
            return f"LABEL-{n:02d}"
        if category == USERNAME:
            return f"user{n:02d}"
        return f"REDACTED-{n:02d}"

    # ---- pass 1: discovery --------------------------------------------------------------
    def collect(self, text: str):
        if not text:
            return
        data = None
        stripped = text.lstrip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                data = json.loads(text)
            except Exception:
                data = None
        if data is not None:
            self._collect_json(data)
        self._collect_dmidecode(text)   # before the generic lines: decides service tag vs serial
        self._collect_lines(text)
        self._collect_patterns(text)

    def _collect_json(self, node, key: Optional[str] = None):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(k, str) and k.lower() == "wwn" and isinstance(v, dict):
                    # smartctl: {"naa": 5, "oui": 3152, "id": 123456789} -> the id identifies the disk
                    self.add(WWN, v.get("id"))
                    continue
                self._collect_json(v, k)
        elif isinstance(node, list):
            for item in node:
                self._collect_json(item, key)
        elif key is not None and isinstance(node, (str, int)) and not isinstance(node, bool):
            category = _JSON_EXACT_KEYS.get(key) or _JSON_KEYS.get(key.lower())
            if category:
                self.add(category, node)

    def _collect_lines(self, text: str):
        for regex, category in _LINE_KEYS:
            for m in regex.finditer(text):
                value = m.group(1)
                if category == WWN and re.fullmatch(r"[0-9A-Fa-f]\s[0-9A-Fa-f]{6}\s[0-9A-Fa-f]{9}", value):
                    self.add(WWN, value)                        # "5 000c50 0abcdef01" (smartctl text)
                    self.add(WWN, value.replace(" ", ""))
                else:
                    self.add(category, value)

    def _collect_dmidecode(self, text: str):
        if "Handle 0x" not in text and "SMBIOS" not in text and "Information\n" not in text:
            return
        block = None
        for line in text.splitlines():
            if line and not line.startswith(("\t", " ")):
                block = line.strip()
                continue
            k, sep, v = line.strip().partition(":")
            if not sep:
                continue
            table = _DMI_SYSTEM if block == "System Information" else _DMI_OTHER
            category = table.get(k.strip().lower())
            if category:
                self.add(category, v)

    def _collect_patterns(self, text: str):
        for m in _MAC_RE.finditer(text):
            if m.group(1).replace(":", "").replace("-", "").strip("0") and \
                    m.group(1).lower() not in ("ff:ff:ff:ff:ff:ff",):
                self.add(MAC, m.group(1))
        for m in _UUID_RE.finditer(text):
            self.add(UUID, m.group(1))
        for m in _IPV4_RE.finditer(text):
            self.add(IPV4, m.group(1))
        for m in _IPV6_RE.finditer(text):
            value = m.group(1)
            if value.count(":") >= 2 and re.search(r"[0-9A-Fa-f]{2,}", value) and value not in ("::", "::1"):
                self.add(IPV6, value)
        for m in _WWN_HEX_RE.finditer(text):
            value = m.group(1)
            # bare 16 digits may be a SMART counter: only 0x-prefixed or hex-looking values
            if value.lower().startswith("0x") or re.search(r"[A-Fa-f]", value):
                self.add(WWN, value)
        for m in _HOME_RE.finditer(text):
            self.add(USERNAME, m.group(1))
        for m in _MEDIA_RE.finditer(text):
            self.add(USERNAME, m.group(1))
            if m.group(2):
                self.add(LABEL, m.group(2))
        for m in _FS_LABEL_RE.finditer(text):
            self.add(LABEL, m.group(1))

    # ---- pass 2: replacement ------------------------------------------------------------
    def _regex(self) -> Optional[re.Pattern]:
        if self._pattern is None and self.mapping:
            alternatives = sorted(self.originals.values(), key=len, reverse=True)
            body = "|".join(re.escape(v) for v in alternatives)
            self._pattern = re.compile(rf"(?<![A-Za-z0-9])(?:{body})(?![A-Za-z0-9])", re.I)
        return self._pattern

    def apply(self, text: str) -> str:
        if not text:
            return text
        text = _INQUIRY_RE.sub(self._inquiry, text)
        regex = self._regex()
        if regex is None:
            return text

        def repl(m):
            category, fake = self.mapping[m.group(0).lower()]
            self.counts[category] += 1
            return fake

        text = regex.sub(repl, text)
        return self._replace_hex_serials(text)

    def _inquiry(self, m) -> str:
        self.counts[SERIAL] += 1
        return f"{m.group(1)}<removed: SCSI inquiry data embeds the serial number>{m.group(3)}"

    def _replace_hex_serials(self, text: str) -> str:
        for key, (category, fake) in self.mapping.items():
            if category not in (SERIAL, SERVICE_TAG, ASSET_TAG):
                continue
            original = self.originals[key]
            for encoded in _hex_forms(original):
                if encoded in text.lower():
                    text = re.sub(re.escape(encoded), " ".join(f"{b:02x}" for b in fake.encode()), text, flags=re.I)
                    self.counts[category] += 1
        return text

    # ---- defense in depth ---------------------------------------------------------------
    def verify(self, files: Dict[str, str]):
        """Raises SanitizationError if any original value (or a serial's hex encoding) remains."""
        for name, text in files.items():
            low = (text or "").lower()
            for key, (category, _fake) in self.mapping.items():
                if len(key) >= 4:
                    found = key in low
                else:
                    found = re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", low) is not None
                if found:
                    raise SanitizationError(category, name)
                if category in (SERIAL, SERVICE_TAG, ASSET_TAG):
                    for encoded in _hex_forms(self.originals[key]):
                        if encoded in low:
                            raise SanitizationError(category, name)

    def summary(self) -> Dict[str, int]:
        """Replacements per category (never the originals)."""
        return {k: v for k, v in sorted(self.counts.items()) if v}


def _hex_forms(value: str) -> List[str]:
    if len(value) < 4:
        return []
    raw = value.encode("utf-8", "ignore")
    return [" ".join(f"{b:02x}" for b in raw), "".join(f"{b:02x}" for b in raw)]


def sanitize_files(files: Dict[str, str], known: Iterable[Tuple[str, object]] = ()) -> Tuple[Dict[str, str], Sanitizer]:
    """Sanitizes a whole bundle {filename: text}. ``known``: (category, value) pairs the caller
    already knows (hostnames, server name, SSH user, disk serials...). Raises SanitizationError
    on any residue."""
    s = Sanitizer()
    for category, value in known:
        s.add(category, value)
    for text in files.values():
        s.collect(text)
    out = {name: s.apply(text) for name, text in files.items()}
    s.verify(out)
    return out, s
