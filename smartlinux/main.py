import sys
import os
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon, QGuiApplication

# Ensure package is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from smartlinux.ui.theme import DARK_THEME_QSS
from smartlinux.ui.main_window import MainWindow


def main():
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"
    
    app = QApplication(sys.argv)
    
    # Critical for GNOME / Ubuntu Dock / Wayland window grouping & icon recognition
    app.setApplicationName("smartlinux")
    app.setApplicationDisplayName("SmartLinux - S.M.A.R.T. Health Diagnostics")
    app.setOrganizationName("SmartLinux")
    QGuiApplication.setDesktopFileName("smartlinux")

    # Load Application Icon
    icon_path = os.path.join(os.path.dirname(__file__), "assets", "icon.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    
    # Apply modern Dark Theme QSS
    app.setStyleSheet(DARK_THEME_QSS)

    # Launch Main Window
    window = MainWindow()
    if os.path.exists(icon_path):
        window.setWindowIcon(QIcon(icon_path))
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
