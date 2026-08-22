import os
import shutil
import subprocess
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QMessageBox, QFrame
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap, QIcon


class InstallWorker(QThread):
    finished_signal = Signal(bool, str)

    def __init__(self, cmd: list):
        super().__init__()
        self.cmd = cmd

    def run(self):
        try:
            proc = subprocess.run(self.cmd, capture_output=True, text=True)
            if proc.returncode == 0:
                self.finished_signal.emit(True, "Dependencies installed and configured successfully!")
            else:
                err = proc.stderr.strip() or proc.stdout.strip() or "Authorization cancelled or failed."
                self.finished_signal.emit(False, err)
        except Exception as e:
            self.finished_signal.emit(False, str(e))


class SetupDependenciesDialog(QDialog):
    """
    User-friendly modal dialog to automatically install and configure
    smartmontools and permissions on any Linux distribution with a single click.
    """

    def __init__(self, issue_description: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SmartLinux - System Setup Required")
        self.setMinimumWidth(520)
        self.issue_description = issue_description
        self.worker: Optional[InstallWorker] = None

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Header with icon
        hdr_box = QHBoxLayout()
        icon_lbl = QLabel("⚙️")
        icon_lbl.setStyleSheet("font-size: 32px;")
        hdr_box.addWidget(icon_lbl)

        hdr_txt_box = QVBoxLayout()
        title = QLabel("System Storage Telemetry Setup")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #58a6ff;")
        hdr_txt_box.addWidget(title)

        subtitle = QLabel("SmartLinux requires 'smartmontools' to inspect hardware storage health.")
        subtitle.setStyleSheet("color: #8b949e; font-size: 13px;")
        hdr_txt_box.addWidget(subtitle)
        hdr_box.addLayout(hdr_txt_box)
        layout.addLayout(hdr_box)

        # Issue Details Frame
        desc_frame = QFrame()
        desc_frame.setStyleSheet("background-color: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 12px;")
        desc_layout = QVBoxLayout(desc_frame)
        
        info_title = QLabel("CURRENT STATUS:")
        info_title.setStyleSheet("font-size: 11px; font-weight: bold; color: #f0883e; letter-spacing: 0.5px;")
        desc_layout.addWidget(info_title)

        self.info_msg = QLabel(self.issue_description)
        self.info_msg.setWordWrap(True)
        self.info_msg.setStyleSheet("color: #e6edf3; font-size: 13px; margin-top: 4px;")
        desc_layout.addWidget(self.info_msg)

        layout.addWidget(desc_frame)

        # Action Explanation
        explain = QLabel(
            "Clicking <b>'Install & Configure'</b> will request standard administrator authorization "
            "to install the required packages and configure non-root disk access automatically."
        )
        explain.setWordWrap(True)
        explain.setStyleSheet("font-size: 13px; color: #c9d1d9;")
        layout.addWidget(explain)

        # Progress Bar (hidden by default)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("font-size: 12px; color: #58a6ff;")
        layout.addWidget(self.status_lbl)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)
        btn_layout.addStretch()

        self.btn_cancel = QPushButton("Cancel / Continue Anyway")
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)

        self.btn_install = QPushButton("⚡ Install & Configure Now")
        self.btn_install.setObjectName("PrimaryButton")
        self.btn_install.clicked.connect(self._start_auto_install)
        btn_layout.addWidget(self.btn_install)

        layout.addLayout(btn_layout)

    def _get_install_command(self) -> list:
        # Detect package manager
        if shutil.which("apt-get"):
            script = "apt-get update && apt-get install -y smartmontools && chmod u+s /usr/sbin/smartctl"
        elif shutil.which("dnf"):
            script = "dnf install -y smartmontools && chmod u+s $(which smartctl)"
        elif shutil.which("pacman"):
            script = "pacman -Sy --noconfirm smartmontools && chmod u+s $(which smartctl)"
        elif shutil.which("zypper"):
            script = "zypper --non-interactive install smartmontools && chmod u+s $(which smartctl)"
        else:
            script = "chmod u+s $(which smartctl)"

        return ["pkexec", "bash", "-c", script]

    def _start_auto_install(self):
        self.btn_install.setEnabled(False)
        self.btn_cancel.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.status_lbl.setText("Prompting for administrator privileges...")

        cmd = self._get_install_command()
        self.worker = InstallWorker(cmd)
        self.worker.finished_signal.connect(self._on_install_completed)
        self.worker.start()

    def _on_install_completed(self, success: bool, message: str):
        self.progress_bar.setVisible(False)
        self.btn_install.setEnabled(True)
        self.btn_cancel.setEnabled(True)

        if success:
            QMessageBox.information(self, "Setup Completed", "Packages installed and permissions configured successfully!")
            self.accept()
        else:
            self.status_lbl.setText("")
            QMessageBox.critical(self, "Setup Failed", f"Could not complete installation:\n\n{message}")
