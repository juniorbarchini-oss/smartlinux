# 🐧 SmartLinux

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Framework](https://img.shields.io/badge/GUI-PySide6%20%2F%20Qt6-brightgreen.svg)](https://wiki.qt.io/Qt_for_Python)
[![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20macOS-informational.svg)](https://github.com/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**SmartLinux** is a modern, high-performance desktop application for storage drive health diagnostics, telemetry inspection, and S.M.A.R.T. attribute monitoring across local drives and remote SSH Homelab servers.

---

## ✨ Key Features

* **⚡ Pure On-Demand Diagnostics:** 
  Zero background spin-ups and zero unnecessary disk wear. Physical drives and remote servers are listed immediately in an unscanned state; S.M.A.R.T. telemetry is only read when you click **Scan Now**.
* **🔌 Real-Time Hotplug Auto-Detection:** 
  Zero-overhead Linux kernel Netlink uevent listener automatically detects connected or removed USB drives in real time without requiring manual refreshes.
* **⏏️ Safely Eject USB Drives:** 
  Safely unmounts and powers off external USB storage devices directly from the interface.
* **🌐 Remote Homelab Monitoring via SSH:** 
  Connect seamlessly to multiple remote servers (e.g., ZimaOS, Proxmox, TrueNAS, Raspberry Pi, Ubuntu Server, macOS) using Password or Private Key authentication with automatic sudo elevation.
* **📄 Multi-Format Diagnostic Reports:** 
  Export comprehensive diagnostic reports with custom drive selection and directory browsing:
  * **📝 Markdown (`.md`):** Clean GitHub-Flavored Markdown tables.
  * **📕 PDF Document (`.pdf`):** Styled report with telemetry metric boxes and colored status badges.
  * **📘 Word Document (`.doc`):** Fully formatted Microsoft Word / LibreOffice Writer report.
  * **📊 Excel Spreadsheet (`.xls`):** Multi-sheet Excel workbook with per-attribute columns.
* **🎨 Modern High-Contrast Dark UI:** 
  Crisp typography (+15px readable scale), responsive telemetry cards (Temperature, Power-On Hours, Power Cycles), and full ATA / NVMe SMART attribute tables.

---

## 🛠️ System Requirements

* **Operating System:** Linux (Ubuntu, Debian, Fedora, Arch, etc.) or macOS (Darwin).
* **Python:** Python 3.10 or higher.
* **smartmontools:** `smartctl` utility installed on the system.

### Installing Dependencies

#### Ubuntu / Debian / Pop!_OS:
```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv smartmontools libxcb-cursor0
```

#### Fedora / RHEL:
```bash
sudo dnf install python3 python3-pip smartmontools
```

#### Arch Linux:
```bash
sudo pacman -S python python-pip smartmontools
```

#### macOS:
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

1. **Clone the repository:**
   ```bash
   git clone https://github.com/hbarchini/smartlinux.git
   cd smartlinux
   ```

2. **Run the launcher script:**
   The included `smartlinux.sh` script automatically provisions the virtual environment, installs dependencies, and launches the application:
   ```bash
   chmod +x smartlinux.sh
   ./smartlinux.sh
   ```

---

## 📂 Project Architecture

```
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
│   │   └── export_dialog.py    # Multi-disk and multi-format export dialog
│   ├── __init__.py
│   └── main.py                 # Application entry point
├── smartlinux.sh               # One-click launcher script
├── requirements.txt            # Python dependencies (PySide6, paramiko)
└── README.md                   # Documentation
```

---

## 📄 License

This project is licensed under the **MIT License** - see the [LICENSE](LICENSE) file for details.
