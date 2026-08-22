from typing import Dict, List, Optional
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTreeWidget, QTreeWidgetItem, QHeaderView, QMenu, QMessageBox
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from ..core.models import DiskInfo, ServerConfig, HealthStatus


class SidebarWidget(QWidget):
    """Sidebar listing Local and Remote Disks with color-coded health badges."""

    disk_selected = Signal(object)              # Emits DiskInfo
    server_add_requested = Signal()             # Request open Add Server dialog
    server_remove_requested = Signal(str)       # Emits server_id
    server_refresh_requested = Signal(str)      # Emits server_id
    local_refresh_requested = Signal()          # Refresh local drives
    eject_requested = Signal(str)               # Emits device_path for local USB drive

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(300)
        self.setMaximumWidth(400)

        self._local_drives: Dict[str, DiskInfo] = {}
        self._remote_drives: Dict[str, Dict[str, DiskInfo]] = {}
        self._servers: Dict[str, ServerConfig] = {}

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        # Header Title
        header_layout = QHBoxLayout()
        title = QLabel("STORAGE DEVICES")
        title.setStyleSheet("font-size: 13px; font-weight: bold; color: #8b949e; letter-spacing: 1px;")
        header_layout.addWidget(title)
        header_layout.addStretch()

        self.refresh_all_btn = QPushButton("↻ Refresh")
        self.refresh_all_btn.setToolTip("Refresh storage devices")
        self.refresh_all_btn.setStyleSheet("font-size: 12px; padding: 4px 10px; font-weight: bold;")
        self.refresh_all_btn.clicked.connect(self.local_refresh_requested.emit)
        header_layout.addWidget(self.refresh_all_btn)

        layout.addLayout(header_layout)

        # Device Tree
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setAnimated(True)
        self.tree.setIndentation(18)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.itemSelectionChanged.connect(self._on_item_selection)
        layout.addWidget(self.tree)

        # Top-level Tree Roots
        self.local_root = QTreeWidgetItem(self.tree)
        self.local_root.setText(0, "💻 Local Drives")
        font = self.local_root.font(0)
        font.setBold(True)
        self.local_root.setFont(0, font)
        self.local_root.setData(0, Qt.UserRole, {"type": "group_local"})
        self.local_root.setExpanded(True)

        self.remote_root = QTreeWidgetItem(self.tree)
        self.remote_root.setText(0, "🌐 Homelab Servers")
        self.remote_root.setFont(0, font)
        self.remote_root.setData(0, Qt.UserRole, {"type": "group_remote"})
        self.remote_root.setExpanded(True)

        # Bottom Add Server Button
        self.add_server_btn = QPushButton("+ Add SSH Server")
        self.add_server_btn.setObjectName("PrimaryButton")
        self.add_server_btn.clicked.connect(self.server_add_requested.emit)
        layout.addWidget(self.add_server_btn)

    def set_local_disks(self, disks: List[DiskInfo]):
        while self.local_root.childCount() > 0:
            self.local_root.removeChild(self.local_root.child(0))
        
        self._local_drives.clear()

        if not disks:
            empty_item = QTreeWidgetItem(self.local_root)
            empty_item.setText(0, "  (No storage drives detected)")
            empty_item.setData(0, Qt.UserRole, {"type": "empty"})
            return

        for disk in disks:
            self._local_drives[disk.device_path] = disk
            item = QTreeWidgetItem(self.local_root)
            self._update_disk_item(item, disk)

    def update_disk_info(self, disk: DiskInfo):
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
        usb_tag = " [USB 🔌]" if disk.is_usb else ""
        size_hint = f" ({disk.size_human})" if disk.size_human and disk.size_human != "Unknown" else ""
        label = f"{icon_str}  {disk.name} • {disk.model}{size_hint}{usb_tag}"
        item.setText(0, label)
        item.setData(0, Qt.UserRole, {
            "type": "disk",
            "device_path": disk.device_path,
            "is_remote": disk.is_remote,
            "is_usb": disk.is_usb,
            "server_id": disk.server_id
        })

    def set_servers(self, servers: List[ServerConfig]):
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
            loading_item.setText(0, "  ⏳ Connect / List drives...")
            loading_item.setData(0, Qt.UserRole, {"type": "server_action", "server_id": srv.id})
            srv_item.setExpanded(True)

    def set_server_drives(self, server_id: str, drives: List[DiskInfo], error: Optional[str] = None):
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
                    empty_item.setText(0, "  (No drives detected)")
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

            act_refresh = menu.addAction("↻ Reload Server Drives")
            act_remove = menu.addAction("🗑️ Remove Server")

            action = menu.exec(self.tree.viewport().mapToGlobal(pos))
            if action == act_refresh:
                self.server_refresh_requested.emit(server_id)
            elif action == act_remove:
                reply = QMessageBox.question(
                    self,
                    "Confirm Removal",
                    f"Are you sure you want to remove '{srv.name}' ({srv.host}) from the list?",
                    QMessageBox.Yes | QMessageBox.No
                )
                if reply == QMessageBox.Yes:
                    self.server_remove_requested.emit(server_id)

        elif item_type == "disk":
            dev_path = data.get("device_path")
            is_remote = data.get("is_remote", False)
            is_usb = data.get("is_usb", False)

            act_scan = menu.addAction("🔍 Scan Now")
            
            act_eject = None
            if not is_remote and is_usb:
                act_eject = menu.addAction("⏏️ Safely Eject Drive")

            action = menu.exec(self.tree.viewport().mapToGlobal(pos))
            if action == act_scan:
                self._on_item_selection()
            elif act_eject and action == act_eject:
                self.eject_requested.emit(dev_path)
