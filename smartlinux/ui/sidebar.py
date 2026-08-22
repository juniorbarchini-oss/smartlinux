from typing import Dict, List, Optional
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTreeWidget, QTreeWidgetItem, QHeaderView, QMenu, QMessageBox, QStyle
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon, QColor, QFont
from ..core.models import DiskInfo, ServerConfig, HealthStatus


class SidebarWidget(QWidget):
    """Sidebar listing Local and Remote Disks with color-coded health badges."""

    disk_selected = Signal(object)              # Emits DiskInfo
    server_add_requested = Signal()             # Request open Add Server dialog
    server_remove_requested = Signal(str)       # Emits server_id
    server_refresh_requested = Signal(str)      # Emits server_id
    local_refresh_requested = Signal()          # Refresh local drives

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(280)
        self.setMaximumWidth(380)

        self._local_drives: Dict[str, DiskInfo] = {}   # device_path -> DiskInfo
        self._remote_drives: Dict[str, Dict[str, DiskInfo]] = {} # server_id -> {device_path: DiskInfo}
        self._servers: Dict[str, ServerConfig] = {}    # server_id -> ServerConfig

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        # Header Title
        header_layout = QHBoxLayout()
        title = QLabel("DISPOSITIVOS")
        title.setStyleSheet("font-size: 13px; font-weight: bold; color: #8b949e; letter-spacing: 1px;")
        header_layout.addWidget(title)
        header_layout.addStretch()

        self.refresh_all_btn = QPushButton("↻ Refrescar")
        self.refresh_all_btn.setToolTip("Refrescar lista de dispositivos")
        self.refresh_all_btn.setStyleSheet("font-size: 11px; padding: 4px 8px; font-weight: bold;")
        self.refresh_all_btn.clicked.connect(self.local_refresh_requested.emit)
        header_layout.addWidget(self.refresh_all_btn)

        layout.addLayout(header_layout)

        # Device Tree
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setAnimated(True)
        self.tree.setIndentation(16)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.itemSelectionChanged.connect(self._on_item_selection)
        layout.addWidget(self.tree)

        # Top-level Tree Roots
        self.local_root = QTreeWidgetItem(self.tree)
        self.local_root.setText(0, "💻 Discos Locales")
        font = self.local_root.font(0)
        font.setBold(True)
        self.local_root.setFont(0, font)
        self.local_root.setData(0, Qt.UserRole, {"type": "group_local"})
        self.local_root.setExpanded(True)

        self.remote_root = QTreeWidgetItem(self.tree)
        self.remote_root.setText(0, "🌐 Servidores Homelab")
        self.remote_root.setFont(0, font)
        self.remote_root.setData(0, Qt.UserRole, {"type": "group_remote"})
        self.remote_root.setExpanded(True)

        # Bottom Add Server Button
        self.add_server_btn = QPushButton("+ Añadir Servidor SSH")
        self.add_server_btn.setObjectName("PrimaryButton")
        self.add_server_btn.clicked.connect(self.server_add_requested.emit)
        layout.addWidget(self.add_server_btn)

    def set_local_disks(self, disks: List[DiskInfo]):
        """Populates the local disks section."""
        while self.local_root.childCount() > 0:
            self.local_root.removeChild(self.local_root.child(0))
        
        self._local_drives.clear()

        if not disks:
            empty_item = QTreeWidgetItem(self.local_root)
            empty_item.setText(0, "  (No se detectaron discos)")
            empty_item.setData(0, Qt.UserRole, {"type": "empty"})
            return

        for disk in disks:
            self._local_drives[disk.device_path] = disk
            item = QTreeWidgetItem(self.local_root)
            self._update_disk_item(item, disk)

    def update_disk_info(self, disk: DiskInfo):
        """Updates health status and text for a specific scanned disk."""
        if not disk.is_remote:
            self._local_drives[disk.device_path] = disk
            for i in range(self.local_root.childCount()):
                item = self.local_root.child(i)
                data = item.data(0, Qt.UserRole)
                if data and data.get("device_path") == disk.device_path:
                    self._update_disk_item(item, disk)
                    break
        else:
            server_id = disk.server_id
            if server_id and server_id in self._remote_drives:
                self._remote_drives[server_id][disk.device_path] = disk
                for s_idx in range(self.remote_root.childCount()):
                    srv_item = self.remote_root.child(s_idx)
                    s_data = srv_item.data(0, Qt.UserRole)
                    if s_data and s_data.get("server_id") == server_id:
                        for d_idx in range(srv_item.childCount()):
                            item = srv_item.child(d_idx)
                            d_data = item.data(0, Qt.UserRole)
                            if d_data and d_data.get("device_path") == disk.device_path:
                                self._update_disk_item(item, disk)
                                break

    def _update_disk_item(self, item: QTreeWidgetItem, disk: DiskInfo):
        icon_str = disk.health_status.icon if disk.attributes else "⚪"
        status_hint = f" ({disk.size_human})" if disk.size_human and disk.size_human != "Desconocido" else ""
        label = f"{icon_str}  {disk.name} • {disk.model}{status_hint}"
        item.setText(0, label)
        item.setData(0, Qt.UserRole, {
            "type": "disk",
            "device_path": disk.device_path,
            "is_remote": disk.is_remote,
            "server_id": disk.server_id
        })

    def set_servers(self, servers: List[ServerConfig]):
        """Populates the remote servers branch."""
        while self.remote_root.childCount() > 0:
            self.remote_root.removeChild(self.remote_root.child(0))
        
        self._servers = {s.id: s for s in servers}
        self._remote_drives = {s.id: {} for s in servers}

        for srv in servers:
            srv_item = QTreeWidgetItem(self.remote_root)
            srv_item.setText(0, f"🖥️  {srv.name} ({srv.host})")
            font = srv_item.font(0)
            font.setBold(True)
            srv_item.setFont(0, font)
            srv_item.setData(0, Qt.UserRole, {
                "type": "server",
                "server_id": srv.id
            })
            
            loading_item = QTreeWidgetItem(srv_item)
            loading_item.setText(0, "  ⏳ Conectar / Cargar discos...")
            loading_item.setData(0, Qt.UserRole, {"type": "server_action", "server_id": srv.id})
            srv_item.setExpanded(True)

    def set_server_drives(self, server_id: str, drives: List[DiskInfo], error: Optional[str] = None):
        """Sets the disk items under a specific remote server."""
        for i in range(self.remote_root.childCount()):
            srv_item = self.remote_root.child(i)
            data = srv_item.data(0, Qt.UserRole)
            if data and data.get("server_id") == server_id:
                while srv_item.childCount() > 0:
                    srv_item.removeChild(srv_item.child(0))

                if error:
                    err_item = QTreeWidgetItem(srv_item)
                    err_item.setText(0, f"  ⚠️ {error}")
                    err_item.setData(0, Qt.UserRole, {"type": "error", "server_id": server_id})
                    return

                if not drives:
                    empty_item = QTreeWidgetItem(srv_item)
                    empty_item.setText(0, "  (Sin discos detectados)")
                    empty_item.setData(0, Qt.UserRole, {"type": "empty"})
                    return

                if server_id not in self._remote_drives:
                    self._remote_drives[server_id] = {}

                for disk in drives:
                    self._remote_drives[server_id][disk.device_path] = disk
                    d_item = QTreeWidgetItem(srv_item)
                    self._update_disk_item(d_item, disk)
                
                srv_item.setExpanded(True)
                break

    def _on_item_selection(self):
        selected = self.tree.selectedItems()
        if not selected:
            return
        item = selected[0]
        data = item.data(0, Qt.UserRole)
        if not data:
            return

        item_type = data.get("type")
        if item_type == "disk":
            dev_path = data.get("device_path")
            is_remote = data.get("is_remote", False)
            server_id = data.get("server_id")

            if not is_remote and dev_path in self._local_drives:
                self.disk_selected.emit(self._local_drives[dev_path])
            elif is_remote and server_id in self._remote_drives and dev_path in self._remote_drives[server_id]:
                self.disk_selected.emit(self._remote_drives[server_id][dev_path])
        elif item_type == "server_action":
            server_id = data.get("server_id")
            if server_id:
                self.server_refresh_requested.emit(server_id)

    def _show_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return
        data = item.data(0, Qt.UserRole)
        if not data:
            return

        item_type = data.get("type")
        menu = QMenu(self)

        if item_type in ("server", "server_action", "error"):
            server_id = data.get("server_id")
            srv = self._servers.get(server_id)
            if not srv:
                return

            act_refresh = menu.addAction("↻ Recargar Discos del Servidor")
            act_remove = menu.addAction("🗑️ Eliminar Servidor")

            action = menu.exec(self.tree.viewport().mapToGlobal(pos))
            if action == act_refresh:
                self.server_refresh_requested.emit(server_id)
            elif action == act_remove:
                reply = QMessageBox.question(
                    self,
                    "Confirmar Eliminación",
                    f"¿Está seguro de eliminar '{srv.name}' ({srv.host}) de la lista?",
                    QMessageBox.Yes | QMessageBox.No
                )
                if reply == QMessageBox.Yes:
                    self.server_remove_requested.emit(server_id)

        elif item_type == "disk":
            act_scan = menu.addAction("🔍 Escanear Ahora (Scan Now)")
            action = menu.exec(self.tree.viewport().mapToGlobal(pos))
            if action == act_scan:
                self._on_item_selection()
