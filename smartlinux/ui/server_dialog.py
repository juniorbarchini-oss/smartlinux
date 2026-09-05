import os
from typing import Optional
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QFileDialog,
    QMessageBox, QSpinBox, QFormLayout, QFrame, QStackedWidget
)
from PySide6.QtCore import Qt, QThread, Signal
from ..core.models import ServerConfig
from ..core.ssh_client import RemoteSSHClient


class TestSSHThread(QThread):
    result_ready = Signal(bool, str)

    def __init__(self, config: ServerConfig):
        super().__init__()
        self.config = config

    def run(self):
        client = RemoteSSHClient(self.config)
        ok, msg = client.connect(timeout=6)
        if ok:
            client.disconnect()
            self.result_ready.emit(True, "SSH connection verified successfully!")
        else:
            self.result_ready.emit(False, msg)


class ServerDialog(QDialog):
    """Dialog to add or edit an SSH Homelab server with Password or Key authentication."""

    def __init__(self, parent=None, server_config: Optional[ServerConfig] = None):
        super().__init__(parent)
        self.setWindowTitle("Configure SSH Server")
        self.setMinimumWidth(500)
        self.server_config = server_config
        self.test_thread: Optional[TestSSHThread] = None

        self._setup_ui()
        if server_config:
            self._load_config(server_config)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(14)

        # Title
        header = QLabel("🖥️ Connect Remote Host")
        header.setStyleSheet("font-size: 18px; font-weight: bold; color: #58a6ff;")
        layout.addWidget(header)

        desc = QLabel("Add a remote Linux/macOS/BSD server to monitor drive diagnostics via SSH.")
        desc.setStyleSheet("color: #8b949e; font-size: 13px; margin-bottom: 4px;")
        layout.addWidget(desc)

        # Server Form
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(12)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g. Storage-Node, Backup-Server, Proxmox-VE")
        form.addRow("Server Name:", self.name_input)

        self.host_input = QLineEdit()
        self.host_input.setPlaceholderText("e.g. 192.168.1.100 or server.local")
        form.addRow("IP / Hostname:", self.host_input)

        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(22)
        form.addRow("SSH Port:", self.port_input)

        self.user_input = QLineEdit()
        self.user_input.setText("root")
        self.user_input.setPlaceholderText("e.g. root or username")
        form.addRow("SSH Username:", self.user_input)

        layout.addLayout(form)

        # Authentication Method Section
        auth_frame = QFrame()
        auth_frame.setStyleSheet("background-color: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 12px;")
        auth_layout = QVBoxLayout(auth_frame)
        auth_layout.setSpacing(10)

        auth_title = QLabel("AUTHENTICATION METHOD")
        auth_title.setStyleSheet("font-size: 12px; font-weight: bold; color: #58a6ff; letter-spacing: 0.5px;")
        auth_layout.addWidget(auth_title)

        self.auth_combo = QComboBox()
        self.auth_combo.addItem("🔒 User Password", "password")
        self.auth_combo.addItem("🔑 Private SSH Key (~/.ssh)", "key")
        self.auth_combo.currentIndexChanged.connect(self._on_auth_type_changed)
        auth_layout.addWidget(self.auth_combo)

        # Stacked Pages
        self.auth_stack = QStackedWidget()

        # Page 0: Password Input
        pass_page = QWidget()
        pass_layout = QHBoxLayout(pass_page)
        pass_layout.setContentsMargins(0, 4, 0, 0)
        
        self.pass_input = QLineEdit()
        self.pass_input.setEchoMode(QLineEdit.Password)
        self.pass_input.setPlaceholderText("Enter SSH user password...")
        pass_layout.addWidget(self.pass_input)

        self.toggle_pass_btn = QPushButton("👁️")
        self.toggle_pass_btn.setToolTip("Show / Hide password")
        self.toggle_pass_btn.setFixedSize(38, 38)
        self.toggle_pass_btn.clicked.connect(self._toggle_pass_visibility)
        pass_layout.addWidget(self.toggle_pass_btn)

        self.auth_stack.addWidget(pass_page)

        # Page 1: Key File Input
        key_page = QWidget()
        key_layout = QHBoxLayout(key_page)
        key_layout.setContentsMargins(0, 4, 0, 0)

        default_key = os.path.expanduser("~/.ssh/id_rsa")
        if not os.path.exists(default_key):
            default_key = os.path.expanduser("~/.ssh/id_ed25519")

        self.key_input = QLineEdit()
        self.key_input.setText(default_key)
        self.key_input.setPlaceholderText("Path to private key file...")
        key_layout.addWidget(self.key_input)

        self.key_browse_btn = QPushButton("Browse...")
        self.key_browse_btn.clicked.connect(self._browse_key_file)
        key_layout.addWidget(self.key_browse_btn)

        self.auth_stack.addWidget(key_page)

        auth_layout.addWidget(self.auth_stack)
        layout.addWidget(auth_frame)

        # Status label
        self.test_status_label = QLabel("")
        self.test_status_label.setWordWrap(True)
        self.test_status_label.setStyleSheet("font-size: 13px; padding: 4px;")
        layout.addWidget(self.test_status_label)

        # Action Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self.test_btn = QPushButton("🔍 Test Connection")
        self.test_btn.clicked.connect(self._test_connection)
        btn_layout.addWidget(self.test_btn)

        btn_layout.addStretch()

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        self.save_btn = QPushButton("Save & Connect")
        self.save_btn.setObjectName("PrimaryButton")
        self.save_btn.clicked.connect(self._save)
        btn_layout.addWidget(self.save_btn)

        layout.addLayout(btn_layout)

    def _on_auth_type_changed(self, index: int):
        self.auth_stack.setCurrentIndex(index)

    def _toggle_pass_visibility(self):
        if self.pass_input.echoMode() == QLineEdit.Password:
            self.pass_input.setEchoMode(QLineEdit.Normal)
            self.toggle_pass_btn.setText("🔒")
        else:
            self.pass_input.setEchoMode(QLineEdit.Password)
            self.toggle_pass_btn.setText("👁️")

    def _browse_key_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Private SSH Key",
            os.path.expanduser("~/.ssh/"),
            "Key Files (*);;All Files (*)"
        )
        if path:
            self.key_input.setText(path)

    def _get_current_config(self) -> Optional[ServerConfig]:
        name = self.name_input.text().strip()
        host = self.host_input.text().strip()
        user = self.user_input.text().strip()
        port = self.port_input.value()

        if not host:
            QMessageBox.warning(self, "Required Field", "Please enter an IP address or hostname.")
            return None

        if not name:
            name = host

        auth_type = self.auth_combo.currentData()
        password = self.pass_input.text() if auth_type == "password" else None
        key_path = self.key_input.text().strip() if auth_type == "key" else None

        srv_id = self.server_config.id if self.server_config else ""
        return ServerConfig(
            id=srv_id,
            name=name,
            host=host,
            port=port,
            username=user or "root",
            auth_type=auth_type,
            key_path=key_path,
            password=password
        )

    def _test_connection(self):
        config = self._get_current_config()
        if not config:
            return

        self.test_btn.setEnabled(False)
        self.test_btn.setText("Testing...")
        self.test_status_label.setText("Connecting to host...")
        self.test_status_label.setStyleSheet("color: #58a6ff; font-size: 13px;")

        self.test_thread = TestSSHThread(config)
        self.test_thread.result_ready.connect(self._on_test_result)
        self.test_thread.start()

    def _on_test_result(self, ok: bool, msg: str):
        self.test_btn.setEnabled(True)
        self.test_btn.setText("🔍 Test Connection")
        if ok:
            self.test_status_label.setStyleSheet("color: #3fb950; font-weight: bold; font-size: 13px;")
            self.test_status_label.setText(f"✓ {msg}")
        else:
            self.test_status_label.setStyleSheet("color: #f85149; font-size: 13px;")
            self.test_status_label.setText(f"✗ {msg}")

    def _load_config(self, cfg: ServerConfig):
        self.name_input.setText(cfg.name)
        self.host_input.setText(cfg.host)
        self.port_input.setValue(cfg.port)
        self.user_input.setText(cfg.username)
        
        if cfg.auth_type == "key":
            self.auth_combo.setCurrentIndex(1)
            if cfg.key_path:
                self.key_input.setText(cfg.key_path)
        else:
            self.auth_combo.setCurrentIndex(0)
            if cfg.password:
                self.pass_input.setText(cfg.password)

    def _save(self):
        cfg = self._get_current_config()
        if not cfg:
            return
        self.server_config = cfg
        self.accept()
