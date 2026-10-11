"""Controller view: one RAID controller as a unit.

Header (model, driver, firmware, battery, global status, vendor tool status), "↻ Scan controller"
with progress / cancel, a Volumes table (vendor tool data) and ONE Physical disks table that
joins smartctl and the vendor tool per disk. Display only: reads happen when the user clicks
Scan controller or a row's "↻" (the main window runs them, in series).
"""
from datetime import datetime
from html import escape
from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QHeaderView, QLabel, QProgressBar, QPushButton, QScrollArea,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core import controller as ctl
from ..core import vendor_tools as vt
from ..core.models import DiskInfo, HealthStatus

VOLUME_COLUMNS = ["Name", "RAID", "State", "Size", "OS device", "Disks", "Note"]
DISK_COLUMNS = ["", "Slot / DID", "Model", "SMART health", "Temperature", "Power-on hours", "Defects",
                "Controller state", "Volume"]
SCAN_COL = 0
HEALTH_COLORS = {HealthStatus.HEALTHY: "#3fb950", HealthStatus.WARNING: "#e3b341",
                 HealthStatus.FAILED: "#f85149", HealthStatus.UNKNOWN: "#8b949e"}
SCAN_TEXT = "↻ Scan controller"


def _fit_height(table: QTableWidget):
    table.resizeColumnsToContents()
    table.horizontalHeader().setStretchLastSection(True)
    rows = sum(table.rowHeight(r) for r in range(table.rowCount()))
    scrollbar = table.horizontalScrollBar().sizeHint().height()
    table.setFixedHeight(rows + table.horizontalHeader().height() + scrollbar + 6)


def tool_text(state: Optional[vt.ControllerVendorState], local: bool) -> str:
    """The vendor tool line of the header (rich text)."""
    tool = state.tool if state else None
    if tool is None:
        return ("Vendor tool (perccli / storcli): checked when you click <b>Scan controller</b> "
                "(read-only; SmartLinux never installs anything).")
    if not tool.installed:
        return (escape(tool.error) + "<br>" if tool.error else "") + vt.MISSING_TOOL_HELP
    if not tool.usable:
        template = vt.NOT_ROOT_LOCAL_TEXT if local else vt.NOT_ROOT_TEXT
        return (f"<b>{escape(tool.name)} {escape(tool.version or '')} ✓</b> — "
                + escape(template.format(tool=tool.name)) + " The disks were still read with smartctl.")
    text = f"<b>{escape(tool.name)} {escape(tool.version or '')} ✓</b> (read-only)"
    if state.error:
        text += f" — <span style='color:#ff7b72'>{escape(state.error)}</span>"
    return text


class ControllerView(QWidget):
    scan_requested = Signal(str, str)        # server_id, controller label
    cancel_requested = Signal()
    disk_scan_requested = Signal(object)     # DiskInfo: this row's "↻"
    disk_open_requested = Signal(object)     # DiskInfo: open the full disk view

    def __init__(self, parent=None):
        super().__init__(parent)
        self.server_id: str = ""
        self.label: str = ""
        self.physical: List[DiskInfo] = []
        self.virtual: List[DiskInfo] = []
        self.state: Optional[vt.ControllerVendorState] = None
        self.row_scan_buttons: List[QPushButton] = []
        self._busy = False
        self._setup_ui()

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)
        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        section = QLabel("RAID CONTROLLER")
        section.setStyleSheet("font-size: 13px; font-weight: bold; color: #8b949e; letter-spacing: 1px;")
        layout.addWidget(section)

        # a) header
        card = QFrame()
        card.setProperty("class", "HeaderCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 16, 18, 16)
        card_layout.setSpacing(6)
        self.title_lbl = QLabel("")
        self.title_lbl.setStyleSheet("font-size: 21px; font-weight: bold; color: #ffffff;")
        card_layout.addWidget(self.title_lbl)
        self.facts_lbl = QLabel("")
        self.facts_lbl.setWordWrap(True)
        self.facts_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.facts_lbl.setStyleSheet("font-size: 13px; color: #c9d1d9;")
        card_layout.addWidget(self.facts_lbl)
        self.status_lbl = QLabel("")
        self.status_lbl.setWordWrap(True)
        card_layout.addWidget(self.status_lbl)
        self.tool_lbl = QLabel("")
        self.tool_lbl.setWordWrap(True)
        self.tool_lbl.setTextFormat(Qt.RichText)
        self.tool_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.tool_lbl.setStyleSheet("font-size: 13px; color: #c9d1d9;")
        card_layout.addWidget(self.tool_lbl)
        layout.addWidget(card)

        # b) scan row
        row = QHBoxLayout()
        self.scan_btn = QPushButton(SCAN_TEXT)
        self.scan_btn.setObjectName("PrimaryButton")
        self.scan_btn.setToolTip("Read the RAID state (vendor tool, if installed) and every physical disk, one after another")
        self.scan_btn.clicked.connect(lambda: self.scan_requested.emit(self.server_id, self.label))
        row.addWidget(self.scan_btn)
        self.last_scan_lbl = QLabel("")
        self.last_scan_lbl.setStyleSheet("font-size: 13px; color: #8b949e;")
        row.addWidget(self.last_scan_lbl)
        row.addStretch()
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(180)
        self.progress_bar.setVisible(False)
        row.addWidget(self.progress_bar)
        self.progress_lbl = QLabel("")
        self.progress_lbl.setStyleSheet("font-size: 13px; color: #e3b341;")
        row.addWidget(self.progress_lbl)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self.cancel_requested.emit)
        row.addWidget(self.cancel_btn)
        layout.addLayout(row)

        # e) not scanned yet
        self.not_scanned_lbl = QLabel(ctl.NOT_SCANNED_TEXT)
        self.not_scanned_lbl.setObjectName("WarningBanner")
        self.not_scanned_lbl.setWordWrap(True)
        self.not_scanned_lbl.setStyleSheet("font-size: 14px; color: #e3b341; padding: 10px; font-weight: 600;")
        layout.addWidget(self.not_scanned_lbl)

        # c) volumes
        self.volumes_caption = self._caption("VOLUMES")
        layout.addWidget(self.volumes_caption)
        self.volumes_table = self._table(VOLUME_COLUMNS)
        layout.addWidget(self.volumes_table)

        # d) physical disks
        layout.addWidget(self._caption("PHYSICAL DISKS"))
        self.disks_table = self._table(DISK_COLUMNS)
        self.disks_table.setToolTip("Double-click a disk to open its full S.M.A.R.T. view")
        self.disks_table.cellDoubleClicked.connect(self._on_disk_activated)
        layout.addWidget(self.disks_table)
        layout.addStretch(1)

    @staticmethod
    def _caption(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("font-size: 12px; font-weight: bold; color: #8b949e; letter-spacing: 1px;")
        return lbl

    @staticmethod
    def _table(columns: List[str]) -> QTableWidget:
        t = QTableWidget(0, len(columns))
        t.setHorizontalHeaderLabels(columns)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectRows)
        t.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        return t

    # ---- display ---------------------------------------------------------------------------
    def display(self, server_id: str, label: str, physical: List[DiskInfo], virtual: List[DiskInfo],
                storage=None, state: Optional[vt.ControllerVendorState] = None,
                last_scan: Optional[float] = None):
        self.server_id, self.label = server_id, label
        self.physical, self.virtual, self.state = list(physical), list(virtual), state
        report = state.report if state else None

        self.title_lbl.setText(label)
        facts = []
        driver = (report.driver_name if report else None) or (storage.driver if storage else None)
        driver_version = (report.driver_version if report else None) or (storage.driver_version if storage else None)
        if driver:
            facts.append(f"Driver: <b>{escape(' '.join(x for x in (driver, driver_version) if x))}</b>")
        firmware = (report.fw_version if report else None) or (storage.firmware if storage else None)
        if firmware:
            facts.append(f"Firmware: <b>{escape(firmware)}</b>")
        if report and report.battery.present:
            b = report.battery
            battery = ", ".join(x for x in (b.state, b.charge) if x)
            facts.append(f"Battery: <b>{escape(battery or 'present')}</b>")
        if storage and storage.pci_slot:
            facts.append(f"PCI: <b>{escape(storage.pci_slot)}</b>")
        facts.append(f"{len(physical)} physical, {len(virtual)} virtual")
        self.facts_lbl.setText(" &nbsp;·&nbsp; ".join(facts))

        status = ctl.controller_health(physical, virtual, report)
        scanned = ctl.has_scan_data(physical, report)
        if not scanned:
            text = "Not scanned"
        elif report and report.level > vt.OK:
            text = report.summary
        else:
            text = {HealthStatus.HEALTHY: "All disks and volumes OK",
                    HealthStatus.WARNING: "Attention needed: see the tables below",
                    HealthStatus.FAILED: "Failure: see the tables below",
                    HealthStatus.UNKNOWN: "Some disks are not scanned yet or could not be read"}[status]
        self.status_lbl.setText(f"{status.icon}  {text}")
        self.status_lbl.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {HEALTH_COLORS[status]};")
        self.tool_lbl.setText(tool_text(state, local=not server_id))

        self.last_scan_lbl.setText(
            f"Last scan: {datetime.fromtimestamp(last_scan).strftime('%H:%M')}" if last_scan else "Never scanned")
        self.not_scanned_lbl.setVisible(not scanned)
        self._fill_volumes(report)
        self._fill_disks(report)
        self.set_busy(self._busy)

    def _fill_volumes(self, report):
        has = bool(report and report.volumes)
        self.volumes_caption.setVisible(has)
        self.volumes_table.setVisible(has)
        self.volumes_table.setRowCount(0)
        if not has:
            return
        self.volumes_table.setRowCount(len(report.volumes))
        for r, v in enumerate(report.volumes):
            members = ", ".join(f"{m['EID:Slt']} (DID {m['DID']})" for m in v.members) or "—"
            note = ctl.SINGLE_DRIVE_NOTE if ctl.single_drive_volume(v) else ""
            if v.active_operations:
                note = "; ".join(x for x in (note, v.active_operations) if x)
            values = [v.name or v.dg_vd, v.raid_type, v.state_text, v.size, v.os_drive or "—", members, note]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                if c == 2:
                    item.setForeground(QColor(HEALTH_COLORS[vt.LEVEL_HEALTH[v.level]]))
                    if v.reasons:
                        item.setToolTip("; ".join(v.reasons))
                if c == 6 and ctl.single_drive_volume(v):
                    item.setToolTip("RAID0 of a single disk: no redundancy. This can be a deliberate design.")
                self.volumes_table.setItem(r, c, item)
        _fit_height(self.volumes_table)

    def _vendor_drive(self, report, disk: DiskInfo):
        if not report:
            return None
        family = (disk.smart_dev_type or "").split("+")[-1]
        return next((d for d in report.drives if d.smartctl_type == family), None)

    def _fill_disks(self, report):
        self.disks_table.setRowCount(len(self.physical))
        self.row_scan_buttons = []
        for r, disk in enumerate(self.physical):
            vd = self._vendor_drive(report, disk)
            scanned = ctl.is_scanned(disk) or disk.error_message is not None
            if not scanned:
                smart = "—"
            elif disk.error_message and not disk.attributes:
                smart = f"{HealthStatus.UNKNOWN.icon} Error: {disk.error_message}"
            else:
                smart = f"{disk.health_status.icon} {disk.health_summary}"
            slot = f"slot {disk.slot}" if disk.slot is not None else disk.name
            if vd:
                slot = f"{vd.eid_slot} / DID {vd.did}"
            volume = (vd.volume.name or vd.volume.dg_vd) if vd and vd.volume else (disk.raid_volume or "—")
            values = [
                slot,
                disk.model if disk.model != "Physical Drive" or not vd else (vd.model or disk.model),
                smart,
                disk.formatted_temperature if disk.temperature_c is not None else (vd.temperature if vd and vd.temperature else "—"),
                f"{disk.power_on_hours:,}" if disk.power_on_hours is not None else "—",
                ctl.defects_text(disk) if scanned else "—",
                f"{vd.state_text}" if vd else "—",
                volume,
            ]
            for c, value in enumerate(values, 1):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, r)
                if c == 3 and scanned:
                    item.setForeground(QColor(HEALTH_COLORS[ctl.disk_health(disk)]))
                    if disk.error_message:
                        item.setToolTip(disk.error_message)
                if c == 7 and vd:
                    item.setForeground(QColor(HEALTH_COLORS[vt.LEVEL_HEALTH[vd.level]]))
                    tip = [f"Media errors {vd.media_errors}, other errors {vd.other_errors}, "
                           f"predictive failures {vd.predictive_failures}, SMART alert "
                           f"{'Yes' if vd.smart_alert else 'No'}"] + vd.reasons
                    item.setToolTip("\n".join(tip))
                self.disks_table.setItem(r, c, item)
            btn = QPushButton("↻")
            btn.setToolTip("Scan only this disk")
            btn.setFixedWidth(44)
            btn.setStyleSheet("padding: 2px 6px;")   # the theme's button padding hides the glyph
            btn.clicked.connect(lambda _=False, d=disk: self.disk_scan_requested.emit(d))
            self.disks_table.setCellWidget(r, SCAN_COL, btn)
            # the theme pads table cells by 8 px top and bottom, which also shrinks cell widgets
            self.disks_table.setRowHeight(r, max(self.disks_table.rowHeight(r), btn.sizeHint().height() + 20))
            self.row_scan_buttons.append(btn)
        _fit_height(self.disks_table)

    def _on_disk_activated(self, row: int, _col: int):
        if 0 <= row < len(self.physical):
            self.disk_open_requested.emit(self.physical[row])

    # ---- scan progress -----------------------------------------------------------------------
    def set_busy(self, busy: bool):
        self._busy = busy
        self.scan_btn.setEnabled(not busy)
        self.progress_bar.setVisible(busy)
        self.cancel_btn.setVisible(busy)
        self.cancel_btn.setEnabled(busy)
        if not busy:
            self.progress_lbl.setText("")
        for btn in self.row_scan_buttons:
            btn.setEnabled(not busy)

    def set_progress(self, text: str, done: int, total: int):
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(done)
        self.progress_lbl.setText(text)
