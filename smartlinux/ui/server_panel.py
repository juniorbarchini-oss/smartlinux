"""Server panel: what SmartLinux recognised about a host (identity card + storage controllers),
its RAID controllers (each opens the controller view) and "↻ Scan all disks".

Display only. The data is collected when the user connects / refreshes the host, and disks are
read only when the user clicks a scan button.
"""
from dataclasses import dataclass
from datetime import datetime
from html import escape
from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QProgressBar, QPushButton,
    QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core import controller as ctl
from ..core.models import HealthStatus
from ..core.system_info import SystemInfo

CONTROLLER_COLUMNS = ["Controller", "Type", "PCI", "Driver", "Physical disks", "Virtual disks", "Firmware"]
RAID_COLUMNS = ["RAID controller", "Status", "Physical disks", "Virtual disks"]
STATUS_TEXT = {HealthStatus.HEALTHY: "OK", HealthStatus.WARNING: "Attention",
               HealthStatus.FAILED: "Failure", HealthStatus.UNKNOWN: "Not scanned"}


@dataclass
class ControllerSummary:
    """One row of the RAID controllers table (built by the main window from the host's disks)."""
    label: str
    status: HealthStatus
    physical: int
    virtual: int
    scanned: bool


def _fit_height(table: QTableWidget):
    table.resizeColumnsToContents()
    table.horizontalHeader().setStretchLastSection(True)
    rows = sum(table.rowHeight(r) for r in range(table.rowCount()))
    scrollbar = table.horizontalScrollBar().sizeHint().height()
    table.setFixedHeight(rows + table.horizontalHeader().height() + scrollbar + 6)


class ServerPanel(QWidget):
    """Identity card ("Dell PowerEdge R630 · 2 × Intel Xeon ...") and a storage controller table."""

    refresh_requested = Signal(str)  # server_id ("" = this computer)
    diagnostic_requested = Signal(str)           # server_id: open the "Export diagnostic bundle" dialog
    scan_all_requested = Signal(str)             # server_id: read every disk of the host, in series
    cancel_requested = Signal()
    controller_open_requested = Signal(str, str)  # server_id, controller label

    def __init__(self, parent=None):
        super().__init__(parent)
        self.server_id: Optional[str] = None
        self.info: Optional[SystemInfo] = None
        self.controllers: List[ControllerSummary] = []
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
        self.diag_btn = QPushButton("Export diagnostic bundle…")
        self.diag_btn.setToolTip("Run a fixed list of read-only commands, sanitize the output and save "
                                 "it as a .zip you can give to the developer (nothing is sent)")
        self.diag_btn.clicked.connect(lambda: self.diagnostic_requested.emit(self.server_id or ""))
        title_row.addWidget(self.diag_btn)
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

        # RAID controllers: each row opens the controller view
        self.discovery_lbl = QLabel(ctl.DISCOVERY_TEXT)
        self.discovery_lbl.setObjectName("WarningBanner")
        self.discovery_lbl.setWordWrap(True)
        self.discovery_lbl.setStyleSheet("font-size: 14px; color: #e3b341; padding: 10px; font-weight: 600;")
        self.discovery_lbl.setVisible(False)
        layout.addWidget(self.discovery_lbl)
        self.raid_caption = QLabel("RAID CONTROLLERS")
        self.raid_caption.setStyleSheet("font-size: 13px; font-weight: bold; color: #8b949e; letter-spacing: 1px;")
        layout.addWidget(self.raid_caption)
        self.raid_table = QTableWidget(0, len(RAID_COLUMNS))
        self.raid_table.setHorizontalHeaderLabels(RAID_COLUMNS)
        self.raid_table.verticalHeader().setVisible(False)
        self.raid_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.raid_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.raid_table.setToolTip("Double-click a controller to open it")
        self.raid_table.cellDoubleClicked.connect(self._on_raid_row)
        layout.addWidget(self.raid_table)

        # Scan all disks (also for hosts without a RAID controller)
        scan_row = QHBoxLayout()
        self.scan_all_btn = QPushButton("↻ Scan all disks")
        self.scan_all_btn.setObjectName("PrimaryButton")
        self.scan_all_btn.setToolTip("Read every physical disk of this computer, one after another")
        self.scan_all_btn.clicked.connect(lambda: self.scan_all_requested.emit(self.server_id or ""))
        scan_row.addWidget(self.scan_all_btn)
        scan_row.addStretch()
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(180)
        self.progress_bar.setVisible(False)
        scan_row.addWidget(self.progress_bar)
        self.progress_lbl = QLabel("")
        self.progress_lbl.setStyleSheet("font-size: 13px; color: #e3b341;")
        scan_row.addWidget(self.progress_lbl)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self.cancel_requested.emit)
        scan_row.addWidget(self.cancel_btn)
        layout.addLayout(scan_row)
        layout.addStretch(1)

    def _clear_details(self):
        while self.details_grid.count():
            item = self.details_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def display(self, server_id: str, host_name: str, info: Optional[SystemInfo],
                controllers: Optional[List[ControllerSummary]] = None):
        self.server_id = server_id
        self.info = info
        self.host_lbl.setText(host_name)
        self._clear_details()
        self.table.setRowCount(0)
        self._fill_raid(controllers or [])

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


    # ---- RAID controllers / scan all -------------------------------------------------------
    def _fill_raid(self, controllers: List[ControllerSummary]):
        self.controllers = list(controllers)
        has = bool(controllers)
        self.raid_caption.setVisible(has)
        self.raid_table.setVisible(has)
        self.discovery_lbl.setVisible(any(not c.scanned for c in controllers))
        self.raid_table.setRowCount(len(controllers))
        for r, c in enumerate(controllers):
            values = [c.label, f"{c.status.icon}  {STATUS_TEXT[c.status]}", str(c.physical), str(c.virtual)]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col >= 2:
                    item.setTextAlignment(Qt.AlignCenter)
                self.raid_table.setItem(r, col, item)
        if has:
            _fit_height(self.raid_table)

    def _on_raid_row(self, row: int, _col: int):
        if 0 <= row < len(self.controllers):
            self.controller_open_requested.emit(self.server_id or "", self.controllers[row].label)

    def set_busy(self, busy: bool):
        self.scan_all_btn.setEnabled(not busy)
        self.progress_bar.setVisible(busy)
        self.cancel_btn.setVisible(busy)
        if not busy:
            self.progress_lbl.setText("")

    def set_progress(self, text: str, done: int, total: int):
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(done)
        self.progress_lbl.setText(text)
