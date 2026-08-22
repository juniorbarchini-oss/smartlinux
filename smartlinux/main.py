import sys
import os
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

# Ensure package is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from smartlinux.ui.theme import DARK_THEME_QSS
from smartlinux.ui.main_window import MainWindow


def main():
    # Configure high DPI scaling
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"
    
    app = QApplication(sys.argv)
    app.setApplicationName("SmartLinux")
    app.setApplicationDisplayName("SmartLinux - Disk Diagnostic")
    app.setOrganizationName("Homelab")
    
    # Apply modern Dark Theme QSS
    app.setStyleSheet(DARK_THEME_QSS)

    # Launch Main Window
    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
