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
  Selecting a drive (local or remote) only shows what is already known; once a drive has been read, the button
  becomes **↻ Refresh** (also in the right-click menu) and the panel shows **Last scanned** with the time of that read.
* **🔌 Real-Time USB Hotplug Auto-Detection:**  
  Zero-overhead Linux kernel Netlink uevent listener automatically detects plugged or removed USB drives in real time without requiring manual refreshes.
* **⏏️ Safely Eject USB Storage:**  
  Safely unmounts and powers off external USB storage devices directly from the UI or right-click context menu.
* **🌐 Remote Homelab Monitoring via SSH:**  
  Connect seamlessly to remote servers (e.g., ZimaOS, Proxmox, TrueNAS, Raspberry Pi, Ubuntu Server, Debian, macOS) using Password or Private Key authentication with automatic `sudo` elevation.
* **🕰️ Legacy Device Support:**  
  Reads very old remote equipment (e.g. WD My Cloud NAS with `smartctl 5.38`, BusyBox, `ssh-dss`-only SSH). See [Legacy devices](#-legacy-devices).
* **🔌 USB Adapters That Hide SMART:**  
  Opt-in compatibility mode that briefly switches a UAS adapter to Bulk-Only to read the full SMART table, then switches it back. See [USB adapters that hide SMART](#-usb-adapters-that-hide-smart).
* **📄 Multi-Format Diagnostic Reports:**  
  Export comprehensive diagnostic reports with multi-drive selection and custom directory browsing:
  * **📝 Markdown (`.md`):** Clean GitHub-Flavored Markdown tables.
  * **📕 PDF Document (`.pdf`):** Styled high-DPI report with telemetry metric boxes and colored status badges.
  * **📘 Word Document (`.doc`):** Formatted Microsoft Word / LibreOffice Writer report.
  * **📊 Excel Spreadsheet (`.xls`):** Multi-sheet Excel workbook with per-attribute columns.
* **📈 On-Demand S.M.A.R.T. History Log:**  
  Persists health snapshots on every manual scan into a lightweight, non-database JSON Lines file (`~/.local/share/smartlinux/history.jsonl`). An interactive **Historial** tab computes delta comparisons against previous scans, instantly flagging worsening sectors or error counts in red.
* **🩺 Native Drive Self-Test (Short & Long):**  
  Trigger internal drive self-tests (`smartctl -t short` or `-t long`) directly from the diagnostic panel for both local and remote SSH drives, with asynchronous background polling and human-readable completion timing ("X hours/days ago").
* **🛡️ Hardened SSH Security & System Keyring:**  
  Replaces permissive auto-add policies with strict `known_hosts` cryptographic verification, protecting Homelab hosts from Man-in-the-Middle (MITM) attacks with interactive fingerprint approval modals. SSH passwords are saved securely in the system keyring (`gnome-keyring` / SecretService) with defensive `0600` / `0700` file permissions.
* **🤖 Standalone Ansible & ntfy Monitor:**  
  Includes `scripts/ansible_smart_check.py`, a standalone zero-dependency Python script designed for headless automation (e.g. Ansible / Semaphore). Evaluates exact ATA/NVMe thresholds and fires immediate push alerts to a self-hosted **ntfy** instance (`NTFY_URL`) on WARNING or FAILED status.
* **🔔 Opt-In Update Notice:**  
  Optionally tells you when a newer release is available. Off until you enable it; it only shows a version number and a link, and never downloads or installs anything. See [Update notifications & privacy](#-update-notifications--privacy).
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
# Ubuntu / Debian / Fedora / Pop!_OS:
sudo chmod u+s /usr/sbin/smartctl

# Arch Linux / Omarchy / Manjaro:
sudo chmod u+s /usr/bin/smartctl
```

*(Alternatively, let SmartLinux's built-in 1-click auto-setup modal handle this automatically upon initial launch).*

---

## 🔔 Update notifications & privacy

SmartLinux can tell you when a newer release exists. This is **off until you opt in**:

* **Opt-in:** you are asked once at first launch. You can change it at any time from the **Updates** button in the status bar. **Check for updates now** always works on click, even with automatic checks off.
* **What is sent:** one anonymous HTTPS `GET` to `https://api.github.com/repos/juniorbarchini-oss/smartlinux/releases/latest` with the header `User-Agent: SmartLinux/<version>`. Like any web request, GitHub sees your IP address. No identifiers, no disk or server data.
* **How often:** at most once every 24 hours.
* **What happens with the answer:** nothing is downloaded or installed; SmartLinux only shows a version number and a link to the release.
* **Where the setting lives:** `~/.config/smartlinux/settings.json`.
* **No other telemetry.**

---

## 🪟 Tiling Window Managers (Hyprland / Omarchy / Sway)

SmartLinux features a high-density diagnostic dashboard layout designed for a native window size of `1180x750`. To prevent tiling window managers from auto-tiling or stretching the dashboard across ultra-wide monitors, add a floating rule:

### Omarchy (`~/.config/hypr/hyprland.lua`):
```lua
o.window("smartlinux", { float = true, center = true, size = { 1180, 750 } })
```

### Standard Hyprland (`~/.config/hypr/hyprland.conf`):
```conf
windowrulev2 = float, class:^(smartlinux)$
windowrulev2 = size 1180 750, class:^(smartlinux)$
windowrulev2 = center, class:^(smartlinux)$
```

---

## 🚀 Installation & Quick Start

### Option 0: Distribution Packages

#### Ubuntu / Debian / Linux Mint / Pop!_OS:
Download `smartlinux_<version>_all.deb` from the [GitHub release](https://github.com/juniorbarchini-oss/smartlinux/releases) and install it:
```bash
sudo apt install ./smartlinux_<version>_all.deb
```

#### Arch Linux / Manjaro:
```bash
git clone https://github.com/juniorbarchini-oss/smartlinux.git
cd smartlinux/packaging/arch && makepkg -si
```
*(AUR publishing is pending: AUR account registration is currently closed upstream.)*

`putty` / `putty-tools` is optional and only needed for legacy SSH devices; see [Legacy devices](#-legacy-devices).

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

### Releasing (maintainers)
1. Bump `__version__` in `smartlinux/__init__.py` and `pkgver` in `packaging/arch/PKGBUILD` together (a test enforces that they match).
2. Tag `vX.Y.Z` and create the GitHub release.
3. Build the `.deb` with `packaging/debian/build-deb.sh` and attach it to the release.

---

## 🕰️ Legacy devices

SmartLinux also works with old NAS boxes and embedded devices (tested on a **WD My Cloud EX4**:
Linux 3.2, BusyBox 1.20, OpenSSH 7.5, `smartctl 5.38`). Two independent problems are handled:

### 1. Old SSH servers (`ssh-dss` host keys)

paramiko ≥ 4 and OpenSSH ≥ 9.8 cannot negotiate `ssh-dss`. SmartLinux therefore has a second SSH
engine based on PuTTY's **`plink`**, with the same behaviour for the rest of the app:

* **Automatic:** SmartLinux tries paramiko first; if the server fails with *"no matching host key type"*,
  *"Incompatible ssh peer"* or another algorithm-negotiation error, it retries with `plink`.
* **Manual:** tick **Legacy SSH (old devices)** in the server dialog to use `plink` straight away.
* **Install `plink` (optional, only for these devices; PuTTY 0.75+):**

  | System | Command |
  |---|---|
  | Debian / Ubuntu | `sudo apt install putty-tools` |
  | Arch / Manjaro | `sudo pacman -S putty` |
  | Fedora | `sudo dnf install putty` |
  | macOS | `brew install putty` |

* **Security is not relaxed:**
  * The password is handed to `plink` through `-pwfile` (a `0600` temp file that lives only as long as each
    `plink` process, preferably in `$XDG_RUNTIME_DIR`), never with `-pw` on the command line. `-batch` is always used.
  * Host keys are never trusted blindly. `plink` checks `~/.putty/sshhostkeys`; for an unknown key SmartLinux
    shows the fingerprint and asks for confirmation. A confirmed fingerprint is pinned in
    `~/.config/smartlinux/legacy_hostkeys.json` (`0600`) and enforced with `-hostkey`. A key that differs from the
    cached/pinned one aborts the connection with a MITM alert.

### 2. Old `smartctl` (no `-j`, no `--scan`)

* If `smartctl -j` answers `UNRECOGNIZED OPTION`, SmartLinux switches to **text mode** for that host and parses
  `smartctl -a` output (`smartlinux/core/smart_text_parser.py`): model, serial, firmware, capacity, health,
  hours, temperature, power cycles, the full ATA attribute table and the self-test log. It tolerates smartctl 5.x–7.x
  layouts, plus basic NVMe and SCSI/SAS reports.
* Drives are discovered without `--scan` or `lsblk`, from `/proc/partitions` and `/sys/block/*/device/model`.
* All `-d` device types (`sat`, `usbjmicron`, …) are tried in text mode too; `smartctl -t` reuses the one that worked.
* `smartctl` is also searched in `/usr/local/modules/bin` (WD firmware).
* Remote commands are BusyBox-`ash` compatible (no `$((..))`, no `which`, no `lsblk`).

Tests run offline against real captures in `tests/fixtures/legacy_smartctl_5_38/`.

---

## 🔌 USB adapters that hide SMART

Some USB adapters (e.g. the Seagate bridge in the **DJI Fly Drive**, USB ID `0bc2:a020`) only answer basic
SCSI commands while the kernel drives them with the fast **UAS** driver: SmartLinux then only gets "Healthy",
with no temperature, power-on hours or attribute table. The drive is fine; the adapter just does not pass
the SMART requests through. With the older **Bulk-Only** driver (`usb-storage`, kernel quirk `VID:PID:u`)
the same drive returns the full ATA table.

When SmartLinux detects this (local USB disk, interface on `uas`, reading without a SMART table) it explains it
in the Diagnostics tab and offers **Read SMART in compatibility mode…**. Nothing happens until you confirm a
dialog that lists every step:

1. The drive's volumes are unmounted (`udisksctl`, as your user).
2. You are asked for administrator permission (`pkexec`, polkit action `org.smartlinux.usbmode`).
3. The packaged helper `/usr/lib/smartlinux/usb-mode-helper` adds **only** `VID:PID:u` to
   `/sys/module/usb_storage/parameters/quirks` (existing entries are kept, the previous value is saved in
   `/run/smartlinux/`) and re-attaches the USB port: ~20-30 s, the `/dev/sdX` name may change.
4. SMART is read (read-only, no self-tests).
5. The helper removes its entry and re-attaches the port again (always, also after an error), and the volumes
   are mounted again by UUID unless your desktop already did it.

SmartLinux refuses if a volume is in use (it shows which programs), if `/`, `/boot`, `/home`, swap or an
LUKS/LVM volume lives on the disk, or if the disk is not on USB. Notes: the drive is slower while in
compatibility mode; if SmartLinux is killed halfway the quirk stays until you reboot or replug the drive
(or run `pkexec /usr/lib/smartlinux/usb-mode-helper restore BUSID VID PID`); the serial number reported
differs between modes, so the reading shows up under its own entry in the History tab.

The helper and policy are installed by the `.deb`, the Arch package and `install.sh` (not when running from
the source tree). Optional tools: `udisks2`, `polkit`/`pkexec`, `lsof`.

---

## 🗄️ RAID controllers (MegaRAID/PERC)

Behind a hardware RAID controller (LSI/Broadcom **MegaRAID**, Dell **PERC**) the operating system only sees
**virtual drives** (`/dev/sda`, model e.g. "PERC H730 Mini"): they have no SMART of their own. The physical
disks are only reachable as `smartctl -d megaraid,N /dev/bus/H`, which `smartctl --scan` lists.

- SmartLinux keeps the `-d` type of every scan entry, so the physical disks that share `/dev/bus/0` are
  listed (and remembered) one by one, grouped under their controller in the sidebar
  ("PERC H730 Mini (MegaRAID) — 5 physical, 4 virtual"). Virtual drives go in their own group, with their
  size, and are never sent to smartctl.
- A scan reads `smartctl -j -a -d megaraid,N /dev/bus/H` (then `-d sat+megaraid,N` for SATA disks).
  SAS disks show health status, temperature, power-on time, start/stop cycles, **grown defect list**,
  corrected/uncorrected read/write/verify errors, non-medium errors and the self-test log. Health: a
  status other than OK is **FAILED**; grown defects or uncorrected errors are a **WARNING** (with the
  count); corrected and non-medium errors alone are normal on SAS.
- Selecting **Local Drives** or a server shows the **Server** panel: model, service tag (needs root),
  CPU, RAM, OS / Proxmox VE version, kernel, uptime and a **Storage controllers** table (driver and
  version, physical and virtual disks per controller; firmware only if the system reports it). It is read
  from `/sys`, `/proc`, `lscpu`, `lspci`, `modinfo`, `pveversion` and `dmidecode` when you connect or
  refresh a host (locally: at startup and on Refresh, not on hotplug), and every value is optional.

Read-only: SmartLinux never runs `storcli`/`perccli`/`MegaCli` and never changes the RAID configuration.
Empty slots are not listed (smartctl does not report them).

---

## 🧰 Vendor tool (RAID)

`smartctl` reads each physical disk behind a MegaRAID/PERC controller, but not the RAID itself. When
Dell **perccli** or Broadcom **storcli** is **already installed**, each MegaRAID controller in the
**Server** panel offers a read mode: **Standard read** (smartctl only, the default) or **Vendor tool**.
The choice is saved per server and controller in `~/.config/smartlinux/settings.json` (0600).

In Vendor tool mode SmartLinux also shows:

- the controller (product, FW version and package, BIOS, driver, personality, controller status);
- every volume: DG/VD, RAID type, state (Optl / Dgrd / Pdgd / OfLn), cache, size, name, the physical
  disks it is built from, the `/dev/sdX` it appears as and active operations (rebuild, init, CC);
  single-disk RAID0 volumes are marked "no redundancy";
- every physical disk: state, media / other error counts, predictive failure count, SMART alert,
  temperature and link speed. DID = the N of `smartctl -d megaraid,N`; double-click a row to open the
  disk. The sidebar shows the volume on each disk ("sdb · VMDATA1 · RAID0 · 1 disk · no redundancy");
- the battery (BBU): state, charge, temperature, replacement required, pack missing.

Health: a volume Dgrd/Pdgd is a WARNING (redundancy lost), OfLn is FAILED; a disk Failed/Offln/UBad is
FAILED, a predictive failure or a SMART alert is a WARNING, media errors are ATTENTION (other errors are
informational); a battery that is not Optimal or needs replacement is a WARNING. The controller shows
the worst of its parts. A missing component ("Cachevault doesn't exist") is not an error.

Policy:

- **Read-only whitelist.** Only these fixed commands run, with the controller number checked against
  `^[0-9]{1,2}$` and no shell (over SSH the validated tokens cannot contain shell syntax):
  `-v`, `show ctrlcount J`, `/cN show J`, `/cN show all J`, `/cN/vall show all J`,
  `/cN/eall/sall show all J`, `/cN/bbu show all J`, `/cN/dall show J`. Anything else (set, start, stop,
  add, del, import, clear, flash, secure, foreign, patrolread...) is refused before it runs.
- **Fixed executables:** `perccli64`, `perccli`, `storcli64`, `storcli`,
  `/opt/MegaRAID/perccli/perccli64`, `/opt/MegaRAID/storcli/storcli64`.
- **SmartLinux never installs it**, locally or over SSH. If it is missing, the panel says what it adds
  and where to get it (Dell: PERCCLI in Drivers & Downloads, category SAS RAID; Broadcom: StorCLI) and
  how to install it by hand (`sudo dpkg -i perccli_*.deb` / `sudo rpm -ivh perccli-*.rpm`).
- **Manual only:** it runs when you connect / refresh a server or click Refresh for this computer,
  never at startup, on hotplug or on a timer. It needs root (or sudo for a remote user with a saved
  password); otherwise the panel explains why it is not available.

---

## 📦 Diagnostic bundle

When SmartLinux does not understand a machine (an HP, Lenovo or Dell server, a RAID card, a USB
bridge), its owner can collect real output for the developer, with no remote access. In the
**Server** panel (this computer or any server) click **Export diagnostic bundle…**:

1. The dialog lists **exactly** which commands will run. All of them are read-only, from a fixed
   list: `/etc/os-release`, `uname -srm`, `lsblk -J -d -o NAME,PATH,MODEL,SIZE,TYPE,TRAN,ROTA,SERIAL,VENDOR,WWN`,
   `lspci -vmm -nn`, `smartctl --version`, `smartctl --scan` (JSON and text), `/sys/class/dmi/id/*`
   (vendor, product, BIOS, board, chassis type), `dmidecode -t system -t bios` (only if already root),
   `lscpu`, `MemTotal` from `/proc/meminfo`, `/sys/class/scsi_host/*/proc_name`, `modinfo` (version and
   description only) of megaraid_sas, hpsa, smartpqi, aacraid, mpt3sas and nvme; for each disk the
   same SMART read the app uses (`smartctl -j -a` with its `-d` type, text `-a` on old smartctl, only
   `smartctl -i` for RAID virtual drives); and perccli / storcli **only if already installed**, through
   the Vendor tool whitelist.
2. Nothing runs until you click **Collect and preview** (it runs in the background, with Cancel). Each
   command has a 30 s timeout and a 2 MB output limit (truncated outputs are marked); a missing tool
   (e.g. on BusyBox) is recorded and the bundle goes on. Nothing is written on the inspected system and
   nothing is installed.
3. You see the **sanitized** files before anything is saved, then **Save as…** writes
   `smartlinux-diag-YYYY-MM-DD-<model>.zip` (no host name in the file name).

The zip contains `README.txt`, `MANIFEST.json` (command, exit code, duration, file, sha256, truncated),
`app_view.json` (SmartLinux version, SSH engine, how the app classified each disk, its health and the
controllers) and one file per command.

**Privacy.** SmartLinux sends nothing and opens no connection of its own: it only saves a local file.
Before saving, personal values are replaced by **consistent fake values** (the same serial always gets
the same fake serial, so files still match each other):

- serial numbers (smartctl, lsblk, dmidecode, vendor tool `SN`), WWN / SAS addresses / logical unit ids
  and NAA ids, UUIDs, service tags / SKU numbers and asset tags, MAC addresses, IPv4 / IPv6 addresses,
  host and server names (every occurrence), filesystem labels and RAID volume names, user names in
  paths (`/home/<user>`, `/run/media/<user>`); SCSI inquiry data (which embeds serials) is removed.
- **Kept**, because they are needed to fix bugs: models, vendors, firmware and software versions,
  smartctl `-d` types, sizes and SMART counters. Placeholders ("Not Specified", "0000…") and values
  shorter than 3 characters are not treated as personal.
- **Defense in depth:** after sanitizing, SmartLinux searches every output file (and the file name) for
  every original value it replaced, including serials hex-encoded. If any is left, the export stops
  with a message and **no zip is written**. The manifest only counts replacements per category; the
  original values are never stored.
- The bundle never contains passwords, keys or the contents of any disk.

---

## 📂 Project Architecture

```text
smartlinux/
├── scripts/
│   └── ansible_smart_check.py  # Standalone headless SMART monitor with ntfy push alerts
├── smartlinux/
│   ├── core/
│   │   ├── models.py           # Data models (DiskInfo, SmartAttribute, ServerConfig)
│   │   ├── detector.py         # Local disk discovery and safe ejection
│   │   ├── history_manager.py  # On-demand JSON Lines health snapshot log (~/.local/share/)
│   │   ├── smart_parser.py     # ATA/SATA, NVMe and SAS/SCSI SMART JSON parser & threshold evaluator
│   │   ├── raid.py             # RAID controllers: -d megaraid,N disks, virtual drive detection
│   │   ├── system_info.py      # Read-only host identity and storage controllers (local / SSH)
│   │   ├── vendor_tools.py     # Optional perccli/storcli reads: whitelist, detection, RAID health
│   │   ├── diagnostics.py      # Diagnostic bundle: fixed read-only commands, manifest, zip
│   │   ├── sanitizer.py        # Consistent fake values for personal data + residue check
│   │   ├── ssh_client.py       # Hardened paramiko client + automatic engine selection (AutoSSHClient)
│   │   ├── plink_client.py     # Legacy SSH engine on PuTTY plink (ssh-dss devices)
│   │   ├── remote_common.py    # Transport-independent smartctl / discovery logic (JSON + text mode)
│   │   ├── smart_text_parser.py # Plain-text smartctl parser for old smartctl 5.x
│   │   ├── watcher.py          # Netlink kernel uevent hotplug watcher
│   │   ├── usb_compat.py       # UAS adapters hiding SMART: detection, guards, compatibility-mode flow
│   │   ├── exporter.py         # Multi-format report exporter (.md, .pdf, .doc, .xls)
│   │   └── config_manager.py   # Secure server persistence with system keyring integration
│   ├── ui/
│   │   ├── theme.py            # Dark theme stylesheet (QSS)
│   │   ├── main_window.py      # Main window & background worker orchestration
│   │   ├── sidebar.py          # Device tree with health badges and context menus
│   │   ├── detail_panel.py     # Telemetry metric cards, self-test actions and attribute table
│   │   ├── server_panel.py     # Server identity card and storage controller table
│   │   ├── diagnostic_dialog.py # Export diagnostic bundle: command list, sanitized preview, Save as
│   │   ├── history_tab.py      # Historical SMART attribute delta and degradation viewer
│   │   ├── server_dialog.py    # Remote SSH server configuration & host key approval modal
│   │   ├── export_dialog.py    # Multi-disk and multi-format export dialog
│   │   ├── compat_dialog.py    # Consent dialog for the USB compatibility mode
│   │   └── setup_dialog.py     # 1-click dependency & SUID installer modal
│   ├── assets/
│   │   ├── icon.svg            # Vector application icon
│   │   └── icon.png            # High-resolution raster application icon
│   ├── __init__.py
│   └── main.py                 # Application entry point
├── tests/
│   ├── test_security.py        # Automated tests for known_hosts, keyring, and permissions
│   ├── test_ansible_smart_check.py # Automated tests for ATA/NVMe thresholds and ntfy alerts
│   ├── test_smart_text_parser.py   # Text parser tests (real smartctl 5.38 captures in fixtures/)
│   ├── test_legacy_remote.py       # Simulated WD My Cloud EX4: discovery, text mode, self-test
│   ├── test_legacy_ssh_engine.py   # plink engine and paramiko→plink automatic selection
│   ├── test_usb_compat*.py         # USB compatibility mode on a fake sysfs (detection, guards, flow, UI)
│   ├── test_megaraid*.py           # Dell R630 / PERC H730 captures: discovery, SAS health, server info, UI
│   ├── test_vendor_tools*.py       # perccli captures: whitelist, detection, RAID health, Vendor tool UI
│   ├── test_sanitizer.py           # Synthetic personal values: replaced, consistent, residue check
│   ├── test_diagnostic*.py         # Bundle whitelist, R630 replay, zip / manifest, size cap, dialog
│   └── test_usb_mode_helper.py     # Root helper: argument checks, quirks editing, idempotence
├── smartlinux.sh               # One-click launcher script
├── install.sh                  # System desktop & icon installer
├── packaging/common/           # usb-mode-helper (root, via pkexec) + org.smartlinux.usbmode.policy
├── build_portable.sh           # Standalone binary compiler (PyInstaller)
├── smartlinux.desktop          # Linux desktop entry specification
├── requirements.txt            # Python dependencies (PySide6, paramiko, keyring)
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
