from typing import Optional
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame,
    QScrollArea, QSizePolicy
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from ..core.models import DiskInfo, HealthStatus


class MetricCard(QFrame):
    """Modern card displaying a single telemetry metric."""

    def __init__(self, title: str, icon: str, value: str = "N/A", subtitle: str = "", parent=None):
        super().__init__(parent)
        self.setProperty("class", "MetricCard")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setMinimumHeight(96)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(5)

        hdr = QHBoxLayout()
        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet("font-size: 18px;")
        hdr.addWidget(icon_lbl)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #8b949e; text-transform: uppercase;")
        hdr.addWidget(title_lbl)
        hdr.addStretch()
        layout.addLayout(hdr)

        self.val_lbl = QLabel(value)
        self.val_lbl.setStyleSheet("font-size: 22px; font-weight: bold; color: #f0f6fc;")
        layout.addWidget(self.val_lbl)

        self.sub_lbl = QLabel(subtitle)
        self.sub_lbl.setStyleSheet("font-size: 12px; color: #8b949e;")
        layout.addWidget(self.sub_lbl)

    def update_data(self, value: str, subtitle: str = "", color: Optional[str] = None):
        self.val_lbl.setText(value)
        self.sub_lbl.setText(subtitle)
        if color:
            self.val_lbl.setStyleSheet(f"font-size: 22px; font-weight: bold; color: {color};")
        else:
            self.val_lbl.setStyleSheet("font-size: 22px; font-weight: bold; color: #f0f6fc;")


class DetailPanel(QWidget):
    """Right-hand panel showing detailed health, metrics, and SMART attributes."""

    scan_requested = Signal(object)     # Emits current DiskInfo
    export_requested = Signal(object)   # Emits current DiskInfo
    eject_requested = Signal(str)       # Emits device_path for local USB drive

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_disk: Optional[DiskInfo] = None
        self._setup_ui()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(14)

        # 1. Header Card (Model, S/N, Health Status Badge)
        self.header_card = QFrame()
        self.header_card.setProperty("class", "HeaderCard")
        hdr_layout = QVBoxLayout(self.header_card)
        hdr_layout.setContentsMargins(18, 16, 18, 16)
        hdr_layout.setSpacing(8)

        # Top row: Title + Health Badge
        top_row = QHBoxLayout()
        self.model_lbl = QLabel("Select a storage device to view diagnostics")
        self.model_lbl.setStyleSheet("font-size: 21px; font-weight: bold; color: #ffffff;")
        top_row.addWidget(self.model_lbl)
        top_row.addStretch()

        self.health_badge = QLabel("NO SELECTION")
        self.health_badge.setStyleSheet(
            "background-color: #30363d; color: #8b949e; font-weight: bold; "
            "font-size: 13px; padding: 6px 14px; border-radius: 12px;"
        )
        top_row.addWidget(self.health_badge)
        hdr_layout.addLayout(top_row)

        # Bottom row: Serial, Firmware, Protocol, Path
        self.info_lbl = QLabel("Device telemetry details will appear here.")
        self.info_lbl.setStyleSheet("font-size: 13px; color: #8b949e;")
        hdr_layout.addWidget(self.info_lbl)

        main_layout.addWidget(self.header_card)

        # Error / Diagnostic Banner
        self.error_banner = QFrame()
        self.error_banner.setObjectName("AlertBanner")
        self.error_banner.setVisible(False)
        eb_layout = QHBoxLayout(self.error_banner)
        eb_layout.setContentsMargins(12, 8, 12, 8)
        self.error_icon = QLabel("⚠️")
        self.error_icon.setStyleSheet("font-size: 16px;")
        eb_layout.addWidget(self.error_icon)
        self.error_lbl = QLabel("")
        self.error_lbl.setWordWrap(True)
        self.error_lbl.setStyleSheet("color: #ff7b72; font-size: 13px; font-weight: 500;")
        eb_layout.addWidget(self.error_lbl, 1)
        main_layout.addWidget(self.error_banner)

        # 2. Quick Metrics Grid
        metrics_layout = QHBoxLayout()
        metrics_layout.setSpacing(12)

        self.temp_card = MetricCard("Temperature", "🌡️", "N/A", "Thermal Sensor")
        self.hours_card = MetricCard("Power-On Time", "⏱️", "N/A", "Accumulated Hours")
        self.cycles_card = MetricCard("Power Cycles", "🔄", "N/A", "Power-on count")

        metrics_layout.addWidget(self.temp_card)
        metrics_layout.addWidget(self.hours_card)
        metrics_layout.addWidget(self.cycles_card)
        main_layout.addLayout(metrics_layout)

        # 3. S.M.A.R.T. Attribute Table Title
        table_hdr = QHBoxLayout()
        table_title = QLabel("S.M.A.R.T. ATTRIBUTES & DIAGNOSTICS")
        table_title.setStyleSheet("font-size: 13px; font-weight: bold; color: #8b949e; letter-spacing: 1px;")
        table_hdr.addWidget(table_title)
        table_hdr.addStretch()

        self.table_count_lbl = QLabel("0 attributes")
        self.table_count_lbl.setStyleSheet("font-size: 12px; color: #8b949e;")
        table_hdr.addWidget(self.table_count_lbl)
        main_layout.addLayout(table_hdr)

        # Table Widget
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "ID", "Attribute Name", "Current", "Worst", "Threshold", "Raw Value", "Status"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(True)
        main_layout.addWidget(self.table)

        # 4. Action Bar
        action_layout = QHBoxLayout()
        action_layout.setSpacing(12)

        # Eject Button (Visible only for local USB drives)
        self.eject_btn = QPushButton("⏏️ Eject USB Drive")
        self.eject_btn.setObjectName("EjectButton")
        self.eject_btn.setVisible(False)
        self.eject_btn.clicked.connect(self._on_eject_clicked)
        action_layout.addWidget(self.eject_btn)

        self.status_msg_lbl = QLabel("")
        self.status_msg_lbl.setStyleSheet("color: #8b949e; font-size: 13px;")
        action_layout.addWidget(self.status_msg_lbl)
        action_layout.addStretch()

        self.export_btn = QPushButton("📄 Export Reports...")
        self.export_btn.setObjectName("AccentButton")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._on_export_clicked)
        action_layout.addWidget(self.export_btn)

        self.scan_btn = QPushButton("🔍 Scan Now")
        self.scan_btn.setObjectName("PrimaryButton")
        self.scan_btn.setEnabled(False)
        self.scan_btn.clicked.connect(self._on_scan_clicked)
        action_layout.addWidget(self.scan_btn)

        main_layout.addLayout(action_layout)

    def display_disk(self, disk: DiskInfo):
        self.current_disk = disk
        self.scan_btn.setEnabled(True)
        self.export_btn.setEnabled(True)

        # Eject button visibility
        if disk.is_usb and not disk.is_remote:
            self.eject_btn.setVisible(True)
        else:
            self.eject_btn.setVisible(False)

        host_tag = f"[{disk.server_name}] " if disk.is_remote and disk.server_name else ""
        usb_hint = " [USB Drive]" if disk.is_usb else ""
        self.model_lbl.setText(f"{host_tag}{disk.model} ({disk.size_human}){usb_hint}")
        
        info_text = (
            f"Path: <b>{disk.device_path}</b> &nbsp;|&nbsp; "
            f"S/N: <b>{disk.serial}</b> &nbsp;|&nbsp; "
            f"Firmware: <b>{disk.firmware}</b> &nbsp;|&nbsp; "
            f"Type: <b>{disk.protocol} ({disk.rotation_rate})</b>"
        )
        self.info_lbl.setText(info_text)

        # Error Banner
        if disk.error_message:
            self.error_lbl.setText(f"Diagnostic not available: {disk.error_message}")
            self.error_banner.setVisible(True)
        else:
            self.error_banner.setVisible(False)

        # Health Badge
        self._update_health_badge(disk)

        # Telemetry Cards
        if disk.temperature_c is not None:
            temp_c = disk.temperature_c
            temp_color = "#3fb950" if temp_c < 45 else ("#d29922" if temp_c < 55 else "#f85149")
            temp_sub = "Optimal temperature" if temp_c < 45 else ("Moderate temperature" if temp_c < 55 else "High temperature warning!")
            self.temp_card.update_data(f"{temp_c} °C", temp_sub, color=temp_color)
        else:
            if not disk.attributes:
                self.temp_card.update_data("---", "Click 'Scan Now'")
            else:
                sub = "Not supported by USB bridge" if disk.is_usb else "No SMART thermal sensor"
                self.temp_card.update_data("N/A", sub)

        if disk.power_on_hours is not None:
            hours = disk.power_on_hours
            if hours >= 8760:
                years = hours / 8760.0
                self.hours_card.update_data(f"{hours:,} hrs", f"Approx. {years:.1f} years power-on")
            elif hours >= 24:
                days = hours / 24.0
                self.hours_card.update_data(f"{hours:,} hrs", f"Approx. {days:.1f} days power-on")
            else:
                self.hours_card.update_data(f"{hours} hrs", "Recent usage")
        else:
            if not disk.attributes:
                self.hours_card.update_data("---", "Click 'Scan Now'")
            else:
                sub = "Not supported by USB bridge" if disk.is_usb else "Not available"
                self.hours_card.update_data("N/A", sub)

        if disk.power_cycles is not None:
            self.cycles_card.update_data(f"{disk.power_cycles:,}", "Recorded power-on cycles")
        else:
            if not disk.attributes:
                self.cycles_card.update_data("---", "Click 'Scan Now'")
            else:
                sub = "Not supported by USB bridge" if disk.is_usb else "Not available"
                self.cycles_card.update_data("N/A", sub)

        # Table
        self._populate_table(disk)

    def _update_health_badge(self, disk: DiskInfo):
        if not disk.attributes and disk.health_status == HealthStatus.UNKNOWN:
            self.health_badge.setText("⚪  UNSCANNED")
            self.health_badge.setStyleSheet(
                "background-color: #21262d; color: #8b949e; border: 1px solid #30363d; "
                "font-weight: bold; font-size: 13px; padding: 6px 14px; border-radius: 12px;"
            )
            return

        status = disk.health_status
        if status == HealthStatus.HEALTHY:
            self.health_badge.setText("🟢  PASSED (HEALTHY)")
            self.health_badge.setStyleSheet(
                "background-color: #04260f; color: #3fb950; border: 1px solid #238636; "
                "font-weight: bold; font-size: 13px; padding: 6px 14px; border-radius: 12px;"
            )
        elif status == HealthStatus.WARNING:
            self.health_badge.setText(f"🟡  {disk.health_summary.upper()}")
            self.health_badge.setStyleSheet(
                "background-color: #382402; color: #e3b341; border: 1px solid #9e6a03; "
                "font-weight: bold; font-size: 13px; padding: 6px 14px; border-radius: 12px;"
            )
        elif status == HealthStatus.FAILED:
            self.health_badge.setText(f"🔴  {disk.health_summary.upper()}")
            self.health_badge.setStyleSheet(
                "background-color: #490202; color: #ff7b72; border: 1px solid #da3633; "
                "font-weight: bold; font-size: 13px; padding: 6px 14px; border-radius: 12px;"
            )
        else:
            self.health_badge.setText(f"⚪  {disk.health_summary.upper()}")
            self.health_badge.setStyleSheet(
                "background-color: #21262d; color: #8b949e; border: 1px solid #30363d; "
                "font-weight: bold; font-size: 13px; padding: 6px 14px; border-radius: 12px;"
            )

    def _populate_table(self, disk: DiskInfo):
        self.table.setRowCount(0)
        attrs = disk.attributes
        self.table_count_lbl.setText(f"{len(attrs)} attributes")

        if not attrs:
            return

        self.table.setRowCount(len(attrs))
        for row, attr in enumerate(attrs):
            id_item = QTableWidgetItem(f"{attr.id:03d}" if attr.id < 1000 else str(attr.id))
            id_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 0, id_item)

            name_item = QTableWidgetItem(attr.name)
            if attr.description:
                name_item.setToolTip(attr.description)
            self.table.setItem(row, 1, name_item)

            cur_item = QTableWidgetItem(str(attr.current))
            cur_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 2, cur_item)

            worst_item = QTableWidgetItem(str(attr.worst))
            worst_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 3, worst_item)

            thr_item = QTableWidgetItem(str(attr.threshold))
            thr_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 4, thr_item)

            raw_item = QTableWidgetItem(str(attr.raw))
            raw_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.table.setItem(row, 5, raw_item)

            st_item = QTableWidgetItem(f"{attr.status_type.icon} {attr.status}")
            st_item.setTextAlignment(Qt.AlignCenter)
            if attr.status_type == HealthStatus.FAILED:
                st_item.setForeground(QColor("#f85149"))
            elif attr.status_type == HealthStatus.WARNING:
                st_item.setForeground(QColor("#e3b341"))
            else:
                st_item.setForeground(QColor("#3fb950"))
            self.table.setItem(row, 6, st_item)

        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)

    def set_loading(self, is_loading: bool, message: str = ""):
        self.scan_btn.setEnabled(not is_loading)
        if is_loading:
            self.scan_btn.setText("⏳ Scanning...")
            self.status_msg_lbl.setText(f"⏳ {message}")
        else:
            self.scan_btn.setText("🔍 Scan Now")
            self.status_msg_lbl.setText(message)

    def _on_scan_clicked(self):
        if self.current_disk:
            self.scan_requested.emit(self.current_disk)

    def _on_export_clicked(self):
        if self.current_disk:
            self.export_requested.emit(self.current_disk)

    def _on_eject_clicked(self):
        if self.current_disk:
            self.eject_requested.emit(self.current_disk.device_path)
