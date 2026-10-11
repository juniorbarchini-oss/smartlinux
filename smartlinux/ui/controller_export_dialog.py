"""Asks only for what the controller report needs: formats and a folder. The same fixed-width
card layout as the diagnostic bundle dialog (the window manager may force the window size)."""
import os
from typing import List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QVBoxLayout)

from ..core import controller_report
from ..core.exporter import ReportExporter

CARD_WIDTH = 640
BODY_PX = 14


class ControllerExportDialog(QDialog):
    def __init__(self, document: controller_report.Document, base_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export controller report")
        self.document = document
        self.base_name = base_name
        self.exported_files: List[str] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 28, 28, 28)
        outer.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        card = QFrame()
        card.setObjectName("ExportCard")
        card.setFixedWidth(CARD_WIDTH)
        card.setStyleSheet("#ExportCard { background-color: #0d1117; border: 1px solid #30363d; "
                           "border-radius: 10px; }")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.setSpacing(10)

        title = QLabel("Export controller report")
        title.setStyleSheet("font-size: 19px; font-weight: bold; color: #58a6ff; background: transparent;")
        layout.addWidget(title)
        text = QLabel("One document with the findings and their evidence, the volumes, the physical disks, "
                      "maintenance, trends and what was read. Nothing is changed on the server.")
        text.setWordWrap(True)
        text.setStyleSheet(f"font-size: {BODY_PX}px; color: #c9d1d9; background: transparent;")
        layout.addWidget(text)

        layout.addWidget(self._caption("FORMAT"))
        formats = QHBoxLayout()
        self.format_boxes = {}
        for key, label in controller_report.FORMATS.items():
            box = QCheckBox(label)
            box.setChecked(key == "pdf")
            box.setStyleSheet("background: transparent;")
            self.format_boxes[key] = box
            formats.addWidget(box)
        formats.addStretch(1)
        layout.addLayout(formats)

        layout.addWidget(self._caption("DESTINATION FOLDER"))
        dest = QHBoxLayout()
        self.dir_input = QLineEdit(ReportExporter.DEFAULT_EXPORT_DIR)
        dest.addWidget(self.dir_input)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        dest.addWidget(browse)
        layout.addLayout(dest)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.export_btn = QPushButton("Export report")
        self.export_btn.setObjectName("PrimaryButton")
        self.export_btn.clicked.connect(self._export)
        buttons.addWidget(self.export_btn)
        layout.addLayout(buttons)

        row.addWidget(card)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)

    @staticmethod
    def _caption(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("font-size: 12px; font-weight: bold; color: #8b949e; letter-spacing: 1px; "
                            "background: transparent;")
        return label

    def selected_formats(self) -> List[str]:
        return [k for k, box in self.format_boxes.items() if box.isChecked()]

    def _browse(self):
        start = self.dir_input.text().strip() or os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(self, "Select export folder", start)
        if chosen:
            self.dir_input.setText(chosen)

    def _export(self):
        formats = self.selected_formats()
        if not formats:
            QMessageBox.warning(self, "No format", "Check at least one format.")
            return
        folder = self.dir_input.text().strip()
        if not folder:
            QMessageBox.warning(self, "Folder required", "Choose the destination folder.")
            return
        ok, paths = controller_report.export_files(self.document, folder, formats, self.base_name)
        if not ok:
            QMessageBox.critical(self, "Export error", f"Could not write the report:\n\n{paths[0]}")
            return
        self.exported_files = paths
        self.accept()
