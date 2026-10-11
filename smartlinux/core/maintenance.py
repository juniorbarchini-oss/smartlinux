"""Maintenance cards of the controller view, built ONLY from vendor tool replies that "Scan
controller" already reads (VendorController.raw: /cN show all, /cN/bbu show all, /cN/vall show
all). No new command, no I/O.

Each card has one status line (plain language) and optional details: (label, value, technical
name) rows, the technical name being shown as a tooltip.
"""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .models import HealthStatus

Row = Tuple[str, str, str]   # (label, value, technical name)


@dataclass
class Card:
    key: str
    title: str
    status: HealthStatus
    line: str
    details: List[Row] = field(default_factory=list)


def _props(rows) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for row in rows or []:
        if isinstance(row, dict) and "Property" in row:
            out.setdefault(str(row["Property"]), str(row.get("Value", "")).strip())
    return out


def _int(value) -> Optional[int]:
    m = re.match(r"\s*(-?[0-9]+)", str(value)) if value is not None else None
    return int(m.group(1)) if m else None


def _blank(value) -> bool:
    return value is None or str(value).strip().upper() in ("", "NA", "N/A", "NONE", "-")


def humanize_period(value: Optional[str]) -> Optional[str]:
    """"168 hrs" -> "every 7 days", "2160 hrs" -> "every 90 days", "12 hrs" -> "every 12 hours"."""
    hours = _int(value)
    if hours is None or hours <= 0:
        return None
    if hours % 24 == 0:
        days = hours // 24
        return f"every {days} day{'s' if days != 1 else ''}"
    return f"every {hours} hour{'s' if hours != 1 else ''}"


# ---- Schedule -----------------------------------------------------------------------------

def schedule_card(raw: Dict[str, dict]) -> Card:
    tasks = (raw.get("show_all") or {}).get("Scheduled Tasks") or {}
    if not tasks:
        return Card("schedule", "Schedule", HealthStatus.UNKNOWN, "The controller did not report a schedule.")
    patrol_every = humanize_period(tasks.get("Patrol Read Reoccurrence"))
    patrol_next = tasks.get("Next Patrol Read launch")
    cc_every = humanize_period(tasks.get("Consistency Check Reoccurrence"))
    cc_next = tasks.get("Consistency Check launch") or tasks.get("Next Consistency check launch")
    learn_every = humanize_period(tasks.get("Battery learn Reoccurrence"))

    parts = []
    if not _blank(patrol_next):
        parts.append(f"Next patrol read {patrol_next}" + (f" ({patrol_every})" if patrol_every else ""))
    elif patrol_every:
        parts.append(f"Patrol read {patrol_every}")
    if _blank(cc_next):
        parts.append("No consistency check is scheduled")       # neutral information, not an alarm
    else:
        parts.append(f"Next consistency check {cc_next}")
    details: List[Row] = [
        ("Patrol read", patrol_every or str(tasks.get("Patrol Read Reoccurrence", "—")), "Patrol Read Reoccurrence"),
        ("Next patrol read", "—" if _blank(patrol_next) else str(patrol_next), "Next Patrol Read launch"),
        ("Consistency check", cc_every or str(tasks.get("Consistency Check Reoccurrence", "—")),
         "Consistency Check Reoccurrence"),
        ("Next consistency check", "Not scheduled" if _blank(cc_next) else str(cc_next), "Next Consistency check launch"),
        ("Battery learn cycle", learn_every or str(tasks.get("Battery learn Reoccurrence", "—")), "Battery learn Reoccurrence"),
    ]
    return Card("schedule", "Schedule", HealthStatus.HEALTHY, " · ".join(parts) or "No tasks scheduled", details)


# ---- Battery ------------------------------------------------------------------------------

def _yes(value) -> Optional[bool]:
    v = str(value or "").strip().lower()
    return True if v == "yes" else (False if v == "no" else None)


def battery_card(raw: Dict[str, dict], battery=None) -> Card:
    """``battery``: the VendorBattery already evaluated by vendor_tools (presence, health)."""
    data = raw.get("battery")
    if not data:
        note = getattr(battery, "note", "") or "No battery (BBU) reported by the controller."
        return Card("battery", "Battery", HealthStatus.UNKNOWN, note)
    info = _props(data.get("BBU_Info"))
    cap = _props(data.get("BBU_Capacity_Info"))
    fw = _props(data.get("BBU_Firmware_Status"))
    design = _props(data.get("BBU_Design_Info"))

    state = info.get("Battery State") or "Unknown"
    charge = cap.get("Relative State of Charge")
    time_to_empty = cap.get("Average time to empty")
    replacement = _yes(fw.get("Replacement required"))
    learn_active = _yes(fw.get("Learn Cycle Active"))
    learn_status = fw.get("Learn Cycle Status")

    status = HealthStatus.HEALTHY
    if state.lower() != "optimal" or replacement or _yes(fw.get("Battery Pack Missing")):
        status = HealthStatus.WARNING
    parts = [state]
    if charge:
        parts.append(charge.replace("%", " %"))
    if time_to_empty and re.match(r"\s*[0-9]", time_to_empty):
        parts.append(f"protects the cache for ~{time_to_empty.strip()}")
    if replacement:
        parts.append("replacement required")

    learn = learn_status or "—"
    if learn_active is not None:
        learn += " (running now)" if learn_active else " (not running)"
    details: List[Row] = [
        ("State", state, "Battery State"),
        ("Charge", charge or "—", "Relative State of Charge"),
        ("Capacity", " / ".join(x for x in (cap.get("Remaining Capacity"), cap.get("Full Charge Capacity")) if x) or "—",
         "Remaining Capacity / Full Charge Capacity"),
        ("Design capacity", design.get("Design Capacity") or "—", "Design Capacity"),
        ("Cache protection", time_to_empty or "—", "Average time to empty"),
        ("Charge cycles", cap.get("Cycle Count") or "—", "Cycle Count"),
        ("Learn cycle", learn, "Learn Cycle Status / Learn Cycle Active"),
        ("Replacement required", "Yes" if replacement else ("No" if replacement is False else "—"), "Replacement required"),
        ("Temperature", (info.get("Temperature") or "—").replace(" C", " °C"), "Temperature"),
    ]
    return Card("battery", "Battery", status, " · ".join(parts), details)


# ---- Controller ---------------------------------------------------------------------------

def controller_card(raw: Dict[str, dict]) -> Card:
    st = (raw.get("show_all") or {}).get("Status") or {}
    if not st:
        return Card("controller", "Controller", HealthStatus.UNKNOWN, "The controller did not report its status.")
    ctrl_status = str(st.get("Controller Status", "")).strip() or "Unknown"
    correctable = _int(st.get("Memory Correctable Errors"))
    uncorrectable = _int(st.get("Memory Uncorrectable Errors"))
    ecc = _int(st.get("ECC Bucket Count"))

    status = HealthStatus.HEALTHY
    problems = []
    if uncorrectable:
        problems.append(f"{uncorrectable} uncorrectable memory error{'s' if uncorrectable != 1 else ''}")
    if ecc:
        problems.append(f"ECC bucket count {ecc}")
    if problems or ctrl_status.lower() != "optimal":
        status = HealthStatus.WARNING
    parts = [ctrl_status]
    if problems:
        parts += problems
    elif correctable:
        parts.append(f"{correctable} corrected memory error{'s' if correctable != 1 else ''}")
    else:
        parts.append("no memory errors")
    details: List[Row] = [
        ("Controller status", ctrl_status, "Controller Status"),
        ("Corrected memory errors", "—" if correctable is None else str(correctable), "Memory Correctable Errors"),
        ("Uncorrectable memory errors", "—" if uncorrectable is None else str(uncorrectable), "Memory Uncorrectable Errors"),
        ("ECC errors", "—" if ecc is None else str(ecc), "ECC Bucket Count"),
    ]
    return Card("controller", "Controller", status, " · ".join(parts), details)


# ---- Operations ---------------------------------------------------------------------------

_PROGRESS_RE = re.compile(r"([0-9]{1,3})\s*%")
_LEFT_RE = re.compile(r"(?:time\s+left|remaining|estimated(?:\s+time)?)\s*[:=]?\s*([0-9][^,;)]*)", re.I)


def parse_operation(text: str) -> Dict[str, Optional[str]]:
    """"Rebuild (Progress 40%, Estimated Time Left 2 Hours 10 Minutes)" -> name, progress, left.
    Progress / time left only when the vendor tool gives them."""
    name = re.split(r"[(:,0-9]", text, maxsplit=1)[0].strip() or text.strip()
    progress = _PROGRESS_RE.search(text)
    left = _LEFT_RE.search(text)
    return {"name": name, "progress": f"{progress.group(1)} %" if progress else None,
            "left": left.group(1).strip() if left else None}


def operations_card(volumes) -> Card:
    running = [(v, parse_operation(v.active_operations)) for v in volumes if v.active_operations]
    if not running:
        return Card("operations", "Operations", HealthStatus.HEALTHY, "No operation in progress",
                    [(v.name or v.dg_vd, "No operation", "Active Operations") for v in volumes])
    status = HealthStatus.HEALTHY
    parts, details = [], []
    for v, op in running:
        if "rebuild" in op["name"].lower():
            status = HealthStatus.WARNING        # redundancy is being restored
        text = f"{op['name']} on {v.name or v.dg_vd}"
        if op["progress"]:
            text += f": {op['progress']}"
        if op["left"]:
            text += f" · about {op['left']} left"
        parts.append(text)
        details.append((v.name or v.dg_vd, v.active_operations, "Active Operations"))
    return Card("operations", "Operations", status, "; ".join(parts), details)


def build_cards(report) -> List[Card]:
    """The four Maintenance cards for one VendorController (None -> no cards)."""
    if report is None:
        return []
    raw = getattr(report, "raw", {}) or {}
    return [schedule_card(raw), battery_card(raw, report.battery), controller_card(raw),
            operations_card(report.volumes)]
