import os
import subprocess
from typing import List, Optional, Set, Dict
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QLabel, QPushButton, QMessageBox, QFileDialog, QStatusBar
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer

from ..core.models import DiskInfo, ServerConfig, HealthStatus
from ..core.detector import LocalDiskDetector
from ..core.ssh_client import RemoteSSHClient
from ..core.config_manager import ConfigManager
from ..core.exporter import ReportExporter
from ..core.watcher import DeviceHotplugWatcher

from .sidebar import SidebarWidget
from .detail_panel import DetailPanel
from .server_dialog import ServerDialog
from .export_dialog import ExportDialog
from .setup_dialog import SetupDependenciesDialog


class TaskWorker(QThread):
    """Executes a background function safely without freezing Qt UI."""
    result_ready = Signal(object)
    error_occurred = Signal(str)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs

    def run(self):
        try:
            res = self.fn(*self.args, **self.kwargs)
            self.result_ready.emit(res)
        except Exception as e:
            self.error_occurred.emit(str(e))


class MainWindow(QMainWindow):
    """Main Application Window for SmartLinux."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("SmartLinux - S.M.A.R.T. Disk Health Diagnostics")
        self.resize(1180, 750)
        self.setMinimumSize(900, 580)

        # Worker & Disk tracking
        self._active_workers: Set[QThread] = set()
        self._active_servers: List[ServerConfig] = []
        self._ssh_clients: Dict[str, RemoteSSHClient] = {}
        self._all_known_disks: Dict[str, DiskInfo] = {}
        self._last_preflight_issue: str = ""

        # Hotplug auto-detection watcher
        self._hotplug_watcher: Optional[DeviceHotplugWatcher] = None
        self._hotplug_timer = QTimer(self)
        self._hotplug_timer.setSingleShot(True)
        self._hotplug_timer.setInterval(400)
        self._hotplug_timer.timeout.connect(self._discover_local_drives)

        self._setup_ui()
        self._check_smartctl_preflight()
        self._load_saved_servers()
        self._discover_local_drives()
        self._start_hotplug_watcher()

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        # 1. Alert Banner for Permissions / Missing Tools
        self.banner_widget = QWidget()
        self.banner_widget.setObjectName("WarningBanner")
        self.banner_widget.setVisible(False)
        banner_layout = QHBoxLayout(self.banner_widget)
        banner_layout.setContentsMargins(14, 10, 14, 10)

        self.banner_icon = QLabel("⚠️")
        self.banner_icon.setStyleSheet("font-size: 18px;")
        banner_layout.addWidget(self.banner_icon)

        self.banner_text = QLabel("")
        self.banner_text.setWordWrap(True)
        self.banner_text.setStyleSheet("font-size: 13px; color: #f0883e; font-weight: 500;")
        banner_layout.addWidget(self.banner_text, 1)

        self.banner_fix_btn = QPushButton("⚡ Auto-Configure")
        self.banner_fix_btn.setStyleSheet("background-color: #238636; color: white; font-weight: bold; padding: 4px 10px; font-size: 12px;")
        self.banner_fix_btn.clicked.connect(self._on_auto_fix_clicked)
        banner_layout.addWidget(self.banner_fix_btn)

        self.banner_dismiss_btn = QPushButton("✕")
        self.banner_dismiss_btn.setFixedSize(26, 26)
        self.banner_dismiss_btn.setStyleSheet("background: transparent; border: none; font-weight: bold; color: #f0883e; font-size: 14px;")
        self.banner_dismiss_btn.clicked.connect(lambda: self.banner_widget.setVisible(False))
        banner_layout.addWidget(self.banner_dismiss_btn)

        main_layout.addWidget(self.banner_widget)

        # 2. Main Splitter (Sidebar + Detail Panel)
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(3)

        self.sidebar = SidebarWidget()
        self.sidebar.disk_selected.connect(self._on_disk_selected_in_sidebar)
        self.sidebar.server_add_requested.connect(self._on_add_server)
        self.sidebar.server_remove_requested.connect(self._on_remove_server)
        self.sidebar.server_refresh_requested.connect(self._on_refresh_server)
        self.sidebar.local_refresh_requested.connect(self._discover_local_drives)
        self.sidebar.eject_requested.connect(self._eject_local_disk)
        splitter.addWidget(self.sidebar)

        self.detail_panel = DetailPanel()
        self.detail_panel.setObjectName("DetailContainer")
        self.detail_panel.scan_requested.connect(self._scan_single_disk)
        self.detail_panel.export_requested.connect(self._open_export_dialog)
        self.detail_panel.eject_requested.connect(self._eject_local_disk)
        splitter.addWidget(self.detail_panel)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([340, 840])

        main_layout.addWidget(splitter, 1)

        # Status Bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready. On-demand mode active (drives are not scanned until requested).")

    def _start_hotplug_watcher(self):
        try:
            self._hotplug_watcher = DeviceHotplugWatcher(self)
            self._hotplug_watcher.device_changed.connect(self._on_hotplug_event)
            self._hotplug_watcher.start()
        except Exception as e:
            print(f"Could not start hotplug watcher: {e}")

    def _on_hotplug_event(self):
        self._hotplug_timer.start()

    def _start_worker(self, fn, on_result=None, on_error=None, *args, **kwargs) -> TaskWorker:
        worker = TaskWorker(fn, *args, **kwargs)
        self._active_workers.add(worker)

        if on_result:
            worker.result_ready.connect(on_result)
        if on_error:
            worker.error_occurred.connect(on_error)

        def _cleanup():
            self._active_workers.discard(worker)
            worker.deleteLater()

        worker.finished.connect(_cleanup)
        worker.start()
        return worker

    def _show_banner(self, message: str, is_error: bool = False):
        self.banner_text.setText(message)
        if is_error:
            self.banner_widget.setObjectName("AlertBanner")
            self.banner_icon.setText("🛑")
        else:
            self.banner_widget.setObjectName("WarningBanner")
            self.banner_icon.setText("⚠️")
        self.banner_widget.style().unpolish(self.banner_widget)
        self.banner_widget.style().polish(self.banner_widget)
        self.banner_widget.setVisible(True)

    def _check_smartctl_preflight(self):
        ok, msg = LocalDiskDetector.check_smartctl_available()
        if not ok:
            self._last_preflight_issue = msg
            self._show_banner(msg, is_error=True)
        else:
            self.banner_widget.setVisible(False)

    def _on_auto_fix_clicked(self):
        dlg = SetupDependenciesDialog(self._last_preflight_issue or "Configure smartctl permissions.", self)
        if dlg.exec():
            self._check_smartctl_preflight()
            self._discover_local_drives()

    def _load_saved_servers(self):
        self._active_servers = ConfigManager.load_servers()
        self.sidebar.set_servers(self._active_servers)

    # ---------------- FAST LOCAL DRIVES DISCOVERY (ON-DEMAND) ----------------

    def _discover_local_drives(self):
        self.status_bar.showMessage("Detecting local storage devices...")
        self._start_worker(
            LocalDiskDetector.discover_drives,
            on_result=self._on_local_drives_discovered,
            on_error=lambda err: self.status_bar.showMessage(f"Error enumerating local drives: {err}")
        )

    def _on_local_drives_discovered(self, drives: List[DiskInfo]):
        updated_drives = []
        for d in drives:
            key = f"local_{d.device_path}"
            if key in self._all_known_disks:
                cached = self._all_known_disks[key]
                if cached.attributes:
                    d.attributes = cached.attributes
                    d.health_status = cached.health_status
                    d.health_summary = cached.health_summary
                    d.temperature_c = cached.temperature_c
                    d.power_on_hours = cached.power_on_hours
                    d.power_cycles = cached.power_cycles
                    d.raw_json = cached.raw_json
            self._all_known_disks[key] = d
            updated_drives.append(d)

        self.sidebar.set_local_disks(updated_drives)
        self.status_bar.showMessage(f"{len(drives)} local drive(s) detected. Select a drive and click 'Scan Now'.")
        if updated_drives and not self.detail_panel.current_disk:
            self.detail_panel.display_disk(updated_drives[0])
            if not updated_drives[0].attributes and updated_drives[0].health_status == HealthStatus.UNKNOWN:
                self._scan_single_disk(updated_drives[0])

    # ---------------- REMOTE SERVERS DISCOVERY (ON-DEMAND) ----------------

    def _on_add_server(self):
        dialog = ServerDialog(self)
        if dialog.exec():
            new_srv = dialog.server_config
            if new_srv:
                self._active_servers = ConfigManager.add_server(new_srv)
                self.sidebar.set_servers(self._active_servers)
                self._on_refresh_server(new_srv.id)

    def _on_remove_server(self, server_id: str):
        self._active_servers = ConfigManager.remove_server(server_id)
        self.sidebar.set_servers(self._active_servers)
        if server_id in self._ssh_clients:
            self._ssh_clients[server_id].disconnect()
            del self._ssh_clients[server_id]
        self._all_known_disks = {k: v for k, v in self._all_known_disks.items() if v.server_id != server_id}
        self.status_bar.showMessage("Server removed.")

    def _on_refresh_server(self, server_id: str):
        srv = next((s for s in self._active_servers if s.id == server_id), None)
        if not srv:
            return

        self.status_bar.showMessage(f"Connecting to {srv.name} ({srv.host})...")
        self._start_worker(
            self._do_discover_remote_drives,
            on_result=lambda res: self._on_remote_drives_discovered(server_id, res),
            on_error=lambda err: self._on_remote_server_error(server_id, err),
            srv=srv
        )

    def _do_discover_remote_drives(self, srv: ServerConfig) -> List[DiskInfo]:
        client = RemoteSSHClient(srv)
        ok, msg = client.connect(timeout=8)
        if not ok:
            raise Exception(msg)

        drives = client.discover_remote_drives()
        self._ssh_clients[srv.id] = client
        return drives

    def _on_remote_drives_discovered(self, server_id: str, drives: List[DiskInfo]):
        for d in drives:
            key = f"{server_id}_{d.device_path}"
            if key not in self._all_known_disks:
                self._all_known_disks[key] = d
        self.sidebar.set_server_drives(server_id, drives)
        self.status_bar.showMessage(f"Server connected: {len(drives)} drive(s) listed.")

    def _on_remote_server_error(self, server_id: str, error_msg: str):
        self.sidebar.set_server_drives(server_id, [], error=error_msg)
        self.status_bar.showMessage(f"Failed to connect to server: {error_msg}")

    # ---------------- ON-DEMAND SINGLE DISK SCAN ----------------

    def _on_disk_selected_in_sidebar(self, disk: DiskInfo):
        key = f"{disk.server_id or 'local'}_{disk.device_path}"
        current = self._all_known_disks.get(key, disk)
        self.detail_panel.display_disk(current)
        if not current.attributes and current.health_status == HealthStatus.UNKNOWN:
            self._scan_single_disk(current)

    def _scan_single_disk(self, disk: DiskInfo):
        self.detail_panel.set_loading(True, f"Reading SMART telemetry for {disk.name}...")
        self.status_bar.showMessage(f"Scanning S.M.A.R.T. for {disk.device_path}...")

        self._start_worker(
            self._do_read_single_disk,
            on_result=self._on_single_disk_scanned,
            on_error=self._on_single_disk_error,
            disk=disk
        )

    def _do_read_single_disk(self, disk: DiskInfo) -> DiskInfo:
        if not disk.is_remote:
            return LocalDiskDetector.read_drive_smart(disk.device_path)
        else:
            server_id = disk.server_id
            srv = next((s for s in self._active_servers if s.id == server_id), None)
            if not srv:
                raise Exception("Server not found in configuration.")
            
            client = self._ssh_clients.get(server_id)
            if not client or not client.is_connected():
                client = RemoteSSHClient(srv)
                ok, msg = client.connect(timeout=8)
                if not ok:
                    raise Exception(msg)
                self._ssh_clients[server_id] = client

            return client.read_remote_drive_smart(disk.device_path)

    def _on_single_disk_scanned(self, updated_disk: DiskInfo):
        key = f"{updated_disk.server_id or 'local'}_{updated_disk.device_path}"
        self._all_known_disks[key] = updated_disk

        self.detail_panel.display_disk(updated_disk)
        self.sidebar.update_disk_info(updated_disk)
        self.detail_panel.set_loading(False, "SMART read complete.")
        self.status_bar.showMessage(f"Diagnostics complete: {updated_disk.model} [{updated_disk.health_status.label}].")

    def _on_single_disk_error(self, error_msg: str):
        self.detail_panel.set_loading(False, f"Error: {error_msg}")
        self.status_bar.showMessage(f"Failed to read disk: {error_msg}")
        QMessageBox.warning(self, "Read Error", f"Unable to read device telemetry:\n\n{error_msg}")

    # ---------------- SAFE EJECT LOCAL USB DRIVE ----------------

    def _eject_local_disk(self, device_path: str):
        reply = QMessageBox.question(
            self,
            "Safely Eject Drive",
            f"Are you sure you want to unmount and safely power off drive '{device_path}'?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            ok, msg = LocalDiskDetector.eject_drive(device_path)
            if ok:
                QMessageBox.information(self, "Drive Ejected", msg)
                self._discover_local_drives()
            else:
                QMessageBox.warning(self, "Eject Failed", msg)

    # ---------------- MULTI-FORMAT EXPORT REPORT DIALOG ----------------

    def _open_export_dialog(self, disk: Optional[DiskInfo] = None):
        available_disks = list(self._all_known_disks.values())
        if not available_disks:
            if self.detail_panel.current_disk:
                available_disks = [self.detail_panel.current_disk]

        if not available_disks:
            QMessageBox.information(self, "No Drives", "No storage drives available for export.")
            return

        dialog = ExportDialog(
            available_disks=available_disks,
            selected_disk=disk or self.detail_panel.current_disk,
            parent=self
        )
        if dialog.exec():
            exported = dialog.exported_files
            if exported:
                msg_box = QMessageBox(self)
                msg_box.setWindowTitle("Reports Exported Successfully")
                files_list = "\n".join([f"• {f}" for f in exported[:4]])
                if len(exported) > 4:
                    files_list += f"\n... and {len(exported)-4} more file(s)."
                
                msg_box.setText(f"The following reports were generated successfully:\n\n{files_list}")
                btn_open_folder = msg_box.addButton("Open Folder", QMessageBox.ActionRole)
                btn_ok = msg_box.addButton("OK", QMessageBox.AcceptRole)
                msg_box.setDefaultButton(btn_ok)
                msg_box.exec()

                if msg_box.clickedButton() == btn_open_folder:
                    subprocess.Popen(["xdg-open", os.path.dirname(exported[0])])

                self.status_bar.showMessage(f"Reports exported ({len(exported)} file(s)).")

    def closeEvent(self, event):
        if self._hotplug_watcher:
            try:
                self._hotplug_watcher.stop()
                self._hotplug_watcher.wait(400)
            except Exception:
                pass
        for worker in list(self._active_workers):
            try:
                worker.quit()
                worker.wait(400)
            except Exception:
                pass
        super().closeEvent(event)
