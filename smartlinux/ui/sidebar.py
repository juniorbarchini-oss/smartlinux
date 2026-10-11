from typing import Dict, List, Optional
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTreeWidget, QTreeWidgetItem, QHeaderView, QMenu, QMessageBox
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from ..core.models import DiskInfo, ServerConfig, HealthStatus
from ..core.raid import VIRTUAL_GROUP, VIRTUAL_SUMMARY
from .scan_state import scan_action_text


def controller_group_label(name: str, physical: int, virtual: int) -> str:
    """e.g. "PERC H730 Mini (MegaRAID) — 5 physical, 4 virtual"."""
    return f"{name} — {physical} physical, {virtual} virtual"


class SidebarWidget(QWidget):
    """Sidebar listing Local and Remote Disks with color-coded health badges."""

    disk_selected = Signal(object)              # Emits DiskInfo (display only, never scans)
    scan_requested = Signal(object)             # Emits DiskInfo: user asked for a SMART read
    server_add_requested = Signal()             # Request open Add Server dialog
    server_remove_requested = Signal(str)       # Emits server_id
    server_refresh_requested = Signal(str)      # Emits server_id
    local_refresh_requested = Signal()          # Refresh local drives
    eject_requested = Signal(str)               # Emits device_path for local USB drive
    host_selected = Signal(str)                 # Emits server_id ("" = this computer): show the Server panel

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(300)
        self.setMaximumWidth(400)

        # keyed by DiskInfo.uid: RAID-attached disks share a device_path (/dev/bus/0)
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
            self._local_drives[disk.uid] = disk
        self._populate_disks(self.local_root, disks, None)

    def _populate_disks(self, parent: QTreeWidgetItem, disks: List[DiskInfo], server_id: Optional[str]):
        """Plain disks first (non-RAID hosts look as before), then the physical disks of
        each RAID controller under it, then the controllers' virtual drives in their own group."""
        plain = [d for d in disks if not d.is_virtual and not d.controller]
        virtual = [d for d in disks if d.is_virtual]
        groups: Dict[str, List[DiskInfo]] = {}
        for d in disks:
            if not d.is_virtual and d.controller:
                groups.setdefault(d.controller, []).append(d)
        for disk in plain:
            self._update_disk_item(QTreeWidgetItem(parent), disk)

        bold = parent.font(0)
        bold.setBold(True)
        for name, members in groups.items():
            n_virtual = sum(1 for d in virtual if d.controller == name)
            grp = QTreeWidgetItem(parent)
            grp.setText(0, f"🎛️  {controller_group_label(name, len(members), n_virtual)}")
            grp.setToolTip(0, f"{controller_group_label(name, len(members), n_virtual)}\n"
                              f"Physical disks behind this RAID controller (read with smartctl -d)")
            grp.setData(0, Qt.UserRole, {"type": "controller_group", "server_id": server_id, "controller": name})
            for disk in sorted(members, key=lambda d: (d.slot is None, d.slot or 0)):
                self._update_disk_item(QTreeWidgetItem(grp), disk)
            grp.setExpanded(True)

        if virtual:
            grp = QTreeWidgetItem(parent)
            grp.setText(0, f"🧩  {VIRTUAL_GROUP} — {len(virtual)}")
            grp.setToolTip(0, "Logical volumes exported by the RAID controller. They have no S.M.A.R.T. "
                              "of their own: check the physical disks above.")
            grp.setData(0, Qt.UserRole, {"type": "virtual_group", "server_id": server_id})
            for disk in virtual:
                self._update_disk_item(QTreeWidgetItem(grp), disk)
            grp.setExpanded(True)

    @classmethod
    def _find_disk_item(cls, parent: QTreeWidgetItem, uid: str) -> Optional[QTreeWidgetItem]:
        for i in range(parent.childCount()):
            item = parent.child(i)
            data = item.data(0, Qt.UserRole) or {}
            if data.get("type") == "disk" and data.get("uid") == uid:
                return item
            found = cls._find_disk_item(item, uid)
            if found:
                return found
        return None

    def update_disk_info(self, disk: DiskInfo):
        if not disk.is_remote:
            self._local_drives[disk.uid] = disk
            item = self._find_disk_item(self.local_root, disk.uid)
            if item:
                self._update_disk_item(item, disk)
        else:
            server_id = disk.server_id
            if server_id and server_id in self._remote_drives:
                self._remote_drives[server_id][disk.uid] = disk
                for s_idx in range(self.remote_root.childCount()):
                    srv_item = self.remote_root.child(s_idx)
                    s_data = srv_item.data(0, Qt.UserRole)
                    if s_data and s_data.get("server_id") == server_id:
                        item = self._find_disk_item(srv_item, disk.uid)
                        if item:
                            self._update_disk_item(item, disk)
                        break

    def _update_disk_item(self, item: QTreeWidgetItem, disk: DiskInfo):
        size_hint = f" ({disk.size_human})" if disk.size_human and disk.size_human != "Unknown" else ""
        if disk.is_virtual:
            label = f"▫️  {disk.name} • {VIRTUAL_SUMMARY}{size_hint}"
            item.setToolTip(0, f"{disk.model} — logical volume of {disk.controller or 'a RAID controller'}; "
                               f"no S.M.A.R.T. of its own")
        else:
            icon_str = disk.health_status.icon if disk.attributes else "⚪"
            usb_tag = " [USB 🔌]" if disk.is_usb else ""
            label = f"{icon_str}  {disk.name} • {disk.model}{size_hint}{usb_tag}"
            if disk.smart_dev_type and disk.uid != disk.device_path:
                item.setToolTip(0, f"smartctl -d {disk.smart_dev_type} {disk.device_path}")
        item.setText(0, label)
        item.setData(0, Qt.UserRole, {
            "type": "disk",
            "device_path": disk.device_path,
            "uid": disk.uid,
            "is_remote": disk.is_remote,
            "is_usb": disk.is_usb,
            "is_virtual": disk.is_virtual,
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
                    first_line = error.splitlines()[0]
                    err_item.setText(0, f"  ⚠️ {first_line}")
                    err_item.setToolTip(0, error)
                    err_item.setData(0, Qt.UserRole, {"type": "error", "server_id": server_id, "error_msg": error})
                    return

                if not drives:
                    empty_item = QTreeWidgetItem(srv_item)
                    empty_item.setText(0, "  (No drives detected)")
                    empty_item.setData(0, Qt.UserRole, {"type": "empty"})
                    return

                if server_id not in self._remote_drives:
                    self._remote_drives[server_id] = {}

                for disk in drives:
                    self._remote_drives[server_id][disk.uid] = disk
                self._populate_disks(srv_item, drives, server_id)

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
            disk = self._disk_for(data.get("uid") or data.get("device_path"),
                                  data.get("is_remote", False), data.get("server_id"))
            if disk:
                self.disk_selected.emit(disk)
        elif item_type == "group_local":
            self.host_selected.emit("")
        elif item_type in ("server", "controller_group", "virtual_group"):
            self.host_selected.emit(data.get("server_id") or "")
        elif item_type == "server_action":
            server_id = data.get("server_id")
            if server_id:
                self.server_refresh_requested.emit(server_id)
        elif item_type == "error":
            error_msg = data.get("error_msg", "Unknown connection error.")
            server_id = data.get("server_id")
            srv = self._servers.get(server_id)
            srv_name = srv.name if srv else "Remote Host"
            QMessageBox.warning(self, f"Connection Alert: {srv_name}", error_msg)

    def _disk_for(self, uid: str, is_remote: bool, server_id: Optional[str]) -> Optional[DiskInfo]:
        if not is_remote:
            return self._local_drives.get(uid)
        return self._remote_drives.get(server_id, {}).get(uid)

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
            act_details = None
            if item_type == "error":
                act_details = menu.addAction("ℹ️ View Error Details")
            act_remove = menu.addAction("🗑️ Remove Server")

            action = menu.exec(self.tree.viewport().mapToGlobal(pos))
            if action == act_refresh:
                self.server_refresh_requested.emit(server_id)
            elif act_details and action == act_details:
                error_msg = data.get("error_msg", "Unknown error.")
                QMessageBox.warning(self, f"Connection Alert: {srv.name}", error_msg)
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

            disk = self._disk_for(data.get("uid") or dev_path, is_remote, data.get("server_id"))
            act_scan = menu.addAction(scan_action_text(disk)) if disk and not disk.is_virtual else None
            
            act_eject = None
            if not is_remote and is_usb:
                act_eject = menu.addAction("⏏️ Safely Eject Drive")
            if menu.isEmpty():
                return

            action = menu.exec(self.tree.viewport().mapToGlobal(pos))
            if act_scan and action == act_scan:
                self.disk_selected.emit(disk)
                self.scan_requested.emit(disk)
            elif act_eject and action == act_eject:
                self.eject_requested.emit(dev_path)
