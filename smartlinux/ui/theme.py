"""Modern Dark Theme QSS and UI Styling for SmartLinux."""

DARK_THEME_QSS = """
/* Global Window & Fonts */
QMainWindow, QDialog, QWidget {
    background-color: #0d1117;
    color: #e6edf3;
    font-family: 'Segoe UI', 'Inter', 'Ubuntu', sans-serif;
    font-size: 13px;
}

/* Sidebar & Left Tree */
QTreeWidget {
    background-color: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 6px;
    color: #e6edf3;
    outline: none;
}

QTreeWidget::item {
    padding: 8px 6px;
    border-radius: 6px;
    margin: 2px 0px;
}

QTreeWidget::item:hover {
    background-color: #21262d;
}

QTreeWidget::item:selected {
    background-color: #1f6feb;
    color: #ffffff;
    font-weight: 600;
}

QTreeWidget::branch {
    background-color: transparent;
}

/* Detail Panel Container */
#DetailContainer {
    background-color: #161b22;
    border: 1px solid #30363d;
    border-radius: 10px;
    padding: 16px;
}

/* Cards */
.MetricCard {
    background-color: #21262d;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 12px;
}

.HeaderCard {
    background-color: #21262d;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 16px;
}

/* Tables */
QTableWidget {
    background-color: #0d1117;
    border: 1px solid #30363d;
    border-radius: 8px;
    gridline-color: #21262d;
    color: #e6edf3;
    selection-background-color: #1f6feb;
    selection-color: #ffffff;
    outline: none;
}

QTableWidget::item {
    padding: 6px 8px;
    border-bottom: 1px solid #21262d;
}

QTableWidget::item:selected {
    background-color: #238636;
    color: #ffffff;
}

QHeaderView::section {
    background-color: #161b22;
    color: #8b949e;
    padding: 8px;
    border: none;
    border-bottom: 2px solid #30363d;
    font-weight: 600;
    font-size: 12px;
    text-transform: uppercase;
}

/* Buttons */
QPushButton {
    background-color: #21262d;
    color: #c9d1d9;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 8px 16px;
    font-weight: 600;
    font-size: 13px;
}

QPushButton:hover {
    background-color: #30363d;
    border-color: #8b949e;
    color: #ffffff;
}

QPushButton:pressed {
    background-color: #161b22;
}

QPushButton:disabled {
    background-color: #161b22;
    border-color: #21262d;
    color: #484f58;
}

/* Primary Action Button (Scan Now) */
QPushButton#PrimaryButton {
    background-color: #238636;
    color: #ffffff;
    border: 1px solid #2ea043;
}

QPushButton#PrimaryButton:hover {
    background-color: #2ea043;
    border-color: #3fb950;
}

QPushButton#PrimaryButton:pressed {
    background-color: #1b682b;
}

/* Accent Action Button (Export) */
QPushButton#AccentButton {
    background-color: #1f6feb;
    color: #ffffff;
    border: 1px solid #388bfd;
}

QPushButton#AccentButton:hover {
    background-color: #388bfd;
    border-color: #58a6ff;
}

/* Inputs, SpinBoxes & ComboBoxes */
QLineEdit, QSpinBox, QComboBox {
    background-color: #161b22;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 8px 10px;
    color: #e6edf3;
    selection-background-color: #1f6feb;
    min-height: 20px;
}

QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
    border: 1px solid #58a6ff;
    outline: none;
}

QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 25px;
    border-left-width: 1px;
    border-left-color: #30363d;
    border-left-style: solid;
}

QComboBox QAbstractItemView {
    background-color: #161b22;
    border: 1px solid #30363d;
    selection-background-color: #1f6feb;
    selection-color: #ffffff;
    color: #e6edf3;
    padding: 4px;
}

/* Radio Buttons & Checkboxes */
QRadioButton, QCheckBox {
    color: #e6edf3;
    spacing: 8px;
    font-size: 13px;
    padding: 4px;
}

QRadioButton::indicator, QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 2px solid #8b949e;
    border-radius: 8px;
    background-color: #161b22;
}

QCheckBox::indicator {
    border-radius: 4px;
}

QRadioButton::indicator:hover, QCheckBox::indicator:hover {
    border-color: #58a6ff;
}

QRadioButton::indicator:checked {
    background-color: #1f6feb;
    border-color: #58a6ff;
}

QCheckBox::indicator:checked {
    background-color: #1f6feb;
    border-color: #58a6ff;
}

/* Scrollbars */
QScrollBar:vertical {
    border: none;
    background: #0d1117;
    width: 10px;
    margin: 0px;
    border-radius: 5px;
}

QScrollBar::handle:vertical {
    background: #30363d;
    min-height: 20px;
    border-radius: 5px;
}

QScrollBar::handle:vertical:hover {
    background: #484f58;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

QScrollBar:horizontal {
    border: none;
    background: #0d1117;
    height: 10px;
    margin: 0px;
    border-radius: 5px;
}

QScrollBar::handle:horizontal {
    background: #30363d;
    min-width: 20px;
    border-radius: 5px;
}

QScrollBar::handle:horizontal:hover {
    background: #484f58;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}

/* Tooltips */
QToolTip {
    background-color: #161b22;
    color: #f0f6fc;
    border: 1px solid #30363d;
    border-radius: 4px;
    padding: 6px;
}

/* Status Bar */
QStatusBar {
    background-color: #0d1117;
    color: #8b949e;
    border-top: 1px solid #21262d;
}

/* Alert Banner */
#AlertBanner {
    background-color: #490202;
    border: 1px solid #f85149;
    border-radius: 8px;
    padding: 10px;
    color: #ff7b72;
}

#WarningBanner {
    background-color: #382402;
    border: 1px solid #d29922;
    border-radius: 8px;
    padding: 10px;
    color: #e3b341;
}

#SuccessBanner {
    background-color: #04260f;
    border: 1px solid #2ea043;
    border-radius: 8px;
    padding: 10px;
    color: #56d364;
}
"""
