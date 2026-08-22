import os
from typing import Optional
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QRadioButton, QButtonGroup, QFileDialog,
    QMessageBox, QSpinBox, QFormLayout
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
            self.result_ready.emit(True, "¡Conexión SSH exitosa y verificada!")
        else:
            self.result_ready.emit(False, msg)


class ServerDialog(QDialog):
    """Dialog to add or edit an SSH Homelab server."""

    def __init__(self, parent=None, server_config: Optional[ServerConfig] = None):
        super().__init__(parent)
        self.setWindowTitle("Configurar Servidor Homelab SSH")
        self.setMinimumWidth(450)
        self.server_config = server_config
        self.test_thread: Optional[TestSSHThread] = None

        self._setup_ui()
        if server_config:
            self._load_config(server_config)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        # Title / Description
        header = QLabel("Conectar Servidor Homelab")
        header.setStyleSheet("font-size: 16px; font-weight: bold; color: #58a6ff;")
        layout.addWidget(header)

        desc = QLabel("Añada un servidor remoto para monitorear sus discos duros por SSH.")
        desc.setStyleSheet("color: #8b949e; font-size: 12px;")
        layout.addWidget(desc)

        # Form layout
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(10)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("ej. NodCasa, i7server, Proxmox")
        form.addRow("Nombre descriptivo:", self.name_input)

        self.host_input = QLineEdit()
        self.host_input.setPlaceholderText("ej. 192.168.1.50 o homelab.local")
        form.addRow("Host / Dirección IP:", self.host_input)

        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(22)
        form.addRow("Puerto SSH:", self.port_input)

        self.user_input = QLineEdit()
        self.user_input.setText("root")
        self.user_input.setPlaceholderText("ej. root o hbarchini")
        form.addRow("Usuario:", self.user_input)

        layout.addLayout(form)

        # Auth Method Group
        auth_box = QVBoxLayout()
        auth_label = QLabel("Método de Autenticación:")
        auth_label.setStyleSheet("font-weight: bold; margin-top: 8px;")
        auth_box.addWidget(auth_label)

        self.auth_group = QButtonGroup(self)
        self.rb_key = QRadioButton("Clave SSH Privada (Recomendado)")
        self.rb_pass = QRadioButton("Contraseña")
        self.rb_key.setChecked(True)
        self.auth_group.addButton(self.rb_key)
        self.auth_group.addButton(self.rb_pass)

        auth_box.addWidget(self.rb_key)

        # Key file selection row
        key_row = QHBoxLayout()
        default_key = os.path.expanduser("~/.ssh/id_rsa")
        if not os.path.exists(default_key):
            default_key = os.path.expanduser("~/.ssh/id_ed25519")
        self.key_input = QLineEdit()
        self.key_input.setText(default_key)
        self.key_btn = QPushButton("Explorar...")
        self.key_btn.clicked.connect(self._browse_key_file)
        key_row.addWidget(self.key_input)
        key_row.addWidget(self.key_btn)
        auth_box.addLayout(key_row)

        auth_box.addWidget(self.rb_pass)
        self.pass_input = QLineEdit()
        self.pass_input.setEchoMode(QLineEdit.Password)
        self.pass_input.setPlaceholderText("Contraseña del usuario SSH")
        self.pass_input.setEnabled(False)
        auth_box.addWidget(self.pass_input)

        self.rb_key.toggled.connect(self._toggle_auth_mode)
        layout.addLayout(auth_box)

        # Status label for connection test
        self.test_status_label = QLabel("")
        self.test_status_label.setWordWrap(True)
        self.test_status_label.setStyleSheet("font-size: 12px; padding: 4px;")
        layout.addWidget(self.test_status_label)

        # Buttons
        btn_layout = QHBoxLayout()
        self.test_btn = QPushButton("Probar Conexión")
        self.test_btn.clicked.connect(self._test_connection)
        btn_layout.addWidget(self.test_btn)

        btn_layout.addStretch()

        self.cancel_btn = QPushButton("Cancelar")
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        self.save_btn = QPushButton("Guardar y Conectar")
        self.save_btn.setObjectName("PrimaryButton")
        self.save_btn.clicked.connect(self._save)
        btn_layout.addWidget(self.save_btn)

        layout.addLayout(btn_layout)

    def _toggle_auth_mode(self):
        is_key = self.rb_key.isChecked()
        self.key_input.setEnabled(is_key)
        self.key_btn.setEnabled(is_key)
        self.pass_input.setEnabled(not is_key)

    def _browse_key_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Seleccionar Clave SSH Privada",
            os.path.expanduser("~/.ssh/"),
            "Archivos de Clave (*);;Todos los archivos (*)"
        )
        if path:
            self.key_input.setText(path)

    def _get_current_config(self) -> Optional[ServerConfig]:
        name = self.name_input.text().strip()
        host = self.host_input.text().strip()
        user = self.user_input.text().strip()
        port = self.port_input.value()

        if not host:
            QMessageBox.warning(self, "Campo Requerido", "Debe ingresar una dirección IP o Host.")
            return None

        if not name:
            name = host

        auth_type = "key" if self.rb_key.isChecked() else "password"
        key_path = self.key_input.text().strip() if auth_type == "key" else None
        password = self.pass_input.text() if auth_type == "password" else None

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
        self.test_btn.setText("Probando...")
        self.test_status_label.setText("Conectando con el host...")
        self.test_status_label.setStyleSheet("color: #58a6ff; font-size: 12px;")

        self.test_thread = TestSSHThread(config)
        self.test_thread.result_ready.connect(self._on_test_result)
        self.test_thread.start()

    def _on_test_result(self, ok: bool, msg: str):
        self.test_btn.setEnabled(True)
        self.test_btn.setText("Probar Conexión")
        if ok:
            self.test_status_label.setStyleSheet("color: #3fb950; font-weight: bold; font-size: 12px;")
            self.test_status_label.setText(f"✓ {msg}")
        else:
            self.test_status_label.setStyleSheet("color: #f85149; font-size: 12px;")
            self.test_status_label.setText(f"✗ {msg}")

    def _load_config(self, cfg: ServerConfig):
        self.name_input.setText(cfg.name)
        self.host_input.setText(cfg.host)
        self.port_input.setValue(cfg.port)
        self.user_input.setText(cfg.username)
        if cfg.auth_type == "password":
            self.rb_pass.setChecked(True)
        else:
            self.rb_key.setChecked(True)
            if cfg.key_path:
                self.key_input.setText(cfg.key_path)

    def _save(self):
        cfg = self._get_current_config()
        if not cfg:
            return
        self.server_config = cfg
        self.accept()
