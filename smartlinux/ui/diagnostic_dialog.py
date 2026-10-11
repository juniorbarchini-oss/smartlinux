"""Export diagnostic bundle: shows the exact commands, what is sanitized, runs them on request
(thread, progress, cancel), previews the SANITIZED result and saves a local .zip.

Same layout rule as CompatModeDialog: some window managers force a fixed size on every window
of the app, so the content lives in a fixed-width card centred in the window.
"""
import os
from typing import Callable, List, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget, QPlainTextEdit,
    QProgressBar, QPushButton, QVBoxLayout,
)

from ..core import diagnostics
from ..core.sanitizer import SanitizationError

CARD_WIDTH = 900
BODY_PX = 14
TITLE_PX = 19

SANITIZED_TEXT = (
    "<b>Replaced by consistent fake values:</b> serial numbers, WWN / SAS addresses / logical unit "
    "ids, UUIDs, service and asset tags, MAC and IP addresses, host and computer names, filesystem "
    "labels and RAID volume names, user names in paths. SCSI inquiry data is removed.<br>"
    "<b>Kept (needed to fix bugs):</b> models, vendors, firmware and versions, smartctl device types, "
    "sizes and SMART counters.<br>"
    "<b>Never included:</b> passwords, keys, disk or file contents. Nothing is sent anywhere: "
    "SmartLinux only saves the .zip where you choose."
)

Job = Callable[[Callable[[str], None], Callable[[], bool]], diagnostics.Bundle]


class DiagnosticWorker(QThread):
    progress = Signal(str)
    done = Signal(object)        # diagnostics.Bundle
    failed = Signal(str)

    def __init__(self, job: Job):
        super().__init__()
        self.job = job
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            bundle = self.job(self.progress.emit, lambda: self._cancel)
            self.done.emit(bundle)
        except diagnostics.Cancelled:
            self.failed.emit("Cancelled. Nothing was saved.")
        except SanitizationError as e:
            self.failed.emit(str(e))
        except Exception as e:
            self.failed.emit(f"Could not build the bundle: {e}")


class DiagnosticBundleDialog(QDialog):
    def __init__(self, host_label: str, commands: List[str], job: Job, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export diagnostic bundle")
        self.job = job
        self.bundle: Optional[diagnostics.Bundle] = None
        self.worker: Optional[DiagnosticWorker] = None
        self.saved_path: Optional[str] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 28, 28, 28)
        outer.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)

        card = QFrame()
        card.setObjectName("DiagCard")
        card.setFixedWidth(CARD_WIDTH)
        card.setStyleSheet("#DiagCard { background-color: #0d1117; border: 1px solid #30363d; border-radius: 10px; }")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.setSpacing(8)

        title = QLabel(f"Export diagnostic bundle — {host_label}")
        title.setTextFormat(Qt.PlainText)
        title.setStyleSheet(f"font-size: {TITLE_PX}px; font-weight: bold; color: #58a6ff; background: transparent;")
        layout.addWidget(title)
        intro = QLabel("SmartLinux will run only these read-only commands, sanitize their output and show "
                       "you the result before anything is saved.")
        intro.setWordWrap(True)
        intro.setStyleSheet(f"font-size: {BODY_PX}px; color: #c9d1d9; background: transparent;")
        layout.addWidget(intro)

        mono = QFont("monospace")
        mono.setStyleHint(QFont.Monospace)
        layout.addWidget(self._caption(f"COMMANDS ({len(commands)}, fixed list)"))
        self.commands_view = QPlainTextEdit("\n".join(commands))
        self.commands_view.setReadOnly(True)
        self.commands_view.setFont(mono)
        self.commands_view.setFixedHeight(150)
        layout.addWidget(self.commands_view)

        sanitized = QLabel(SANITIZED_TEXT)
        sanitized.setWordWrap(True)
        sanitized.setTextFormat(Qt.RichText)
        sanitized.setStyleSheet(f"font-size: {BODY_PX - 1}px; color: #c9d1d9; background: transparent;")
        layout.addWidget(sanitized)

        progress_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFixedWidth(160)
        self.progress_bar.setVisible(False)
        progress_row.addWidget(self.progress_bar)
        self.status_lbl = QLabel("Click 'Collect and preview' to start. Nothing runs before that.")
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setTextFormat(Qt.PlainText)
        self.status_lbl.setStyleSheet(f"font-size: {BODY_PX - 1}px; color: #8b949e; background: transparent;")
        progress_row.addWidget(self.status_lbl, 1)
        layout.addLayout(progress_row)

        layout.addWidget(self._caption("PREVIEW (ALREADY SANITIZED)"))
        preview_row = QHBoxLayout()
        self.file_list = QListWidget()
        self.file_list.setFixedWidth(300)
        self.file_list.setFixedHeight(240)
        self.file_list.currentTextChanged.connect(self._show_file)
        preview_row.addWidget(self.file_list)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(mono)
        self.preview.setFixedHeight(240)
        self.preview.setPlaceholderText("The sanitized files appear here before you save them.")
        preview_row.addWidget(self.preview, 1)
        layout.addLayout(preview_row)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self._on_close)
        buttons.addWidget(self.close_btn)
        self.collect_btn = QPushButton("Collect and preview")
        self.collect_btn.setObjectName("AccentButton")
        self.collect_btn.setAutoDefault(False)
        self.collect_btn.clicked.connect(self._on_collect_clicked)
        buttons.addWidget(self.collect_btn)
        self.save_btn = QPushButton("Save as…")
        self.save_btn.setObjectName("PrimaryButton")
        self.save_btn.setAutoDefault(False)
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._on_save)
        buttons.addWidget(self.save_btn)
        layout.addLayout(buttons)

        row.addWidget(card)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)
        self.close_btn.setDefault(True)

    @staticmethod
    def _caption(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("font-size: 12px; font-weight: bold; color: #8b949e; letter-spacing: 1px; background: transparent;")
        return lbl

    # ---- run -------------------------------------------------------------------------
    def _on_collect_clicked(self):
        if self.worker is not None:          # running: the button is "Cancel"
            self.worker.cancel()
            self.status_lbl.setText("Cancelling after the current command…")
            return
        self.start()

    def start(self, sync: bool = False):
        """Runs the collection in a thread (``sync`` runs it inline: tests)."""
        self.bundle = None
        self.save_btn.setEnabled(False)
        self.file_list.clear()
        self.preview.clear()
        self.progress_bar.setVisible(True)
        self.collect_btn.setText("Cancel")
        self.worker = DiagnosticWorker(self.job)
        self.worker.progress.connect(self.status_lbl.setText)
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        if sync:
            self.worker.run()
        else:
            self.worker.finished.connect(self._worker_finished)
            self.worker.start()

    def _worker_finished(self):
        if self.worker is not None:
            self.worker.deleteLater()
        self.worker = None

    def _reset_buttons(self):
        self.progress_bar.setVisible(False)
        self.collect_btn.setText("Collect again")
        if self.worker is not None and not self.worker.isRunning():
            self.worker = None

    def _on_done(self, bundle: diagnostics.Bundle):
        self.bundle = bundle
        self._reset_buttons()
        replaced = sum(bundle.replacements.values())
        self.status_lbl.setText(f"Ready: {len(bundle.files)} files, {replaced} personal value(s) replaced and "
                                f"verified. Review the files, then 'Save as…'.")
        self.file_list.addItems(["README.txt", "MANIFEST.json", "app_view.json"] +
                                sorted(n for n in bundle.files if n not in ("README.txt", "MANIFEST.json", "app_view.json")))
        self.file_list.setCurrentRow(0)
        self.save_btn.setEnabled(True)

    def _on_failed(self, message: str):
        self.bundle = None
        self._reset_buttons()
        self.status_lbl.setText(message)
        self.status_lbl.setStyleSheet(f"font-size: {BODY_PX - 1}px; color: #ff7b72; background: transparent;")

    def _show_file(self, name: str):
        if self.bundle and name in self.bundle.files:
            self.preview.setPlainText(self.bundle.files[name])

    # ---- save ------------------------------------------------------------------------
    def _on_save(self):
        if not self.bundle:
            return
        default = os.path.join(os.path.expanduser("~"), self.bundle.zip_name)
        path, _ = QFileDialog.getSaveFileName(self, "Save diagnostic bundle", default, "Zip archives (*.zip)")
        if not path:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"
        try:
            diagnostics.write_zip(self.bundle, path)
        except OSError as e:
            self._on_failed(f"Could not save: {e}")
            return
        self.saved_path = path
        self.status_lbl.setStyleSheet(f"font-size: {BODY_PX - 1}px; color: #56d364; background: transparent;")
        self.status_lbl.setText(f"Saved: {path}. SmartLinux did not send it anywhere.")

    def _on_close(self):
        if self.worker is not None:
            self.worker.cancel()
            self.worker.wait(35000)
        self.reject()

    def closeEvent(self, event):
        if self.worker is not None:
            self.worker.cancel()
            self.worker.wait(35000)
        super().closeEvent(event)
