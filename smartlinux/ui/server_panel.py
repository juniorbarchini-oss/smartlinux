"""Server panel: what SmartLinux recognised about a host (identity card + storage controllers).

Display only. The data is collected when the user connects / refreshes the host, never here.
"""
from dataclasses import dataclass
from datetime import datetime
from html import escape
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QRadioButton, QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core import vendor_tools as vt
from ..core.system_info import SystemInfo

CONTROLLER_COLUMNS = ["Controller", "Type", "PCI", "Driver", "Physical disks", "Virtual disks", "Firmware"]
VOLUME_COLUMNS = ["DG/VD", "Name", "Type", "State", "Cache", "Size", "OS drive", "Physical drives",
                  "Active operations", "Notes"]
DRIVE_COLUMNS = ["EID:Slt", "DID", "smartctl", "Volume", "State", "Media errors", "Other errors",
                 "Predictive failures", "SMART alert", "Temperature", "Link speed", "Status"]
LEVEL_COLORS = {vt.OK: "#3fb950", vt.ATTENTION: "#e3b341", vt.WARNING: "#e3b341", vt.FAILED: "#f85149"}

STANDARD_TEXT = "Standard read (smartctl)"
VENDOR_TEXT = "Vendor tool"


@dataclass
class VendorSection:
    """Widgets of one controller's "Vendor tool" section (kept for the main window and tests)."""
    standard_btn: QRadioButton
    vendor_btn: QRadioButton
    info_lbl: QLabel
    status_lbl: Optional[QLabel] = None
    volumes_table: Optional[QTableWidget] = None
    drives_table: Optional[QTableWidget] = None
    battery_lbl: Optional[QLabel] = None


def _fit_height(table: QTableWidget):
    table.resizeColumnsToContents()
    table.horizontalHeader().setStretchLastSection(True)
    rows = sum(table.rowHeight(r) for r in range(table.rowCount()))
    scrollbar = table.horizontalScrollBar().sizeHint().height()
    table.setFixedHeight(rows + table.horizontalHeader().height() + scrollbar + 6)


class ServerPanel(QWidget):
    """Identity card ("Dell PowerEdge R630 · 2 × Intel Xeon ...") and a storage controller table."""

    refresh_requested = Signal(str)  # server_id ("" = this computer)
    vendor_mode_changed = Signal(str, str, str)  # server_id, controller PCI slot, mode
    disk_link_requested = Signal(str, str, str)  # server_id, "smartctl" | "os_drive", value

    def __init__(self, parent=None):
        super().__init__(parent)
        self.server_id: Optional[str] = None
        self.info: Optional[SystemInfo] = None
        self.vendor_sections: Dict[str, VendorSection] = {}
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
        layout.setSpacing(14)

        title_row = QHBoxLayout()
        section = QLabel("SERVER")
        section.setStyleSheet("font-size: 13px; font-weight: bold; color: #8b949e; letter-spacing: 1px;")
        title_row.addWidget(section)
        title_row.addStretch()
        self.collected_lbl = QLabel("")
        self.collected_lbl.setStyleSheet("font-size: 12px; color: #8b949e;")
        title_row.addWidget(self.collected_lbl)
        self.refresh_btn = QPushButton("↻ Refresh")
        self.refresh_btn.setObjectName("PrimaryButton")
        self.refresh_btn.setToolTip("Reconnect, list drives and read the server information again")
        self.refresh_btn.clicked.connect(lambda: self.refresh_requested.emit(self.server_id or ""))
        title_row.addWidget(self.refresh_btn)
        layout.addLayout(title_row)

        # Identity card
        self.card = QFrame()
        self.card.setProperty("class", "HeaderCard")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(18, 16, 18, 16)
        card_layout.setSpacing(8)
        self.host_lbl = QLabel("")
        self.host_lbl.setStyleSheet("font-size: 21px; font-weight: bold; color: #ffffff;")
        card_layout.addWidget(self.host_lbl)
        self.identity_lbl = QLabel("")
        self.identity_lbl.setWordWrap(True)
        self.identity_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.identity_lbl.setStyleSheet("font-size: 15px; color: #c9d1d9; font-weight: 600;")
        card_layout.addWidget(self.identity_lbl)
        self.details_grid = QGridLayout()
        self.details_grid.setHorizontalSpacing(18)
        self.details_grid.setVerticalSpacing(4)
        card_layout.addLayout(self.details_grid)
        layout.addWidget(self.card)

        self.notice_lbl = QLabel("")
        self.notice_lbl.setWordWrap(True)
        self.notice_lbl.setStyleSheet("font-size: 13px; color: #8b949e;")
        layout.addWidget(self.notice_lbl)

        hdr = QLabel("STORAGE CONTROLLERS")
        hdr.setStyleSheet("font-size: 13px; font-weight: bold; color: #8b949e; letter-spacing: 1px;")
        layout.addWidget(hdr)

        self.table = QTableWidget()
        self.table.setColumnCount(len(CONTROLLER_COLUMNS))
        self.table.setHorizontalHeaderLabels(CONTROLLER_COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table)

        # Per MegaRAID controller: Standard read / Vendor tool (perccli, storcli; read-only)
        self.vendor_container = QVBoxLayout()
        self.vendor_container.setSpacing(14)
        layout.addLayout(self.vendor_container)
        layout.addStretch(1)

    def _clear_details(self):
        while self.details_grid.count():
            item = self.details_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def display(self, server_id: str, host_name: str, info: Optional[SystemInfo],
                vendor_states: Optional[Dict[str, vt.ControllerVendorState]] = None):
        self.server_id = server_id
        self.info = info
        self.host_lbl.setText(host_name)
        self._clear_details()
        self.table.setRowCount(0)
        self._clear_vendor()

        if info is None:
            self.identity_lbl.setText("Not read yet.")
            self.collected_lbl.setText("")
            self.notice_lbl.setText(
                "Server information is read only when you connect or refresh this host "
                "(click '↻ Refresh')."
            )
            return

        self.identity_lbl.setText(info.identity_line() or "No identity information reported by this host.")
        if info.collected_at:
            self.collected_lbl.setText(f"Read at {datetime.fromtimestamp(info.collected_at).strftime('%H:%M')}")
        for row, (label, value) in enumerate(info.details()):
            k = QLabel(label)
            k.setStyleSheet("font-size: 13px; color: #8b949e;")
            v = QLabel(f"<b>{escape(value)}</b>")
            v.setTextInteractionFlags(Qt.TextSelectableByMouse)
            v.setStyleSheet("font-size: 13px; color: #c9d1d9;")
            self.details_grid.addWidget(k, row // 2, (row % 2) * 2)
            self.details_grid.addWidget(v, row // 2, (row % 2) * 2 + 1)

        notes = []
        if info.error:
            notes.append(f"Some information could not be read: {info.error}")
        if not info.controllers:
            notes.append("No storage controllers reported (lspci is not available or returned nothing).")
        if not info.service_tag and info.vendor:
            notes.append("Service tag needs root (dmidecode).")
        self.notice_lbl.setText("  ".join(notes))

        self.table.setRowCount(len(info.controllers))
        for row, c in enumerate(info.controllers):
            driver = c.driver or "—"
            if c.driver and c.driver_version:
                driver = f"{c.driver} {c.driver_version}"
            physical = str(c.physical_disks) if c.counts_known else "—"
            virtual = str(c.virtual_disks) if c.counts_known else "—"
            values = [c.label, c.kind, c.pci_slot, driver, physical, virtual, c.firmware or "—"]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setToolTip(" / ".join(x for x in (c.vendor, c.device, c.subsystem) if x))
                if col == 3 and c.driver_description:
                    item.setToolTip(c.driver_description)
                if col >= 4:
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, col, item)
        _fit_height(self.table)

        for ctrl in info.controllers:
            if vt.supported(ctrl):
                state = (vendor_states or {}).get(ctrl.pci_slot) or vt.ControllerVendorState(pci_slot=ctrl.pci_slot)
                self._add_vendor_section(ctrl, state)

    # ---- vendor tool sections -----------------------------------------------------------
    def _clear_vendor(self):
        self.vendor_sections = {}
        while self.vendor_container.count():
            item = self.vendor_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _add_vendor_section(self, ctrl, state: vt.ControllerVendorState):
        box = QFrame()
        box.setProperty("class", "HeaderCard")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        title = QLabel(f"{ctrl.label} · {ctrl.pci_slot}")
        title.setStyleSheet("font-size: 15px; font-weight: bold; color: #ffffff;")
        lay.addWidget(title)

        tool = state.tool
        usable = bool(tool and tool.usable)
        row = QHBoxLayout()
        mode_lbl = QLabel("Read mode:")
        mode_lbl.setStyleSheet("font-size: 13px; color: #8b949e;")
        row.addWidget(mode_lbl)
        std = QRadioButton(STANDARD_TEXT)
        ven = QRadioButton(f"{VENDOR_TEXT} ({tool.found_text})" if tool and tool.installed else VENDOR_TEXT)
        group = QButtonGroup(box)
        group.addButton(std)
        group.addButton(ven)
        ven.setEnabled(usable)
        (ven if usable and state.mode == vt.MODE_VENDOR else std).setChecked(True)
        row.addWidget(std)
        row.addWidget(ven)
        row.addStretch()
        lay.addLayout(row)

        info_lbl = QLabel(self._vendor_info_text(state, local=not self.server_id))
        info_lbl.setWordWrap(True)
        info_lbl.setTextFormat(Qt.RichText)
        info_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        info_lbl.setStyleSheet("font-size: 13px; color: #c9d1d9;")
        lay.addWidget(info_lbl)
        section = VendorSection(standard_btn=std, vendor_btn=ven, info_lbl=info_lbl)

        slot = ctrl.pci_slot
        ven.toggled.connect(lambda on, s=slot: self.vendor_mode_changed.emit(
            self.server_id or "", s, vt.MODE_VENDOR if on else vt.MODE_STANDARD))

        if usable and state.mode == vt.MODE_VENDOR and state.report:
            self._add_report(lay, state.report, section)
        self.vendor_sections[slot] = section
        self.vendor_container.addWidget(box)

    @staticmethod
    def _vendor_info_text(state: vt.ControllerVendorState, local: bool) -> str:
        tool = state.tool
        if tool is None:
            return ("Vendor tool: not checked yet. Click <b>↻ Refresh</b> to look for perccli / storcli "
                    "(only read-only commands are run; nothing is installed).")
        if tool.error and not tool.installed:
            return escape(tool.error)
        if not tool.installed:
            return vt.MISSING_TOOL_HELP
        if not tool.usable:
            template = vt.NOT_ROOT_LOCAL_TEXT if local else vt.NOT_ROOT_TEXT
            return f"<b>{escape(tool.found_text)}</b>. " + escape(template.format(tool=tool.name))
        if state.mode != vt.MODE_VENDOR:
            return (f"<b>{escape(tool.found_text)}</b>. Choose <b>{VENDOR_TEXT}</b> to also read the RAID "
                    f"state, the disks of each volume, the controller's counters and the battery "
                    f"(read-only), then click <b>↻ Refresh</b>.")
        if state.error:
            return f"<b>{escape(tool.found_text)}</b>. <span style='color:#ff7b72'>{escape(state.error)}</span>"
        if not state.report:
            return (f"<b>{escape(tool.found_text)}</b>. Click <b>↻ Refresh</b> to read this controller "
                    f"with {escape(tool.name)} (read-only).")
        return f"<b>{escape(tool.found_text)}</b>. Read-only data from {escape(tool.name)}:"

    def _add_report(self, lay: QVBoxLayout, report: vt.VendorController, section: VendorSection):
        color = LEVEL_COLORS[report.level]
        status = QLabel(f"{report.health.icon}  {escape(report.summary)}")
        status.setWordWrap(True)
        status.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {color};")
        lay.addWidget(status)
        section.status_lbl = status

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        for i, (k, v) in enumerate(report.details()):
            kl = QLabel(k)
            kl.setStyleSheet("font-size: 13px; color: #8b949e;")
            vl = QLabel(f"<b>{escape(v)}</b>")
            vl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(kl, i // 2, (i % 2) * 2)
            grid.addWidget(vl, i // 2, (i % 2) * 2 + 1)
        lay.addLayout(grid)

        lay.addWidget(self._caption("VIRTUAL DRIVES (VOLUMES)"))
        vols = self._table(VOLUME_COLUMNS, len(report.volumes))
        for r, v in enumerate(report.volumes):
            members = ", ".join(f"{m['EID:Slt']} (DID {m['DID']}, {m['State']})" for m in v.members)
            values = [v.dg_vd, v.name, v.raid_type, f"{v.state} ({v.state_text})", v.cache, v.size,
                      v.os_drive or "—", members or "—", v.active_operations or "None",
                      "; ".join(v.notes + v.reasons) or "—"]
            self._fill_row(vols, r, values, v.level, 3, link=("os_drive", v.os_drive) if v.os_drive else None)
        _fit_height(vols)
        vols.cellDoubleClicked.connect(lambda r, _c, t=vols: self._emit_link(t, r))
        lay.addWidget(vols)
        section.volumes_table = vols

        lay.addWidget(self._caption("PHYSICAL DRIVES"))
        drives = self._table(DRIVE_COLUMNS, len(report.drives))
        for r, d in enumerate(report.drives):
            alert = "—" if d.smart_alert is None else ("Yes" if d.smart_alert else "No")
            values = [d.eid_slot, "—" if d.did is None else str(d.did), d.smartctl_type or "—",
                      (d.volume.name or d.volume.dg_vd) if d.volume else "—", f"{d.state} ({d.state_text})",
                      self._num(d.media_errors), self._num(d.other_errors), self._num(d.predictive_failures),
                      alert, d.temperature or "—", d.link_speed or "—", vt.LEVEL_TEXT[d.level]]
            self._fill_row(drives, r, values, d.level, 11, tooltip="; ".join(d.reasons),
                           link=("smartctl", d.smartctl_type) if d.smartctl_type else None)
        _fit_height(drives)
        drives.cellDoubleClicked.connect(lambda r, _c, t=drives: self._emit_link(t, r))
        lay.addWidget(drives)
        section.drives_table = drives

        b = report.battery
        if b.present:
            parts = [f"{b.kind or 'Battery'}: {b.state or 'unknown'}"]
            if b.charge:
                parts.append(f"charge {b.charge}")
            if b.temperature:
                parts.append(b.temperature)
            parts.append(f"replacement required: {'Yes' if b.replacement_required else 'No'}")
            parts.append(f"pack missing: {'Yes' if b.pack_missing else 'No'}")
            text = " · ".join(parts)
        else:
            text = b.note or "No battery reported"
        battery = QLabel(f"🔋  {escape(text)}")
        battery.setWordWrap(True)
        battery.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {LEVEL_COLORS[b.level]};")
        lay.addWidget(battery)
        section.battery_lbl = battery
        if report.errors:
            err = QLabel("Not read: " + escape("; ".join(report.errors)))
            err.setWordWrap(True)
            err.setStyleSheet("font-size: 12px; color: #ff7b72;")
            lay.addWidget(err)

    @staticmethod
    def _num(value) -> str:
        return "—" if value is None else f"{value:,}"

    @staticmethod
    def _caption(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("font-size: 12px; font-weight: bold; color: #8b949e; letter-spacing: 1px;")
        return lbl

    @staticmethod
    def _table(columns: List[str], rows: int) -> QTableWidget:
        t = QTableWidget(rows, len(columns))
        t.setHorizontalHeaderLabels(columns)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectRows)
        t.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        return t

    @staticmethod
    def _fill_row(table: QTableWidget, row: int, values: List[str], level: int, status_col: int,
                  tooltip: str = "", link=None):
        for col, value in enumerate(values):
            item = QTableWidgetItem(value)
            if col == status_col:
                item.setForeground(QColor(LEVEL_COLORS[level]))
                if tooltip:
                    item.setToolTip(tooltip)
            if link:
                item.setData(Qt.UserRole, link)
                if col == 0:
                    item.setToolTip("Double-click to open this disk in the sidebar")
            table.setItem(row, col, item)

    def _emit_link(self, table: QTableWidget, row: int):
        item = table.item(row, 0)
        link = item.data(Qt.UserRole) if item else None
        if link:
            self.disk_link_requested.emit(self.server_id or "", link[0], link[1])
