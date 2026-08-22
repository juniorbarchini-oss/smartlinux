#!/usr/bin/env bash
set -e

# Base directory where repository is located
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPS_DIR="$HOME/.local/share/applications"
ICONS_BASE="$HOME/.local/share/icons/hicolor"

echo "=========================================="
echo "  SmartLinux Desktop Integration Installer"
echo "=========================================="

mkdir -p "$APPS_DIR"

# Install icons into system icon theme directories
for size in 32 48 64 128 256 512; do
    target_dir="$ICONS_BASE/${size}x${size}/apps"
    mkdir -p "$target_dir"
    if [ -f "$REPO_DIR/smartlinux/assets/icon_${size}x${size}.png" ]; then
        cp "$REPO_DIR/smartlinux/assets/icon_${size}x${size}.png" "$target_dir/smartlinux.png"
    fi
done

# Copy 512x512 fallback icon
mkdir -p "$HOME/.local/share/icons"
cp "$REPO_DIR/smartlinux/assets/icon.png" "$HOME/.local/share/icons/smartlinux.png"

# Generate local desktop entry with absolute paths
cat << DESKTOP_EOF > "$APPS_DIR/smartlinux.desktop"
[Desktop Entry]
Name=SmartLinux
GenericName=Disk Diagnostic Tool
Comment=S.M.A.R.T. Disk Health Diagnostics & Monitoring
Exec=$REPO_DIR/smartlinux.sh
Icon=smartlinux
Terminal=false
Type=Application
Categories=System;HardwareSettings;Utility;
Keywords=smart;disk;ssd;hdd;nvme;health;telemetry;diagnostic;storage;
StartupWMClass=smartlinux
DESKTOP_EOF

chmod +x "$APPS_DIR/smartlinux.desktop"
chmod +x "$REPO_DIR/smartlinux.sh"

# Update desktop and icon databases
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPS_DIR" || true
fi

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" || true
fi

echo ""
echo "✓ SmartLinux successfully installed to your Application Menu!"
echo "  You can now launch SmartLinux directly from your system search or app drawer."
echo "=========================================="
