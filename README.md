# 🐧 SmartLinux

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![GUI](https://img.shields.io/badge/GUI-PySide6%20%2F%20Qt6-brightgreen.svg)](https://wiki.qt.io/Qt_for_Python)
[![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20macOS-informational.svg)](https://github.com/juniorbarchini-oss/smartlinux)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Support on Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/hbarchini)

**SmartLinux** is a modern, high-performance desktop application for storage drive health diagnostics, telemetry inspection, and S.M.A.R.T. attribute monitoring across local drives (SATA, NVMe, USB) and remote SSH Homelab servers (Linux, macOS, BSD).

Designed with an **on-demand philosophy**, SmartLinux avoids unnecessary drive spin-ups and disk wear by only scanning when explicitly requested.

---

## ✨ Key Features

* **⚡ Pure On-Demand Diagnostics:**  
  Zero background spin-ups and zero unnecessary wear on standby HDDs. Storage devices and remote servers are listed immediately in an unscanned state; S.M.A.R.T. telemetry is read only when you click **Scan Now**.
* **🔌 Real-Time USB Hotplug Auto-Detection:**  
  Zero-overhead Linux kernel Netlink uevent listener automatically detects plugged or removed USB drives in real time without requiring manual refreshes.
* **⏏️ Safely Eject USB Storage:**  
  Safely unmounts and powers off external USB storage devices directly from the UI or right-click context menu.
* **🌐 Remote Homelab Monitoring via SSH:**  
  Connect seamlessly to remote servers (e.g., ZimaOS, Proxmox, TrueNAS, Raspberry Pi, Ubuntu Server, Debian, macOS) using Password or Private Key authentication with automatic `sudo` elevation.
* **📄 Multi-Format Diagnostic Reports:**  
  Export comprehensive diagnostic reports with multi-drive selection and custom directory browsing:
  * **📝 Markdown (`.md`):** Clean GitHub-Flavored Markdown tables.
  * **📕 PDF Document (`.pdf`):** Styled high-DPI report with telemetry metric boxes and colored status badges.
  * **📘 Word Document (`.doc`):** Formatted Microsoft Word / LibreOffice Writer report.
  * **📊 Excel Spreadsheet (`.xls`):** Multi-sheet Excel workbook with per-attribute columns.
* **🎨 Modern High-Contrast Dark UI:**  
  Crisp typography (+15px readable scale), responsive telemetry cards (Temperature, Power-On Hours, Power Cycles), and full ATA / NVMe SMART attribute tables.
* **🪄 1-Click Dependency Auto-Configuration:**  
  If `smartctl` is missing or lacks non-root SUID permissions, SmartLinux presents a single-click auto-setup modal that installs packages and sets permissions via system authorization (`pkexec`).

---

## 🛠️ System Requirements

* **Operating System:** Linux (Ubuntu, Debian, Fedora, Arch, Linux Mint, Pop!_OS, etc.) or macOS.
* **Python:** Python 3.10 or higher.
* **smartmontools:** `smartctl` utility installed on the system.

### Installing Dependencies

#### Ubuntu / Debian / Linux Mint / Pop!_OS:
```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv smartmontools libxcb-cursor0
```

#### Fedora / RHEL / Nobara:
```bash
sudo dnf install python3 python3-pip smartmontools
```

#### Arch Linux / Manjaro:
```bash
sudo pacman -S python python-pip smartmontools
```

#### macOS (Homebrew):
```bash
brew install python smartmontools
```

---

## 🔒 Permission Setup (Local SUID)

To allow SmartLinux to read local raw disk health without prompting for root passwords on every startup, set the SUID bit on `smartctl`:

```bash
sudo chmod u+s /usr/sbin/smartctl
```

*(On systems where `smartctl` is in `/usr/local/sbin/smartctl`, adjust the path accordingly).*

---

## 🚀 Installation & Quick Start

### Option 1: Desktop Integration (1-Click Installer)
To install SmartLinux into your system's Application Menu with its official commercial icon:
```bash
git clone https://github.com/juniorbarchini-oss/smartlinux.git
cd smartlinux
chmod +x install.sh
./install.sh
```

### Option 2: Run Directly (Development Mode)
```bash
git clone https://github.com/juniorbarchini-oss/smartlinux.git
cd smartlinux
chmod +x smartlinux.sh
./smartlinux.sh
```

### Option 3: Compile Standalone Portable Package
To bundle SmartLinux into a standalone portable folder suitable for USB flash drives:
```bash
chmod +x build_portable.sh
./build_portable.sh
```
The compiled standalone binary will be generated in `dist/SmartLinux/`.

---

## 📂 Project Architecture

```text
smartlinux/
├── smartlinux/
│   ├── core/
│   │   ├── models.py           # Data models (DiskInfo, SmartAttribute, ServerConfig)
│   │   ├── detector.py         # Local disk discovery and safe ejection
│   │   ├── smart_parser.py     # ATA/SATA and NVMe SMART JSON parser
│   │   ├── ssh_client.py       # Cross-platform SSH client with sudo elevation
│   │   ├── watcher.py          # Netlink kernel uevent hotplug watcher
│   │   ├── exporter.py         # Multi-format report exporter (.md, .pdf, .doc, .xls)
│   │   └── config_manager.py   # Server persistence (~/.config/smartlinux/)
│   ├── ui/
│   │   ├── theme.py            # Dark theme stylesheet (QSS)
│   │   ├── main_window.py      # Main window & background worker orchestration
│   │   ├── sidebar.py          # Device tree with health badges and context menus
│   │   ├── detail_panel.py     # Telemetry metric cards and attribute table
│   │   ├── server_dialog.py    # Remote SSH server configuration modal
│   │   ├── export_dialog.py    # Multi-disk and multi-format export dialog
│   │   └── setup_dialog.py     # 1-click dependency & SUID installer modal
│   ├── assets/
│   │   ├── icon.svg            # Vector application icon
│   │   └── icon.png            # High-resolution raster application icon
│   ├── __init__.py
│   └── main.py                 # Application entry point
├── smartlinux.sh               # One-click launcher script
├── install.sh                  # System desktop & icon installer
├── build_portable.sh           # Standalone binary compiler (PyInstaller)
├── smartlinux.desktop          # Linux desktop entry specification
├── requirements.txt            # Python dependencies (PySide6, paramiko)
└── README.md                   # Documentation
```

---

## 👥 Credits

* **Concept, Design & Development:** Humberto Barchini (HB) & Antigravity (AGY)
* **License:** Open Source under the [MIT License](LICENSE).

---

## 🌟 Support & Donations

If you find SmartLinux useful for managing your storage hardware and Homelab servers, feel free to support the project:

[![Support me on Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/hbarchini)
