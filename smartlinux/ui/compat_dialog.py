from typing import List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton

from ..core.usb_compat import UasCandidate, Volume, describe_plan


def plan_lines(candidate: UasCandidate, volumes: List[Volume]) -> List[str]:
    """The confirmation text, one line per step / detail ("a. ...", "    • ...")."""
    lines = []
    for letter, (step, details) in zip("abcdef", describe_plan(candidate, volumes)):
        lines.append(f"{letter}. {step}")
        lines.extend(f"    •  {d}" for d in details)
    return lines


class CompatModeDialog(QDialog):
    """Explicit consent before switching a USB adapter to Bulk-Only mode."""

    def __init__(self, candidate: UasCandidate, volumes: List[Volume], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Read SMART in compatibility mode")
        self.setFixedWidth(620)  # fixed width -> word-wrapped labels get a correct height

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(8)

        title = QLabel("This is what will happen:")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #58a6ff; background: transparent;")
        layout.addWidget(title)

        for line in plan_lines(candidate, volumes):
            is_detail = line.startswith("    ")
            lbl = QLabel(line.strip())
            lbl.setTextFormat(Qt.PlainText)  # volume labels come from the disk: never render them as HTML
            lbl.setWordWrap(True)
            lbl.setStyleSheet(
                "font-size: 13px; background: transparent; "
                + ("color: #c9d1d9;" if is_detail else "color: #f0f6fc; font-weight: 600;")
            )
            if is_detail:
                row = QHBoxLayout()
                row.addSpacing(18)
                row.addWidget(lbl, 1)
                layout.addLayout(row)
            else:
                layout.addSpacing(4)
                layout.addWidget(lbl)

        layout.addSpacing(8)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)
        continue_btn = QPushButton("Continue")
        continue_btn.setObjectName("PrimaryButton")
        continue_btn.setAutoDefault(False)
        continue_btn.clicked.connect(self.accept)
        buttons.addWidget(continue_btn)
        layout.addLayout(buttons)

        cancel_btn.setDefault(True)
        cancel_btn.setFocus()
