import os
import subprocess
from typing import List, Optional, Set
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QLabel, QPushButton, QMessageBox, QFileDialog, QStatusBar
)
from PySide6.QtCore import Qt, QThread, Signal

from ..core.models import DiskInfo, ServerConfig, HealthStatus
from ..core.detector import LocalDiskDetector
from ..core.ssh_client import RemoteSSHClient
from ..core.config_manager import ConfigManager
from ..core.exporter import MarkdownExporter

from .sidebar import SidebarWidget
from .detail_panel import DetailPanel
from .server_dialog import ServerDialog


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
        self.setWindowTitle("SmartLinux - Diagnóstico S.M.A.R.T. On-Demand")
        self.resize(1100, 720)
        self.setMinimumSize(850, 550)

        # Worker management to prevent GC segfaults
        self._active_workers: Set[QThread] = set()
        self._active_servers: List[ServerConfig] = []
        self._ssh_clients: dict = {}

        self._setup_ui()
        self._check_smartctl_preflight()
        self._load_saved_servers()
        self._scan_local_drives()

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        # 1. Alert Banner for Permissions / Missing Tools
        self.banner_widget = QWidget()
        self.banner_widget.setObjectName("WarningBanner")
        self.banner_widget.setVisible(False)
        banner_layout = QHBoxLayout(self.banner_widget)
        banner_layout.setContentsMargins(12, 8, 12, 8)

        self.banner_icon = QLabel("⚠️")
        self.banner_icon.setStyleSheet("font-size: 16px;")
        banner_layout.addWidget(self.banner_icon)

        self.banner_text = QLabel("")
        self.banner_text.setWordWrap(True)
        self.banner_text.setStyleSheet("font-size: 12px; color: #f0883e; font-weight: 500;")
        banner_layout.addWidget(self.banner_text, 1)

        self.banner_dismiss_btn = QPushButton("✕")
        self.banner_dismiss_btn.setFixedSize(24, 24)
        self.banner_dismiss_btn.setStyleSheet("background: transparent; border: none; font-weight: bold; color: #f0883e;")
        self.banner_dismiss_btn.clicked.connect(lambda: self.banner_widget.setVisible(False))
        banner_layout.addWidget(self.banner_dismiss_btn)

        main_layout.addWidget(self.banner_widget)

        # 2. Main Splitter (Sidebar + Detail Panel)
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(2)

        self.sidebar = SidebarWidget()
        self.sidebar.disk_selected.connect(self._on_disk_selected_in_sidebar)
        self.sidebar.server_add_requested.connect(self._on_add_server)
        self.sidebar.server_remove_requested.connect(self._on_remove_server)
        self.sidebar.server_refresh_requested.connect(self._on_refresh_server)
        self.sidebar.local_refresh_requested.connect(self._scan_local_drives)
        splitter.addWidget(self.sidebar)

        self.detail_panel = DetailPanel()
        self.detail_panel.setObjectName("DetailContainer")
        self.detail_panel.scan_requested.connect(self._scan_single_disk)
        self.detail_panel.export_requested.connect(self._export_report)
        splitter.addWidget(self.detail_panel)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 780])

        main_layout.addWidget(splitter, 1)

        # Status Bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Listo. Modo on-demand activo (sin consumo en segundo plano).")

    def _start_worker(self, fn, on_result=None, on_error=None, *args, **kwargs) -> TaskWorker:
        """Starts a background worker thread with robust memory lifecycle management."""
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
        """Checks smartctl availability and permissions at startup."""
        ok, msg = LocalDiskDetector.check_smartctl_available()
        if not ok:
            self._show_banner(msg, is_error=True)

    def _load_saved_servers(self):
        self._active_servers = ConfigManager.load_servers()
        self.sidebar.set_servers(self._active_servers)

    # ---------------- LOCAL DRIVES SCANNING ----------------

    def _scan_local_drives(self):
        self.status_bar.showMessage("Buscando discos físicos locales...")
        self._start_worker(
            self._do_discover_and_read_local_drives,
            on_result=self._on_local_drives_ready,
            on_error=lambda err: self.status_bar.showMessage(f"Error al escanear discos locales: {err}")
        )

    def _do_discover_and_read_local_drives(self) -> List[DiskInfo]:
        drives = LocalDiskDetector.discover_drives()
        result_drives = []
        for d in drives:
            full_disk = LocalDiskDetector.read_drive_smart(d.device_path)
            result_drives.append(full_disk)
        return result_drives

    def _on_local_drives_ready(self, drives: List[DiskInfo]):
        self.sidebar.set_local_disks(drives)
        self.status_bar.showMessage(f"Detección completada: {len(drives)} disco(s) local(es) encontrado(s).")
        if drives and not self.detail_panel.current_disk:
            self.detail_panel.display_disk(drives[0])

    # ---------------- REMOTE SERVERS SCANNING ----------------

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
        self.status_bar.showMessage("Servidor eliminado.")

    def _on_refresh_server(self, server_id: str):
        srv = next((s for s in self._active_servers if s.id == server_id), None)
        if not srv:
            return

        self.status_bar.showMessage(f"Conectando a {srv.name} ({srv.host})...")
        self._start_worker(
            self._do_scan_remote_server,
            on_result=lambda res: self._on_remote_server_scanned(server_id, res),
            on_error=lambda err: self._on_remote_server_error(server_id, err),
            srv=srv
        )

    def _do_scan_remote_server(self, srv: ServerConfig):
        client = RemoteSSHClient(srv)
        ok, msg = client.connect(timeout=8)
        if not ok:
            raise Exception(msg)

        drives = client.discover_remote_drives()
        scanned_drives = []
        for d in drives:
            full_disk = client.read_remote_drive_smart(d.device_path)
            scanned_drives.append(full_disk)
        
        self._ssh_clients[srv.id] = client
        return scanned_drives

    def _on_remote_server_scanned(self, server_id: str, drives: List[DiskInfo]):
        self.sidebar.set_server_drives(server_id, drives)
        self.status_bar.showMessage(f"Servidor escaneado: {len(drives)} disco(s) detectado(s).")

    def _on_remote_server_error(self, server_id: str, error_msg: str):
        self.sidebar.set_server_drives(server_id, [], error=error_msg)
        self.status_bar.showMessage(f"Error al conectar con servidor: {error_msg}")

    # ---------------- SINGLE DISK SCAN ----------------

    def _on_disk_selected_in_sidebar(self, disk: DiskInfo):
        self.detail_panel.display_disk(disk)
        if disk.health_status == HealthStatus.UNKNOWN and not disk.attributes:
            self._scan_single_disk(disk)

    def _scan_single_disk(self, disk: DiskInfo):
        self.detail_panel.set_loading(True, f"Escaneando telemetría de {disk.name}...")
        self.status_bar.showMessage(f"Leyendo telemetría S.M.A.R.T. de {disk.device_path}...")

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
                raise Exception("Servidor no encontrado en configuración.")
            
            client = self._ssh_clients.get(server_id)
            if not client or not client.is_connected():
                client = RemoteSSHClient(srv)
                ok, msg = client.connect(timeout=8)
                if not ok:
                    raise Exception(msg)
                self._ssh_clients[server_id] = client

            return client.read_remote_drive_smart(disk.device_path)

    def _on_single_disk_scanned(self, updated_disk: DiskInfo):
        self.detail_panel.display_disk(updated_disk)
        self.sidebar.update_disk_info(updated_disk)
        self.detail_panel.set_loading(False, "Lectura completada.")
        self.status_bar.showMessage(f"Diagnóstico actualizado para {updated_disk.model} ({updated_disk.device_path}).")

    def _on_single_disk_error(self, error_msg: str):
        self.detail_panel.set_loading(False, f"Error: {error_msg}")
        self.status_bar.showMessage(f"Fallo al escanear disco: {error_msg}")
        QMessageBox.warning(self, "Error de Lectura", f"No se pudo leer el disco:\n\n{error_msg}")

    # ---------------- EXPORT REPORT ----------------

    def _export_report(self, disk: DiskInfo):
        if not disk:
            return

        ok, out_path = MarkdownExporter.export_to_file(disk)
        if ok:
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("Informe Exportado")
            msg_box.setText(f"El informe S.M.A.R.T. ha sido generado exitosamente:\n\n{out_path}")
            
            btn_open_folder = msg_box.addButton("Abrir Carpeta", QMessageBox.ActionRole)
            btn_ok = msg_box.addButton("Aceptar", QMessageBox.AcceptRole)
            msg_box.setDefaultButton(btn_ok)
            msg_box.exec()

            if msg_box.clickedButton() == btn_open_folder:
                subprocess.Popen(["xdg-open", os.path.dirname(out_path)])
            
            self.status_bar.showMessage(f"Informe guardado en: {out_path}")
        else:
            QMessageBox.critical(self, "Error al Exportar", f"No se pudo generar el informe:\n{out_path}")

    def closeEvent(self, event):
        for worker in list(self._active_workers):
            try:
                worker.quit()
                worker.wait(500)
            except Exception:
                pass
        super().closeEvent(event)
