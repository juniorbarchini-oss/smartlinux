#!/usr/bin/env bash
set -e

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPT_DIR="/opt/smartlinux"
BIN_FILE="/usr/local/bin/smartlinux"
DESKTOP_FILE="/usr/share/applications/smartlinux.desktop"
ICONS_BASE="/usr/share/icons/hicolor"

echo "=========================================="
echo "  SmartLinux System Installer (/opt)"
echo "=========================================="

if [ "$EUID" -ne 0 ]; then
    echo "⚠️ This installer requires root privileges to install into /opt and /usr/local/bin."
    echo "   Rerunning with sudo..."
    exec sudo bash "$0" "$@"
fi

# 1. Deploy application files
mkdir -p "$OPT_DIR"
rsync -a --delete --exclude=".git" --exclude="__pycache__" "$SOURCE_DIR/" "$OPT_DIR/"
chown -R root:root "$OPT_DIR"
chmod -R 755 "$OPT_DIR"

# 2. Deploy binary wrapper
cat << 'EOF' > "$BIN_FILE"
#!/usr/bin/env bash
cd /opt/smartlinux
exec /opt/smartlinux/.venv/bin/python3 -m smartlinux.main "$@"
EOF
chmod 755 "$BIN_FILE"

# 3. Deploy system icons
mkdir -p "$ICONS_BASE/scalable/apps" "$ICONS_BASE/128x128/apps"
if [ -f "$OPT_DIR/smartlinux/assets/icon.svg" ]; then
    cp "$OPT_DIR/smartlinux/assets/icon.svg" "$ICONS_BASE/scalable/apps/smartlinux.svg"
fi
if [ -f "$OPT_DIR/smartlinux/assets/icon.png" ]; then
    cp "$OPT_DIR/smartlinux/assets/icon.png" "$ICONS_BASE/128x128/apps/smartlinux.png"
fi

# 4. Deploy Desktop Entry
cat << 'EOF' > "$DESKTOP_FILE"
[Desktop Entry]
Version=1.0
Type=Application
Name=SmartLinux
GenericName=Disk Diagnostic Tool
Comment=S.M.A.R.T. Disk Health Diagnostics & Monitoring
Exec=/usr/local/bin/smartlinux
Icon=smartlinux
Terminal=false
Categories=System;HardwareSettings;Utility;
Keywords=smart;disk;ssd;hdd;nvme;health;telemetry;diagnostic;storage;
StartupWMClass=smartlinux
StartupNotify=true
EOF
chmod 644 "$DESKTOP_FILE"

# 5. Refresh caches
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database /usr/share/applications/ || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$ICONS_BASE" || true
fi

echo "=========================================="
echo "✅ SmartLinux successfully installed in the OS!"
echo "   • Location:    $OPT_DIR"
echo "   • Binary:      $BIN_FILE"
echo "   • Application: $DESKTOP_FILE"
echo "=========================================="

