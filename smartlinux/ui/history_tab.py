from typing import Optional, List, Dict, Any
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QHeaderView
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from ..core.models import DiskInfo
from ..core.history_manager import HistoryManager

COLUMNS = [
    ("timestamp", "Fecha (UTC)"),
    ("pending_sector", "Pending Sector"),
    ("reallocated_sector_ct", "Reallocated Sector Ct"),
    ("reallocated_event_count", "Reallocated Event Count"),
    ("nvme_wear_pct", "NVMe Wear %"),
    ("temperature_c", "Temp (°C)"),
    ("power_on_hours", "Power-On Hours"),
]

# Columns where a higher value than the previous scan means the drive got worse.
WORSENING_COLUMNS = {"pending_sector", "reallocated_sector_ct", "reallocated_event_count", "nvme_wear_pct"}


class HistoryTab(QWidget):
    """Shows the local SMART history (JSONL) for the currently selected disk."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(10)

        hdr = QHBoxLayout()
        title = QLabel("HISTORIAL DE ATRIBUTOS SMART")
        title.setStyleSheet("font-size: 13px; font-weight: bold; color: #8b949e; letter-spacing: 1px;")
        hdr.addWidget(title)
        hdr.addStretch()
        self.count_lbl = QLabel("0 registros")
        self.count_lbl.setStyleSheet("font-size: 12px; color: #8b949e;")
        hdr.addWidget(self.count_lbl)
        layout.addLayout(hdr)

        self.info_lbl = QLabel(
            "Se guarda un registro cada vez que se hace 'Scan Now' sobre este disco. "
            "Archivo: ~/.local/share/smartlinux/history.jsonl"
        )
        self.info_lbl.setWordWrap(True)
        self.info_lbl.setStyleSheet("font-size: 12px; color: #8b949e;")
        layout.addWidget(self.info_lbl)

        self.table = QTableWidget()
        self.table.setColumnCount(len(COLUMNS))
        self.table.setHorizontalHeaderLabels([label for _, label in COLUMNS])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table)

    def load_history(self, disk: Optional[DiskInfo]):
        self.table.setRowCount(0)
        if disk is None:
            self.count_lbl.setText("0 registros")
            return

        entries = HistoryManager.get_history(disk)
        self.count_lbl.setText(f"{len(entries)} registro(s)")
        if not entries:
            return

        # Most recent scan first.
        entries = list(reversed(entries))
        self.table.setRowCount(len(entries))

        for row, entry in enumerate(entries):
            previous = entries[row + 1] if row + 1 < len(entries) else None
            for col, (field, _) in enumerate(COLUMNS):
                value = entry.get(field)
                display = "N/A" if value is None else str(value)
                item = QTableWidgetItem(display)
                item.setTextAlignment(Qt.AlignCenter if field != "timestamp" else Qt.AlignLeft | Qt.AlignVCenter)

                if field in WORSENING_COLUMNS and previous is not None:
                    prev_value = previous.get(field)
                    if isinstance(value, (int, float)) and isinstance(prev_value, (int, float)) and value > prev_value:
                        item.setForeground(QColor("#f85149"))
                        item.setToolTip(f"Empeoró respecto al scan anterior ({prev_value} → {value})")

                self.table.setItem(row, col, item)

        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)
