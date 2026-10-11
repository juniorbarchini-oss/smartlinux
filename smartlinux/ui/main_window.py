import os
import subprocess
from typing import List, Optional, Set, Dict, Tuple
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QLabel, QPushButton, QMessageBox, QFileDialog, QStatusBar
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer

from ..core.models import DiskInfo, ServerConfig, HealthStatus
from ..core.detector import LocalDiskDetector
from ..core.ssh_client import create_ssh_client, AutoSSHClient, host_key_fingerprint, trust_host_key, known_hosts_label
from ..core.config_manager import ConfigManager
from ..core.exporter import ReportExporter
from ..core.watcher import DeviceHotplugWatcher
from ..core.history_manager import HistoryManager
from ..core import usb_compat

from .sidebar import SidebarWidget
from .detail_panel import DetailPanel
from .server_dialog import ServerDialog
from .export_dialog import ExportDialog
from .setup_dialog import SetupDependenciesDialog
from .compat_dialog import CompatModeDialog


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


class CompatModeWorker(QThread):
    """Runs the USB compatibility-mode read (unmount -> Bulk-Only -> read -> restore -> remount)."""
    progress = Signal(str)
    result_ready = Signal(object)
    error_occurred = Signal(str)

    def __init__(self, candidate):
        super().__init__()
        self.candidate = candidate

    def run(self):
        try:
            res = usb_compat.run_compat_read(
                self.candidate, LocalDiskDetector.read_drive_smart, progress=self.progress.emit
            )
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
        self._ssh_clients: Dict[str, AutoSSHClient] = {}
        self._all_known_disks: Dict[str, DiskInfo] = {}
        self._last_preflight_issue: str = ""
        self._compat_worker: Optional[CompatModeWorker] = None

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
        self.detail_panel.self_test_requested.connect(self._run_self_test)
        self.detail_panel.compat_mode_requested.connect(self._on_compat_mode_requested)
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
            self._hotplug_watcher = DeviceHotplugWatcher()
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
                    d.uas_candidate = cached.uas_candidate
                    if cached.compat_read_note:
                        d.compat_read_note = cached.compat_read_note
                        d.model, d.serial, d.firmware = cached.model, cached.serial, cached.firmware
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
        client = create_ssh_client(srv)
        self._ssh_clients[srv.id] = client
        ok, msg = client.connect(timeout=8)
        if not ok:
            raise Exception(msg)

        drives = client.discover_remote_drives()
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
        self.status_bar.showMessage(f"Failed to connect to server: {error_msg.splitlines()[0]}")

        srv = next((s for s in self._active_servers if s.id == server_id), None)
        srv_name = srv.name if srv else "Server"

        if "SECURITY ALERT" in error_msg or "Host Key Mismatch" in error_msg:
            QMessageBox.critical(
                self,
                f"SSH Security Alert: {srv_name}",
                f"{error_msg}\n\nSmartLinux aborted the connection to protect against potential credential theft or MITM attacks."
            )
        elif "Host Key Not Verified" in error_msg or "known_hosts" in error_msg:
            client = self._ssh_clients.get(server_id)
            if client and client.last_untrusted_host_key:
                host, key = client.last_untrusted_host_key
                fp = host_key_fingerprint(key)
                where = known_hosts_label(key)
                reply = QMessageBox.question(
                    self,
                    f"Trust Remote Host Key: {srv_name}?",
                    f"Host key for '{host}' is not present in the trusted host keys.\n\n"
                    f"Key Type: {key.get_name()}\n"
                    f"Fingerprint: {fp}\n\n"
                    f"Do you want to trust this host and save its key to {where}?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No
                )
                if reply == QMessageBox.Yes:
                    if trust_host_key(host, key):
                        self.status_bar.showMessage(f"Host key saved for {srv_name}. Retrying connection...")
                        self._on_refresh_server(server_id)
                        return
                    else:
                        QMessageBox.warning(self, "Error", "Failed to write key to ~/.ssh/known_hosts.")
            else:
                QMessageBox.warning(
                    self,
                    f"Host Key Verification Failed: {srv_name}",
                    f"Connection to '{srv_name}' was blocked by host key verification:\n\n{error_msg}"
                )
        else:
            QMessageBox.warning(
                self,
                f"Connection Error: {srv_name}",
                f"Failed to connect to '{srv_name}':\n\n{error_msg}"
            )

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
            local = LocalDiskDetector.read_drive_smart(disk.device_path)
            try:
                local.uas_candidate = usb_compat.detect_uas_smart_block(local)
            except Exception as e:  # detection is best-effort and never blocks a scan
                print(f"USB compatibility-mode detection failed: {e}")
            return local
        else:
            server_id = disk.server_id
            srv = next((s for s in self._active_servers if s.id == server_id), None)
            if not srv:
                raise Exception("Server not found in configuration.")
            
            client = self._ssh_clients.get(server_id)
            if not client or not client.is_connected():
                client = create_ssh_client(srv)
                ok, msg = client.connect(timeout=8)
                if not ok:
                    raise Exception(msg)
                self._ssh_clients[server_id] = client

            return client.read_remote_drive_smart(disk.device_path)

    def _on_single_disk_scanned(self, updated_disk: DiskInfo):
        key = f"{updated_disk.server_id or 'local'}_{updated_disk.device_path}"
        self._all_known_disks[key] = updated_disk
        HistoryManager.append_snapshot(updated_disk)

        self.detail_panel.display_disk(updated_disk)
        self.sidebar.update_disk_info(updated_disk)
        self.detail_panel.set_loading(False, "SMART read complete.")
        self.status_bar.showMessage(f"Diagnostics complete: {updated_disk.model} [{updated_disk.health_status.label}].")

    # ---------------- NATIVE SELF-TEST (fire-and-forget, checked via a later scan) ----------------

    def _run_self_test(self, disk: DiskInfo, test_type: str):
        label = "short (~2 min)" if test_type == "short" else "long/extended (can take hours)"
        self.status_bar.showMessage(f"Starting {label} self-test on {disk.device_path}...")
        self._start_worker(
            self._do_run_self_test,
            on_result=lambda res: self._on_self_test_result(disk, test_type, res),
            on_error=lambda err: self._on_self_test_error(disk, err),
            disk=disk,
            test_type=test_type
        )

    def _do_run_self_test(self, disk: DiskInfo, test_type: str) -> Tuple[bool, str]:
        if not disk.is_remote:
            return LocalDiskDetector.run_self_test(disk.device_path, test_type)

        server_id = disk.server_id
        srv = next((s for s in self._active_servers if s.id == server_id), None)
        if not srv:
            raise Exception("Server not found in configuration.")

        client = self._ssh_clients.get(server_id)
        if not client or not client.is_connected():
            client = create_ssh_client(srv)
            ok, msg = client.connect(timeout=8)
            if not ok:
                raise Exception(msg)
            self._ssh_clients[server_id] = client

        return client.run_self_test(disk.device_path, test_type)

    def _on_self_test_result(self, disk: DiskInfo, test_type: str, result):
        ok, msg = result
        if ok:
            self.status_bar.showMessage(f"Self-test ({test_type}) started on {disk.device_path}.")
            QMessageBox.information(
                self, "Self-Test Started",
                f"Self-test ({test_type}) queued on {disk.model} ({disk.device_path}).\n\n"
                f"{msg}\n\n"
                f"Wait for it to finish, then click 'Scan Now' again on this drive — "
                f"the result will show up as 'Last Self-Test' right under the drive name, at the top of the Diagnostics tab."
            )
        else:
            self._on_self_test_error(disk, msg)

    def _on_self_test_error(self, disk: DiskInfo, error_msg: str):
        self.status_bar.showMessage(f"Failed to start self-test on {disk.device_path}: {error_msg}")
        QMessageBox.warning(self, "Self-Test Failed", f"Could not start self-test:\n\n{error_msg}")

    def _on_single_disk_error(self, error_msg: str):
        self.detail_panel.set_loading(False, f"Error: {error_msg}")
        self.status_bar.showMessage(f"Failed to read disk: {error_msg}")
        QMessageBox.warning(self, "Read Error", f"Unable to read device telemetry:\n\n{error_msg}")

    # ---------------- USB COMPATIBILITY MODE (UAS -> Bulk-Only, explicit consent) ----------------

    def _on_compat_mode_requested(self, disk: DiskInfo):
        candidate = disk.uas_candidate
        if candidate is None or self._compat_worker is not None:
            return
        self.detail_panel.set_compat_busy(True, "Checking the drive...")
        self._start_worker(
            usb_compat.preflight,
            on_result=lambda res: self._on_compat_preflight(disk, candidate, res),
            on_error=self._on_compat_preflight_error,
            candidate=candidate
        )

    def _on_compat_preflight_error(self, error_msg: str):
        self.detail_panel.set_compat_busy(False)
        QMessageBox.warning(self, "Compatibility Mode", f"Could not prepare compatibility mode:\n\n{error_msg}")

    def _on_compat_preflight(self, disk: DiskInfo, candidate, result):
        volumes, problems = result
        self.detail_panel.set_compat_busy(False)
        if problems:
            QMessageBox.warning(
                self, "Compatibility Mode Not Possible",
                "SmartLinux will not switch this drive to compatibility mode:\n\n"
                + "\n".join(f"• {p}" for p in problems)
            )
            return
        if CompatModeDialog(candidate, volumes, self).exec() != CompatModeDialog.Accepted:
            return  # Cancel: nothing was done

        old_key = f"local_{disk.device_path}"
        self.detail_panel.set_compat_busy(True, "Starting...")
        self.status_bar.showMessage(f"Reading {disk.device_path} in USB compatibility mode...")
        worker = CompatModeWorker(candidate)
        self._compat_worker = worker
        self._active_workers.add(worker)
        worker.progress.connect(self._on_compat_progress)
        worker.result_ready.connect(lambda res: self._on_compat_done(old_key, res))
        worker.error_occurred.connect(self._on_compat_error)

        def _cleanup():
            self._active_workers.discard(worker)
            self._compat_worker = None
            worker.deleteLater()

        worker.finished.connect(_cleanup)
        worker.start()

    def _on_compat_progress(self, message: str):
        self.detail_panel.set_compat_busy(True, f"⏳ {message}")
        self.status_bar.showMessage(message)

    def _on_compat_done(self, old_key: str, result):
        disk = result.disk
        self.detail_panel.set_compat_busy(False)
        self._all_known_disks.pop(old_key, None)
        self._all_known_disks[f"local_{disk.device_path}"] = disk
        HistoryManager.append_snapshot(disk)
        self.detail_panel.display_disk(disk)
        self.status_bar.showMessage(f"Compatibility-mode read complete: {disk.model} [{disk.health_status.label}].")
        self._discover_local_drives()

        notes = []
        if not result.restored:
            notes.append("The adapter could not be switched back to normal (UAS) mode"
                         + (f" ({result.restore_error})" if result.restore_error else "")
                         + ". Unplug and replug the drive (or reboot) to restore it.")
        notes.extend(result.remount_warnings)
        if notes:
            QMessageBox.warning(self, "Compatibility Mode", "\n\n".join(notes))

    def _on_compat_error(self, error_msg: str):
        self.detail_panel.set_compat_busy(False)
        self.status_bar.showMessage("Compatibility-mode read failed.")
        self._discover_local_drives()
        QMessageBox.warning(self, "Compatibility Mode Failed", error_msg)

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
        if self._compat_worker is not None and self._compat_worker.isRunning():
            reply = QMessageBox.question(
                self, "Compatibility Mode Running",
                "A compatibility-mode read is still running. If you quit now, the USB adapter may "
                "stay in compatibility mode until you reboot or unplug and replug the drive.\n\n"
                "Quit anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
        if self._hotplug_watcher:
            try:
                self._hotplug_watcher.stop()
                self._hotplug_watcher.wait(2000)
            except Exception:
                pass
        for worker in list(self._active_workers):
            try:
                worker.quit()
                worker.wait(2000)
            except Exception:
                pass
        super().closeEvent(event)
