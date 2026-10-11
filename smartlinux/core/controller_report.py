"""Controller report: ONE document for a server's RAID controller that says what is wrong, why it
matters, what the evidence is (value, rule, command and time of the reading) and what was NOT
read. Pure data: nothing here reads a disk or runs a command; it only describes what the
controller view already holds.

``build_report`` returns a small document model (headings, tables, findings) that
``to_html`` / ``to_markdown`` render, and ``export_files`` writes as .pdf / .html / .md.
"""
import datetime
import html
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union

from . import controller as ctl
from . import maintenance, trends
from . import vendor_tools as vt
from .models import DiskInfo, HealthStatus

LABELS = {HealthStatus.HEALTHY: "OK", HealthStatus.WARNING: "Attention", HealthStatus.FAILED: "Failure",
          HealthStatus.UNKNOWN: "Not read"}
COLORS = {HealthStatus.HEALTHY: "#15803d", HealthStatus.WARNING: "#b45309", HealthStatus.FAILED: "#b91c1c",
          HealthStatus.UNKNOWN: "#64748b"}
_ORDER = {HealthStatus.FAILED: 0, HealthStatus.WARNING: 1, HealthStatus.UNKNOWN: 2, HealthStatus.HEALTHY: 3}

Cell = Union[str, Tuple[str, HealthStatus]]


# ---- document model ------------------------------------------------------------------------

@dataclass
class Finding:
    level: HealthStatus
    title: str                       # "Disk 32:3 has reallocated sectors"
    what: str                        # the plain fact
    why: str                         # why it matters, in plain language
    evidence: List[str] = field(default_factory=list)   # value, rule
    source: str = ""                 # command(s) + time of the reading
    action: str = ""                 # sober next step


@dataclass
class Heading:
    text: str
    level: int = 2
    new_page: bool = False          # PDF/HTML: start this section on a fresh page


@dataclass
class Para:
    text: str
    muted: bool = False


@dataclass
class Facts:
    rows: List[Tuple[str, str]]


@dataclass
class Table:
    headers: List[str]
    rows: List[List[Cell]]
    widths: Optional[List[int]] = None     # percent, optional


@dataclass
class Verdict:
    status: HealthStatus
    headline: str
    detail: str = ""


Block = Union[Heading, Para, Facts, Table, Finding, Verdict]


@dataclass
class Document:
    title: str
    subtitle: str
    blocks: List[Block] = field(default_factory=list)


# ---- helpers -------------------------------------------------------------------------------

def _when(epoch: Optional[float]) -> str:
    if not epoch:
        return "not read in this session"
    return datetime.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _vendor_drive(report, disk: DiskInfo):
    if not report:
        return None
    family = (disk.smart_dev_type or "").split("+")[-1]
    return next((d for d in report.drives if d.smartctl_type == family), None)


def disk_label(disk: DiskInfo, vd=None) -> str:
    """"32:3 (DID 3) · ST1200MM0099"; falls back to the slot / device name."""
    if vd is not None:
        place = f"{vd.eid_slot} (DID {vd.did})"
    elif disk.slot is not None:
        place = f"slot {disk.slot}"
    else:
        place = disk.name
    model = disk.model if disk.model not in ("Physical Drive", "Unknown Drive") else (vd.model if vd else "")
    return f"{place} · {model}" if model else place


def _smart_command(disk: DiskInfo) -> str:
    if disk.smart_dev_type:
        return f"smartctl -d {disk.smart_dev_type} {disk.device_path}"
    return f"smartctl {disk.device_path}"


def _vendor_command(tool_name: str, index: int, kind: str) -> str:
    return f"{tool_name} /c{index}" + {
        "drives": "/eall/sall show all J", "volumes": "/vall show all J",
        "battery": "/bbu show all J", "controller": " show all J"}[kind]


# Why an attribute matters / how SmartLinux flags it. Keyed by lower-case name fragment.
_ATTR_TEXT = [
    ("reallocated sector", "Sectors the disk could no longer use and replaced with spare ones. A few over many "
     "years can be normal; a number that keeps growing means the surface is wearing out.",
     "flagged when the raw value is above 0"),
    ("grown defect", "Sectors the disk remapped after leaving the factory (the SAS equivalent of reallocated "
     "sectors). Any growth deserves attention.", "flagged when the value is above 0"),
    ("reallocation event", "How many times the disk had to remap sectors.", "flagged when the raw value is above 0"),
    ("pending", "Sectors the disk had trouble reading and has not been able to move to a spare one yet. "
     "They can turn into reallocated sectors or into unreadable data.", "flagged when the raw value is above 0"),
    ("offline_uncorrect", "Sectors that could not be corrected during the disk's own background scan.",
     "flagged when the raw value is above 0"),
    ("offline uncorrect", "Sectors that could not be corrected during the disk's own background scan.",
     "flagged when the raw value is above 0"),
    ("reported_uncorrect", "Read errors the disk could not correct on its own.", "flagged when the value is above 0"),
    ("uncorrected", "Errors the disk could not recover by itself.", "flagged when the value is above 0"),
    ("command_timeout", "Commands the disk did not answer in time, often a sign of a failing disk, cable or "
     "backplane.", "flagged above the SmartLinux attention limit"),
    ("self-test", "The last self-test the disk ran on itself did not pass.", "flagged when the test failed"),
    ("temperature", "The disk runs hotter than the limit it reports.", "flagged above the disk's trip point"),
]


def _attr_text(attr) -> Tuple[str, str]:
    name = attr.name.lower()
    for key, why, rule in _ATTR_TEXT:
        if key in name:
            return why, rule
    return (attr.description or "SmartLinux flagged this reading as outside the normal range."), \
        "flagged by SmartLinux's S.M.A.R.T. rules"


def _count_text(n: int, one: str, many: str) -> str:
    return _plural(n, one, many)


# ---- findings ------------------------------------------------------------------------------

def collect_findings(physical: List[DiskInfo], virtual: List[DiskInfo], report, tool_name: str,
                     last_scan: Optional[float], disk_trends: Dict[str, "trends.DiskTrend"]) -> List[Finding]:
    out: List[Finding] = []
    index = report.index if report else 0
    vendor_when = _when(last_scan)

    # -- the controller's own view of each disk and volume
    if report:
        for vd in report.drives:
            disk = next((d for d in physical if _vendor_drive(report, d) is vd), None)
            name = disk_label(disk, vd) if disk else f"{vd.eid_slot} (DID {vd.did})"
            source = f"{_vendor_command(tool_name, index, 'drives')} · read {vendor_when}"
            if vd.state in ("Failed", "Offln", "UBad"):
                out.append(Finding(
                    HealthStatus.FAILED, f"Disk {name} is {vd.state_text.lower()}",
                    f"The controller reports this disk as {vd.state_text.lower()}.",
                    "The controller no longer uses a failed or offline disk. Volumes that depend on it can be "
                    "degraded or offline.",
                    [f"Drive state: {vd.state_text} ({vd.state})"], source,
                    "Check the volumes it belongs to and plan its replacement."))
            elif vd.state == "Rbld":
                out.append(Finding(
                    HealthStatus.WARNING, f"Disk {name} is rebuilding",
                    "The controller is rebuilding data onto this disk.",
                    "Until the rebuild finishes the volume has no redundancy against another failure.",
                    [f"Drive state: {vd.state_text}"], source, "Let it finish; avoid heavy load meanwhile."))
            if vd.predictive_failures or vd.smart_alert:
                ev = []
                if vd.predictive_failures:
                    ev.append(f"Predictive Failure Count: {vd.predictive_failures}")
                if vd.smart_alert:
                    ev.append("S.M.A.R.T. alert flagged by the drive: Yes")
                out.append(Finding(
                    HealthStatus.WARNING, f"Disk {name} reported a predictive-failure alert",
                    "The disk's own firmware flagged an alert to the controller.",
                    "This is the disk's own warning, not an estimate made by SmartLinux. It means the "
                    "disk considers itself at risk.", ev, source,
                    "Back up its data and plan its replacement."))
            if vd.media_errors:
                out.append(Finding(
                    HealthStatus.WARNING, f"Disk {name} has media errors",
                    f"The controller counted {_count_text(vd.media_errors, 'media error', 'media errors')} "
                    "on this disk.",
                    "A media error means the disk could not read or write some data on its surface.",
                    [f"Media Error Count: {vd.media_errors}",
                     "Rule: any value above 0 is flagged"], source,
                    "Watch whether the count grows between scans."))
        for v in report.volumes:
            name = v.name or v.dg_vd
            source = f"{_vendor_command(tool_name, index, 'volumes')} · read {vendor_when}"
            if v.state in ("Dgrd", "Pdgd"):
                out.append(Finding(
                    HealthStatus.WARNING, f"Volume {name} has lost redundancy",
                    f"{name} ({v.raid_type}) is {v.state_text.lower()}.",
                    "The data is still available, but another disk failure could cause data loss.",
                    [f"Volume state: {v.state_text} ({v.state})"], source,
                    "Replace the failed member and let the controller rebuild."))
            elif v.state.startswith("OfLn"):
                out.append(Finding(
                    HealthStatus.FAILED, f"Volume {name} is offline",
                    f"{name} ({v.raid_type}) is offline.",
                    "The operating system cannot use this volume.",
                    [f"Volume state: {v.state_text} ({v.state})"], source,
                    "Check the disks it is built from."))
            elif v.state != "Optl":
                out.append(Finding(
                    HealthStatus.WARNING, f"Volume {name} is in state {v.state_text}",
                    f"{name} ({v.raid_type}) is not Optimal.", "The controller does not consider it healthy.",
                    [f"Volume state: {v.state_text} ({v.state})"], source, ""))
        b = report.battery
        if b.present and b.level > 0:
            out.append(Finding(
                HealthStatus.WARNING, "The controller battery needs attention",
                "; ".join(b.reasons) or "The battery is not Optimal.",
                "The battery keeps the controller's write cache safe during a power cut. Without it the "
                "controller usually switches to a slower, safer write mode.",
                [f"Battery state: {b.state or 'unknown'}"]
                + (["Replacement required: Yes"] if b.replacement_required else []),
                f"{_vendor_command(tool_name, index, 'battery')} · read {vendor_when}",
                "Plan the battery replacement."))
        if report.status and report.status.strip().lower() != "optimal":
            out.append(Finding(
                HealthStatus.WARNING, f"The controller reports {report.status}",
                f"Controller status: {report.status}.", "The controller itself is not in its normal state.",
                [f"Controller Status: {report.status}"],
                f"{_vendor_command(tool_name, index, 'controller')} · read {vendor_when}", ""))
        for card in maintenance.build_cards(report):
            if card.key == "controller" and card.status == HealthStatus.WARNING and \
                    not (report.status and report.status.strip().lower() != "optimal"):
                out.append(Finding(
                    HealthStatus.WARNING, "The controller reports memory errors", card.line,
                    "Uncorrectable memory errors on a RAID controller can corrupt data passing through its cache.",
                    [f"{label}: {value}" for label, value, _t in card.details],
                    f"{_vendor_command(tool_name, index, 'controller')} · read {vendor_when}",
                    "Contact the server vendor."))

    # -- the S.M.A.R.T. reading of each disk
    for disk in physical:
        vd = _vendor_drive(report, disk)
        name = disk_label(disk, vd)
        if ctl.is_scanned(disk) and disk.attributes and disk.health_status in (HealthStatus.WARNING,
                                                                              HealthStatus.FAILED):
            bad = [a for a in disk.attributes if a.status_type in (HealthStatus.WARNING, HealthStatus.FAILED)]
            ev = [f"SmartLinux verdict for this disk: {disk.health_summary}"]
            whys = []
            for a in bad:
                why, rule = _attr_text(a)
                ident = f" (ID {a.id})" if a.id else ""
                ev.append(f"{a.name}{ident}: raw {a.raw}, value {a.current}, threshold {a.threshold} "
                          f"→ {a.status}; rule: {rule}")
                if why not in whys:
                    whys.append(why)
            out.append(Finding(
                disk.health_status,
                f"Disk {name} " + ("failed its S.M.A.R.T. check" if disk.health_status == HealthStatus.FAILED
                                   else "has S.M.A.R.T. warnings"),
                disk.health_summary + ".",
                " ".join(whys) or "The disk's own health data is outside the normal range.", ev,
                f"{_smart_command(disk)} · read {_when(disk.last_scanned)}",
                "Compare with the next scan: a value that keeps growing is the signal to act on."))
        elif disk.error_message and not disk.attributes and disk.last_scanned is not None:
            out.append(Finding(
                HealthStatus.UNKNOWN, f"Disk {name} could not be read",
                disk.error_message, "Without a reading, nothing can be said about this disk's health.",
                [], f"{_smart_command(disk)} · tried {_when(disk.last_scanned)}",
                "Retry with the ↻ button of its row."))
        trend = disk_trends.get(disk.uid)
        if trend and trend.enough:
            for mt in trend.metrics:
                if mt.metric.key in ("defects", "uncorrected"):
                    grown = [c for c in mt.changes if c.delta is not None and c.delta > 0]
                    if grown:
                        out.append(Finding(
                            HealthStatus.WARNING,
                            f"Disk {name}: {mt.metric.label.lower()} grew between scans",
                            "; ".join(f"{c.text} in {c.days} days" for c in grown) + f" (now {mt.current_text}).",
                            "A counter that keeps growing is more telling than a fixed number.",
                            [f"{mt.metric.label}: {c.text} in the last {c.days} days" for c in mt.changes
                             if c.delta is not None],
                            "SmartLinux's own history of your scans (local file history.jsonl)",
                            "Scan again in a few days to confirm."))
    out.sort(key=lambda f: _ORDER[f.level])
    return out


# ---- the document --------------------------------------------------------------------------

_COMPONENT_RE = re.compile(r"^((?:Disk|Volume) .+?)(?: is | has | reported | failed | could | lost |: | \u00b7 )")


def component_of(title: str) -> str:
    """"Disk 32:2 (DID 2) · MODEL has S.M.A.R.T. warnings" -> "Disk 32:2 (DID 2)"."""
    m = _COMPONENT_RE.match(title)
    if m:
        return m.group(1)
    lowered = title.lower()
    if "battery" in lowered:
        return "Battery"
    if "controller" in lowered:
        return "Controller"
    return "—"


def build_report(label: str, server_name: str, physical: List[DiskInfo], virtual: List[DiskInfo],
                 report=None, storage=None, tool=None, last_scan: Optional[float] = None,
                 disk_trends: Optional[Dict[str, "trends.DiskTrend"]] = None, version: str = "",
                 now: Optional[datetime.datetime] = None) -> Document:
    """Order of a diagnostic report: identification of the equipment, diagnosis (status, findings
    overview, finding detail), then the supporting sections and the scope."""
    disk_trends = disk_trends or {}
    now = now or datetime.datetime.now()
    tool_name = (tool.name if tool and tool.name else "perccli64")
    status = ctl.controller_health(physical, virtual, report)
    scanned = ctl.has_scan_data(physical, report)
    sentence = ctl.status_sentence(physical, virtual, report)
    note = ctl.redundancy_note(report)
    findings = collect_findings(physical, virtual, report, tool_name, last_scan, disk_trends)
    problems = [f for f in findings if f.level in (HealthStatus.FAILED, HealthStatus.WARNING)]
    notes = [f for f in findings if f.level == HealthStatus.UNKNOWN]

    doc = Document("RAID controller report", f"{server_name or 'This computer'} · {label}")
    blocks = doc.blocks

    # 1. Equipment
    blocks.append(Heading("1. Equipment"))
    rows: List[Tuple[str, str]] = [("Server", server_name or "This computer"), ("Controller", label)]
    if report:
        rows += report.details()
    else:
        driver = " ".join(x for x in (getattr(storage, "driver", None), getattr(storage, "driver_version", None)) if x)
        if driver:
            rows.append(("Driver", driver))
        if getattr(storage, "firmware", None):
            rows.append(("Firmware", storage.firmware))
    if getattr(storage, "pci_slot", None):
        rows.append(("PCI slot", storage.pci_slot))
    if report and report.battery.present:
        bt = report.battery
        rows.append(("Battery", ", ".join(x for x in (bt.state, bt.charge) if x) or "present"))
    rows += [("Disks", f"{len(physical)} physical, {len(virtual)} virtual"),
             ("Report generated", now.strftime("%Y-%m-%d %H:%M")),
             ("Last controller scan", _when(last_scan)),
             ("Generated by", f"SmartLinux {version}".strip())]
    blocks.append(Facts(rows))

    # 2. Diagnosis
    blocks.append(Heading("2. Diagnosis"))
    if not scanned:
        verdict = Verdict(HealthStatus.UNKNOWN, "This controller has not been scanned yet", ctl.NOT_SCANNED_TEXT)
    elif problems:
        verdict = Verdict(status, _plural(len(problems), "finding needs attention", "findings need attention"),
                          sentence)
    else:
        verdict = Verdict(status, "No findings: everything that was read is in order",
                          sentence if sentence != ctl.ALL_GOOD else "")
    blocks.append(verdict)
    if note:
        blocks.append(Para(note, muted=True))
    if problems:
        blocks.append(Heading("Findings overview", 3))
        blocks.append(Table(
            ["#", "Severity", "Component", "Finding", "Suggested step"],
            [[str(n), (LABELS[f.level], f.level), component_of(f.title), f.what, f.action or "—"]
             for n, f in enumerate(problems, 1)], [5, 14, 22, 31, 28]))
        blocks.append(Heading("Finding detail", 3))
        blocks.append(Para("Each finding lists the evidence: the value that was read, the rule that flags it and "
                           "the command and time of the reading.", muted=True))
        blocks.extend(problems)
    if notes:
        blocks.append(Heading("Not read", 3))
        blocks.extend(notes)

    # 3. Volumes
    blocks.append(Heading("3. Volumes"))
    if report and report.volumes:
        vrows = []
        for v in report.volumes:
            members = ", ".join(f"{m['EID:Slt']} (DID {m['DID']})" for m in v.members) or "—"
            extra = []
            if ctl.single_drive_volume(v):
                extra.append("single drive, no controller redundancy")
            if v.active_operations:
                extra.append(v.active_operations)
            vrows.append([v.name or v.dg_vd, v.raid_type, (v.state_text, vt.LEVEL_HEALTH[v.level]), v.size,
                          v.os_drive or "—", members, "; ".join(extra) or "—"])
        blocks.append(Table(["Volume", "RAID", "State", "Size", "OS device", "Member disks", "Notes"], vrows,
                            [12, 8, 11, 10, 11, 22, 26]))
    elif virtual:
        blocks.append(Table(["OS device", "Size"], [[d.device_path, d.size_human or "—"] for d in virtual]))
        blocks.append(Para("The RAID state of these volumes was not read.", muted=True))
    else:
        blocks.append(Para("No volumes were listed.", muted=True))

    # 4. Physical disks
    blocks.append(Heading("4. Physical disks"))
    drows = []
    for disk in physical:
        vd = _vendor_drive(report, disk)
        scanned_disk = ctl.is_scanned(disk)
        health = ctl.disk_health(disk)
        if scanned_disk and disk.attributes:
            smart: Cell = (f"{LABELS[health]}", health)
        elif disk.error_message and not disk.attributes:
            smart = ("Could not be read", HealthStatus.UNKNOWN)
        else:
            smart = ("Not read", HealthStatus.UNKNOWN)
        drows.append([
            f"{vd.eid_slot} / DID {vd.did}" if vd else (f"slot {disk.slot}" if disk.slot is not None else disk.name),
            (disk.model if disk.model != "Physical Drive" or not vd else (vd.model or disk.model))
            + "\n" + (disk.serial if disk.serial not in ("N/A", "") else "—"),
            smart,
            disk.formatted_temperature if disk.temperature_c is not None
            else (vd.temperature if vd and vd.temperature else "—"),
            f"{disk.power_on_hours:,}" if disk.power_on_hours is not None else "—",
            ctl.defects_text(disk) if scanned_disk else "—",
            vd.state_text if vd else "—",
            ((vd.volume.name or vd.volume.dg_vd) if vd and vd.volume else (disk.raid_volume or "—")),
        ])
    blocks.append(Table(["Disk", "Model / serial", "SMART", "Temp.", "Hours", "Defects", "State", "Volume"],
                        drows, [16, 23, 13, 8, 9, 8, 9, 14]))

    # 5. Maintenance
    blocks.append(Heading("5. Maintenance"))
    cards = maintenance.build_cards(report)
    if not cards:
        blocks.append(Para("Maintenance data comes from the vendor tool, which was not read.", muted=True))
    for card in cards:
        blocks.append(Heading(card.title, 3))
        blocks.append(Verdict(card.status, card.line))
        if card.details:
            blocks.append(Facts([(label_, value) for label_, value, _tech in card.details]))

    # 6. Trends
    blocks.append(Heading("6. Trends"))
    trows = []
    for disk in physical:
        trend = disk_trends.get(disk.uid)
        if not trend:
            continue
        name = f"slot {disk.slot}" if disk.slot is not None else disk.name
        if not trend.enough:
            trows.append([name, trends.NOT_ENOUGH, "", "", ""])
            continue
        for mt in trend.metrics:
            cells: List[Cell] = [name, mt.metric.label, mt.current_text]
            for c in mt.changes:
                cells.append((c.text, HealthStatus.WARNING) if c.text.startswith(trends.WORSE) else c.text)
            trows.append(cells)
    if trows:
        blocks.append(Para("Change of each measure between your scans (▲ worse, ▼ better, = no change). Only real "
                           "readings are compared: SmartLinux does not predict failures.", muted=True))
        blocks.append(Table(["Disk", "Measure", "Now", "7 days", "30 days"], trows, [16, 28, 16, 20, 20]))
    else:
        blocks.append(Para("No history yet. Scan the controller on different days to see changes.", muted=True))

    # 7. Scope and method
    blocks.append(Heading("7. What was read, and how"))
    read_disks = [d for d in physical if ctl.is_scanned(d) and d.attributes]
    scope = []
    if report:
        scope.append(("RAID state", f"{tool_name}{' ' + tool.version if tool and tool.version else ''}, read-only "
                      f"commands on controller /c{report.index}, read {_when(last_scan)}"))
    else:
        scope.append(("RAID state", "Not read (vendor tool not used or not installed)"))
    scope.append(("S.M.A.R.T.", f"{len(read_disks)} of {len(physical)} physical disks read with smartctl through "
                  "the controller (-d megaraid,N). Virtual drives have no S.M.A.R.T. of their own"))
    unread = [disk_label(d, _vendor_drive(report, d)) for d in physical if d not in read_disks]
    if unread:
        scope.append(("Not read", ", ".join(unread)))
    scope.append(("Changes made", "None. Everything here was only read from the server, the controller and the disks"))
    scope.append(("Rules", "SmartLinux reports readings and applies fixed rules (for example: reallocated or "
                  "pending sectors above 0). It does not predict failures or estimate time to failure"))
    blocks.append(Facts(scope))

    # Annex
    if read_disks:
        blocks.append(Heading("Annex A. S.M.A.R.T. readings per disk", new_page=True))
        for disk in read_disks:
            vd = _vendor_drive(report, disk)
            blocks.append(Heading(disk_label(disk, vd), 3))
            blocks.append(Para(f"{disk.health_summary} · {_smart_command(disk)} · read {_when(disk.last_scanned)}",
                               muted=True))
            blocks.append(Table(
                ["ID", "Attribute", "Value", "Worst", "Threshold", "Raw", "Status"],
                [[str(a.id) if a.id else "—", a.name, str(a.current), str(a.worst), str(a.threshold), str(a.raw),
                  (a.status, a.status_type)] for a in disk.attributes], [6, 34, 10, 10, 10, 18, 12]))
    return doc


# ---- renderers -----------------------------------------------------------------------------
# One visual language: every block is a bordered table, generous padding, shaded label column or
# header row. The status colour appears only as text and as the left edge of a finding.

def _cell(cell: Cell) -> Tuple[str, Optional[HealthStatus]]:
    return (cell, None) if isinstance(cell, str) else (cell[0], cell[1])


_CSS = """
body { font-family: 'DejaVu Sans', 'Liberation Sans', Helvetica, Arial, sans-serif; color: #1e293b; font-size: 10pt; }
h1 { color: #0f172a; font-size: 20pt; margin: 0 0 2pt 0; }
h2 { color: #1d4ed8; font-size: 13pt; margin: 20pt 0 6pt 0; border-bottom: 2px solid #3b82f6; padding-bottom: 3pt; }
h3 { color: #0f172a; font-size: 11pt; margin: 12pt 0 4pt 0; }
.sub { color: #64748b; font-size: 11pt; margin: 0 0 6pt 0; }
.muted { color: #64748b; }
"""
_PAD = "cellpadding='9' cellspacing='0' border='1' width='100%' style='margin:6pt 0 10pt 0;'"
_LABEL_BG = "#f1f5f9"
SPACER = "<p style='font-size:5pt; margin:0;'>&nbsp;</p>"


def _badge(text: str, status: HealthStatus) -> str:
    return f"<b style='color:{COLORS[status]}'>{html.escape(text)}</b>"


def to_html(doc: Document) -> str:
    out = [f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{html.escape(doc.title)}</title>"
           f"<style>{_CSS}</style></head><body>",
           f"<h1>{html.escape(doc.title)}</h1><p class='sub'>{html.escape(doc.subtitle)}</p>"]
    number = 0
    for b in doc.blocks:
        if isinstance(b, Heading):
            style = " style='page-break-before: always;'" if b.new_page else ""
            out.append(f"<h{b.level}{style}>{html.escape(b.text)}</h{b.level}>")
        elif isinstance(b, Para):
            cls = " class='muted'" if b.muted else ""
            out.append(f"<p{cls}>{html.escape(b.text)}</p>")
        elif isinstance(b, Facts):
            out.append(f"<table {_PAD}>" + "".join(
                f"<tr><td width='28%' bgcolor='{_LABEL_BG}' valign='top'>{html.escape(k)}</td>"
                f"<td valign='top'><b>{html.escape(v)}</b></td></tr>" for k, v in b.rows) + "</table>" + SPACER)
        elif isinstance(b, Verdict):
            color = COLORS[b.status]
            out.append(f"<table {_PAD}><tr>"
                       f"<td width='18%' bgcolor='{_LABEL_BG}' valign='top'><b style='font-size:12pt; "
                       f"color:{color}'>{LABELS[b.status]}</b></td>"
                       f"<td valign='top'><b>{html.escape(b.headline)}</b>"
                       + (f"<br>{html.escape(b.detail)}" if b.detail else "") + "</td></tr></table>" + SPACER)
        elif isinstance(b, Table):
            widths = b.widths or [None] * len(b.headers)
            head = "".join((f"<th width='{w}%' " if w else "<th ") + f"bgcolor='{_LABEL_BG}' align='left'>"
                           f"{html.escape(h)}</th>" for h, w in zip(b.headers, widths))
            rows = []
            for row in b.rows:
                cells = []
                for c in row:
                    text, st = _cell(c)
                    body = _badge(text, st) if st else html.escape(text).replace("\n", "<br>")
                    cells.append(f"<td valign='top'>{body}</td>")
                rows.append("<tr>" + "".join(cells) + "</tr>")
            pad = _PAD.replace("cellpadding='9'", "cellpadding='5'") if len(b.headers) >= 7 else _PAD
            out.append(f"<table {pad}><tr>{head}</tr>{''.join(rows)}</table>" + SPACER)
        elif isinstance(b, Finding):
            number += 1
            ev = "<br>".join("• " + html.escape(e) for e in b.evidence) or "—"
            lines = [("What", html.escape(b.what)), ("Why it matters", html.escape(b.why)), ("Evidence", ev)]
            if b.source:
                lines.append(("Source", html.escape(b.source)))
            if b.action:
                lines.append(("Suggested step", html.escape(b.action)))
            out.append(
                f"<table {_PAD}><tr><td colspan='2' bgcolor='{_LABEL_BG}'><b>Finding {number}: "
                f"{html.escape(b.title)}</b> &nbsp; {_badge(LABELS[b.level], b.level)}</td></tr>"
                + "".join(f"<tr><td width='18%' valign='top'>{k}</td><td valign='top'>{v}</td></tr>"
                          for k, v in lines) + "</table>" + SPACER)
    out.append("<p class='muted'>Generated by SmartLinux.</p></body></html>")
    return "".join(out)


def _md(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def to_markdown(doc: Document) -> str:
    out = [f"# {doc.title}", "", f"_{doc.subtitle}_", ""]
    number = 0
    for b in doc.blocks:
        if isinstance(b, Heading):
            out += [f"{'#' * b.level} {b.text}", ""]
        elif isinstance(b, Para):
            out += [f"_{b.text}_" if b.muted else b.text, ""]
        elif isinstance(b, Facts):
            out += ["| | |", "| :--- | :--- |"] + [f"| {_md(k)} | **{_md(v)}** |" for k, v in b.rows] + [""]
        elif isinstance(b, Verdict):
            out += [f"> **{LABELS[b.status]} — {b.headline}**" + (f"  \n> {b.detail}" if b.detail else ""), ""]
        elif isinstance(b, Table):
            out += ["| " + " | ".join(_md(h) for h in b.headers) + " |",
                    "| " + " | ".join(":---" for _ in b.headers) + " |"]
            for row in b.rows:
                out.append("| " + " | ".join(
                    (f"**{_md(t)}**" if s else _md(t)) for t, s in (_cell(c) for c in row)) + " |")
            out.append("")
        elif isinstance(b, Finding):
            number += 1
            out += [f"#### Finding {number}: {b.title} — {LABELS[b.level]}", "",
                    f"- **What:** {b.what}", f"- **Why it matters:** {b.why}"]
            if b.evidence:
                out.append("- **Evidence:**")
                out += [f"  - {e}" for e in b.evidence]
            if b.source:
                out.append(f"- **Source:** {b.source}")
            if b.action:
                out.append(f"- **Suggested step:** {b.action}")
            out.append("")
    out += ["---", "_Generated by SmartLinux._", ""]
    return "\n".join(out)


FORMATS = {"pdf": "PDF", "html": "HTML", "md": "Markdown"}


def write_pdf(html_text: str, path: str):
    from PySide6.QtCore import QMarginsF, QSizeF
    from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter, QTextDocument
    writer = QPdfWriter(path)
    writer.setResolution(96)
    writer.setPageLayout(QPageLayout(QPageSize(QPageSize.A4), QPageLayout.Portrait,
                                     QMarginsF(14, 14, 14, 14), QPageLayout.Millimeter))
    text = QTextDocument()
    rect = writer.pageLayout().paintRectPixels(writer.resolution())
    text.setPageSize(QSizeF(rect.width(), rect.height()))
    text.setHtml(html_text)
    text.print_(writer)


def export_files(doc: Document, output_dir: str, formats: List[str], base_name: str = "") -> Tuple[bool, List[str]]:
    """Writes the report once per requested format. Returns (ok, paths) or (False, [error])."""
    if not formats:
        return False, ["No format selected."]
    os.makedirs(output_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = re.sub(r"[^a-zA-Z0-9_\-]+", "_", base_name or doc.title).strip("_") or "controller"
    paths: List[str] = []
    try:
        for fmt in formats:
            path = os.path.join(output_dir, f"SmartLinux_{safe}_{stamp}.{fmt}")
            if fmt == "md":
                with open(path, "w", encoding="utf-8") as f:
                    f.write(to_markdown(doc))
            elif fmt == "html":
                with open(path, "w", encoding="utf-8") as f:
                    f.write(to_html(doc))
            elif fmt == "pdf":
                write_pdf(to_html(doc), path)
            else:
                return False, [f"Unsupported format: {fmt}"]
            paths.append(path)
    except Exception as e:  # disk full, permissions...
        return False, [str(e)]
    return True, paths
