from typing import List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton

from ..core.usb_compat import UasCandidate, Volume, describe_plan


def plan_lines(candidate: UasCandidate, volumes: List[Volume]) -> List[str]:
    """The confirmation text, one line per step / detail ("a. ...", "    • ...")."""
    lines = []
    for letter, (step, details) in zip("abcdef", describe_plan(candidate, volumes)):
        lines.append(f"{letter}. {step}")
        lines.extend(f"    •  {d}" for d in details)
    return lines


CARD_WIDTH = 700     # the text block keeps this width whatever size the window manager gives the dialog
BODY_PX = 15
TITLE_PX = 19


class CompatModeDialog(QDialog):
    """Explicit consent before switching a USB adapter to Bulk-Only mode.

    Some window managers force a fixed size on every window of the app (e.g. a Hyprland rule
    matching the app class sizes dialogs like the main window). The text therefore lives in a
    fixed-width card centred in the window: it reads at a comfortable size either way, and
    without such a rule the window simply shrinks to the card.
    """

    def __init__(self, candidate: UasCandidate, volumes: List[Volume], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Read SMART in compatibility mode")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 28, 28, 28)
        outer.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)

        card = QFrame()
        card.setObjectName("CompatCard")
        card.setFixedWidth(CARD_WIDTH)
        card.setStyleSheet(
            "#CompatCard { background-color: #0d1117; border: 1px solid #30363d; border-radius: 10px; }"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.setSpacing(8)

        title = QLabel("This is what will happen:")
        title.setStyleSheet(f"font-size: {TITLE_PX}px; font-weight: bold; color: #58a6ff; background: transparent;")
        layout.addWidget(title)

        for line in plan_lines(candidate, volumes):
            is_detail = line.startswith("    ")
            lbl = QLabel(line.strip())
            lbl.setTextFormat(Qt.PlainText)  # volume labels come from the disk: never render them as HTML
            lbl.setWordWrap(True)
            lbl.setStyleSheet(
                f"font-size: {BODY_PX}px; background: transparent; "
                + ("color: #c9d1d9;" if is_detail else "color: #f0f6fc; font-weight: 600;")
            )
            if is_detail:
                detail_row = QHBoxLayout()
                detail_row.addSpacing(20)
                detail_row.addWidget(lbl, 1)
                layout.addLayout(detail_row)
            else:
                layout.addSpacing(4)
                layout.addWidget(lbl)

        layout.addSpacing(10)
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

        row.addWidget(card)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)

        cancel_btn.setDefault(True)
        cancel_btn.setFocus()
