from html import escape
from typing import Optional
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame,
    QScrollArea, QSizePolicy, QTabWidget, QMenu
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from ..core.models import DiskInfo, HealthStatus
from ..core.raid import VIRTUAL_SUMMARY
from ..core.controller import is_controller_disk
from .history_tab import HistoryTab
from .scan_state import SCAN_NOW_TEXT, scan_action_text, format_last_scanned


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

    scan_requested = Signal(object)         # Emits current DiskInfo
    export_requested = Signal(object)       # Emits current DiskInfo
    eject_requested = Signal(str)           # Emits device_path for local USB drive
    self_test_requested = Signal(object, str)  # Emits (DiskInfo, test_type: "short"|"long")
    compat_mode_requested = Signal(object)  # Emits DiskInfo whose USB adapter hides SMART (UAS)
    back_to_controller_requested = Signal(object)  # Emits DiskInfo: open its RAID controller view

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_disk: Optional[DiskInfo] = None
        self._compat_busy = False
        self._setup_ui()

    def _setup_ui(self):
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        self.tabs = QTabWidget()
        outer_layout.addWidget(self.tabs)

        diagnostics_widget = QWidget()
        self.tabs.addTab(diagnostics_widget, "Diagnostics")

        self.history_tab = HistoryTab()
        self.tabs.addTab(self.history_tab, "History")

        main_layout = QVBoxLayout(diagnostics_widget)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(14)

        # "← Back to controller" (only for disks that belong to a RAID controller)
        self.back_btn = QPushButton("← Back to controller")
        self.back_btn.setFlat(True)
        self.back_btn.setCursor(Qt.PointingHandCursor)
        self.back_btn.setVisible(False)
        self.back_btn.clicked.connect(self._on_back_clicked)
        back_row = QHBoxLayout()
        back_row.addWidget(self.back_btn)
        back_row.addStretch()
        main_layout.addLayout(back_row)

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

        self.last_scanned_lbl = QLabel("")
        self.last_scanned_lbl.setStyleSheet("font-size: 13px; color: #8b949e; background: transparent;")
        top_row.addWidget(self.last_scanned_lbl)

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

        # Last Self-Test result row (filled after a scan; see 'Self-Test' button)
        self.self_test_status_lbl = QLabel("Last Self-Test: —")
        self.self_test_status_lbl.setStyleSheet("font-size: 13px; color: #8b949e; font-weight: 600;")
        hdr_layout.addWidget(self.self_test_status_lbl)

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

        # Shown on a reading taken in USB compatibility (Bulk-Only) mode
        self.compat_note_banner = QFrame()
        self.compat_note_banner.setObjectName("SuccessBanner")
        self.compat_note_banner.setVisible(False)
        cn_layout = QHBoxLayout(self.compat_note_banner)
        cn_layout.setContentsMargins(12, 8, 12, 8)
        cn_icon = QLabel("🔌")
        cn_icon.setStyleSheet("font-size: 16px; background: transparent;")
        cn_layout.addWidget(cn_icon)
        self.compat_note_lbl = QLabel("")
        self.compat_note_lbl.setWordWrap(True)
        self.compat_note_lbl.setStyleSheet("color: #56d364; font-size: 13px; font-weight: 500; background: transparent;")
        cn_layout.addWidget(self.compat_note_lbl, 1)
        main_layout.addWidget(self.compat_note_banner)

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

        # USB adapter hides SMART in UAS mode: explanation + opt-in compatibility read,
        # shown in place of the (empty) attribute table.
        self.compat_notice = QFrame()
        self.compat_notice.setObjectName("WarningBanner")
        self.compat_notice.setVisible(False)
        notice_layout = QVBoxLayout(self.compat_notice)
        notice_layout.setContentsMargins(16, 14, 16, 14)
        notice_layout.setSpacing(10)
        notice_title = QLabel("🔌  The USB adapter is hiding this drive's S.M.A.R.T. data")
        notice_title.setStyleSheet("font-size: 15px; font-weight: bold; color: #e3b341; background: transparent;")
        notice_layout.addWidget(notice_title)
        self.compat_notice_lbl = QLabel("")
        self.compat_notice_lbl.setWordWrap(True)
        self.compat_notice_lbl.setTextFormat(Qt.RichText)
        self.compat_notice_lbl.setStyleSheet("font-size: 13px; color: #c9d1d9; background: transparent;")
        notice_layout.addWidget(self.compat_notice_lbl)
        notice_btn_row = QHBoxLayout()
        self.compat_btn = QPushButton("Read SMART in compatibility mode…")
        self.compat_btn.setObjectName("AccentButton")
        self.compat_btn.clicked.connect(self._on_compat_clicked)
        notice_btn_row.addWidget(self.compat_btn)
        self.compat_progress_lbl = QLabel("")
        self.compat_progress_lbl.setWordWrap(True)
        self.compat_progress_lbl.setStyleSheet("font-size: 13px; color: #e3b341; background: transparent;")
        notice_btn_row.addWidget(self.compat_progress_lbl, 1)
        notice_layout.addLayout(notice_btn_row)
        notice_layout.addStretch()
        main_layout.addWidget(self.compat_notice, 1)

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

        self.self_test_btn = QPushButton("🩺 Self-Test ▾")
        self.self_test_btn.setEnabled(False)
        self._self_test_menu = QMenu(self.self_test_btn)
        self._self_test_menu.addAction("Short (~2 min)", lambda: self._on_self_test_clicked("short"))
        self._self_test_menu.addAction("Long / Extended (can take hours)", lambda: self._on_self_test_clicked("long"))
        self.self_test_btn.setMenu(self._self_test_menu)
        action_layout.addWidget(self.self_test_btn)

        self.scan_btn = QPushButton(SCAN_NOW_TEXT)
        self.scan_btn.setObjectName("PrimaryButton")
        self.scan_btn.setEnabled(False)
        self.scan_btn.clicked.connect(self._on_scan_clicked)
        action_layout.addWidget(self.scan_btn)

        main_layout.addLayout(action_layout)

    def display_disk(self, disk: DiskInfo):
        self.current_disk = disk
        self.back_btn.setVisible(is_controller_disk(disk))
        if disk.controller:
            self.back_btn.setToolTip(disk.controller)
        self.scan_btn.setEnabled(not self._compat_busy)
        self.export_btn.setEnabled(True)
        self.self_test_btn.setEnabled(not self._compat_busy)
        self.scan_btn.setVisible(not disk.is_virtual)
        self.self_test_btn.setVisible(not disk.is_virtual)
        self.scan_btn.setText(scan_action_text(disk))
        self.last_scanned_lbl.setText(format_last_scanned(disk.last_scanned))

        # Eject button visibility
        if disk.is_usb and not disk.is_remote:
            self.eject_btn.setVisible(True)
        else:
            self.eject_btn.setVisible(False)

        host_tag = f"[{disk.server_name}] " if disk.is_remote and disk.server_name else ""
        usb_hint = " [USB Drive]" if disk.is_usb else ""
        self.model_lbl.setText(f"{host_tag}{disk.model} ({disk.size_human}){usb_hint}")
        
        if disk.is_virtual:
            self._display_virtual(disk)
            return

        path = escape(disk.device_path)
        if disk.uid != disk.device_path:
            path += f" -d {escape(disk.smart_dev_type)}"
        info_text = (
            f"Path: <b>{path}</b> &nbsp;|&nbsp; "
            f"S/N: <b>{escape(disk.serial)}</b> &nbsp;|&nbsp; "
            f"Firmware: <b>{escape(disk.firmware)}</b> &nbsp;|&nbsp; "
            f"Type: <b>{escape(disk.protocol)} ({escape(disk.rotation_rate)})</b>"
        )
        extra = []
        if disk.vendor:
            extra.append(f"Vendor: <b>{escape(disk.vendor)}</b>")
        if disk.transport:
            extra.append(f"Transport: <b>{escape(disk.transport)}</b>")
        if disk.form_factor:
            extra.append(f"Form factor: <b>{escape(disk.form_factor)}</b>")
        if disk.controller:
            slot = f", slot {disk.slot}" if disk.slot is not None else ""
            extra.append(f"Controller: <b>{escape(disk.controller)}{slot}</b>")
        if extra:
            info_text += "<br>" + " &nbsp;|&nbsp; ".join(extra)
        if disk.raid_detail:
            info_text += f"<br>Vendor tool: <b>{escape(disk.raid_detail)}</b>"
        self.info_lbl.setText(info_text)

        # Last Self-Test result (set by 'Self-Test' button + a later 'Refresh')
        if disk.self_test_status:
            status_lower = disk.self_test_status.lower()
            if "progress" in status_lower or "remaining" in status_lower:
                color = "#e3b341"
            elif "without error" in status_lower or "completed" in status_lower or "passed" in status_lower:
                color = "#3fb950"
            elif "fail" in status_lower or "error" in status_lower or "abort" in status_lower:
                color = "#f85149"
            else:
                color = "#8b949e"
            self.self_test_status_lbl.setText(f"Last Self-Test: {disk.self_test_status}")
            self.self_test_status_lbl.setStyleSheet(f"font-size: 13px; color: {color}; font-weight: 600;")
        else:
            self.self_test_status_lbl.setText("Last Self-Test: — (use the 'Self-Test' button below, then 'Refresh')")
            self.self_test_status_lbl.setStyleSheet("font-size: 13px; color: #8b949e; font-weight: 600;")

        # USB compatibility mode: note on a Bulk-Only reading / notice when SMART is hidden
        candidate = disk.uas_candidate if not disk.compat_read_note else None
        if disk.compat_read_note:
            self.compat_note_lbl.setText(disk.compat_read_note)
        self.compat_note_banner.setVisible(bool(disk.compat_read_note))
        if candidate is not None:
            self.compat_notice_lbl.setText(
                f"Your drive answered the health check, but the USB adapter it is connected through "
                f"(<b>{escape(candidate.name)}</b>, USB ID {candidate.usb_id}) does not pass detailed "
                f"S.M.A.R.T. requests through while it runs in its fast <b>UAS</b> mode. That is why "
                f"temperature, power-on hours and the attribute table are missing. "
                f"<b>Nothing is wrong with the drive.</b><br><br>"
                f"SmartLinux can briefly switch the adapter to its slower compatibility "
                f"(Bulk-Only) mode, read the full data and switch it back. You will see exactly "
                f"what happens before anything is done."
            )
        self.compat_notice.setVisible(candidate is not None)
        self.table.setVisible(candidate is None)
        if not self._compat_busy:
            self.compat_progress_lbl.setText("")

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
                sub = self._missing_metric_hint(disk, "No SMART thermal sensor")
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
                sub = self._missing_metric_hint(disk, "Not available")
                self.hours_card.update_data("N/A", sub)

        if disk.power_cycles is not None:
            cycles_sub = "Start/stop cycles" if disk.transport else "Recorded power-on cycles"
            self.cycles_card.update_data(f"{disk.power_cycles:,}", cycles_sub)
        else:
            if not disk.attributes:
                self.cycles_card.update_data("---", "Click 'Scan Now'")
            else:
                sub = self._missing_metric_hint(disk, "Not available")
                self.cycles_card.update_data("N/A", sub)

        # Table
        self._populate_table(disk)

        # History tab (reads local JSONL history for this disk's serial)
        self.history_tab.load_history(disk)

    def _display_virtual(self, disk: DiskInfo):
        """RAID volume: size and controller only. No scan, no self-test, no invented health."""
        self.scan_btn.setEnabled(False)
        self.self_test_btn.setEnabled(False)
        self.last_scanned_lbl.setText("")
        controller = escape(disk.controller or "a RAID controller")
        self.info_lbl.setText(
            f"Path: <b>{escape(disk.device_path)}</b> &nbsp;|&nbsp; Size: <b>{escape(disk.size_human)}</b>"
            f" &nbsp;|&nbsp; Controller: <b>{controller}</b><br>"
            f"Logical volume of the controller: it has no S.M.A.R.T. data of its own. "
            f"Check the physical disks listed under the controller."
            + (f"<br>Vendor tool: <b>{escape(disk.raid_detail)}</b>" if disk.raid_detail else "")
        )
        self.self_test_status_lbl.setText("Last Self-Test: not applicable (virtual drive)")
        self.self_test_status_lbl.setStyleSheet("font-size: 13px; color: #8b949e; font-weight: 600;")
        self.compat_note_banner.setVisible(False)
        self.compat_notice.setVisible(False)
        self.table.setVisible(True)
        self.error_banner.setVisible(False)
        if disk.raid_health and disk.raid_volume:
            # state reported by the controller itself (vendor tool), not invented
            colors = {HealthStatus.HEALTHY: ("#04260f", "#3fb950", "#238636"),
                      HealthStatus.WARNING: ("#382402", "#e3b341", "#9e6a03"),
                      HealthStatus.FAILED: ("#490202", "#ff7b72", "#da3633")}
            bg, fg, border = colors.get(disk.raid_health, ("#21262d", "#8b949e", "#30363d"))
            self.health_badge.setText(f"{disk.raid_health.icon}  RAID VOLUME: {disk.raid_volume.upper()}")
            self.health_badge.setStyleSheet(
                f"background-color: {bg}; color: {fg}; border: 1px solid {border}; "
                "font-weight: bold; font-size: 13px; padding: 6px 14px; border-radius: 12px;"
            )
        else:
            self.health_badge.setText(f"▫️  {VIRTUAL_SUMMARY.upper()}")
            self.health_badge.setStyleSheet(
                "background-color: #21262d; color: #8b949e; border: 1px solid #30363d; "
                "font-weight: bold; font-size: 13px; padding: 6px 14px; border-radius: 12px;"
            )
        for card in (self.temp_card, self.hours_card, self.cycles_card):
            card.update_data("—", "Not applicable (virtual drive)")
        self._populate_table(disk)
        self.history_tab.load_history(disk)

    @staticmethod
    def _missing_metric_hint(disk: DiskInfo, fallback: str) -> str:
        if disk.uas_candidate is not None and not disk.compat_read_note:
            return "Hidden by the USB adapter (UAS mode)"
        return "Not supported by USB bridge" if disk.is_usb else fallback

    def set_compat_busy(self, busy: bool, message: str = ""):
        """Locks the panel while a compatibility-mode read is running."""
        self._compat_busy = busy
        self.compat_btn.setEnabled(not busy)
        self.compat_btn.setText("⏳ Working…" if busy else "Read SMART in compatibility mode…")
        self.compat_progress_lbl.setText(message)
        for btn in (self.scan_btn, self.self_test_btn, self.eject_btn):
            btn.setEnabled(not busy and self.current_disk is not None and not self.current_disk.is_virtual)

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
            self.scan_btn.setText(scan_action_text(self.current_disk))
            self.status_msg_lbl.setText(message)

    def _on_back_clicked(self):
        if self.current_disk:
            self.back_to_controller_requested.emit(self.current_disk)

    def _on_scan_clicked(self):
        if self.current_disk:
            self.scan_requested.emit(self.current_disk)

    def _on_export_clicked(self):
        if self.current_disk:
            self.export_requested.emit(self.current_disk)

    def _on_eject_clicked(self):
        if self.current_disk:
            self.eject_requested.emit(self.current_disk.device_path)

    def _on_compat_clicked(self):
        if self.current_disk and self.current_disk.uas_candidate is not None:
            self.compat_mode_requested.emit(self.current_disk)

    def _on_self_test_clicked(self, test_type: str):
        if self.current_disk:
            self.self_test_requested.emit(self.current_disk, test_type)
