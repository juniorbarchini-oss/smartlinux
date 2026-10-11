"""Server panel: what SmartLinux recognised about a host (identity card + storage controllers).

Display only. The data is collected when the user connects / refreshes the host, never here.
"""
from datetime import datetime
from html import escape
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core.system_info import SystemInfo

CONTROLLER_COLUMNS = ["Controller", "Type", "PCI", "Driver", "Physical disks", "Virtual disks", "Firmware"]


class ServerPanel(QWidget):
    """Identity card ("Dell PowerEdge R630 · 2 × Intel Xeon ...") and a storage controller table."""

    refresh_requested = Signal(str)  # server_id ("" = this computer)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.server_id: Optional[str] = None
        self.info: Optional[SystemInfo] = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
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
        layout.addWidget(self.table, 1)

    def _clear_details(self):
        while self.details_grid.count():
            item = self.details_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def display(self, server_id: str, host_name: str, info: Optional[SystemInfo]):
        self.server_id = server_id
        self.info = info
        self.host_lbl.setText(host_name)
        self._clear_details()
        self.table.setRowCount(0)

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
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)
