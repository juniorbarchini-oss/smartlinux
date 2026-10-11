#!/usr/bin/env bash
# Build smartlinux_<version>_all.deb from the committed tree (HEAD).
#   packaging/debian/build-deb.sh [version]      (default: version of the latest v* git tag)
# Needs dpkg-deb. On a machine without it (e.g. Arch) it re-runs itself inside a throwaway
# ubuntu container, so nothing is installed on the host.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="$ROOT/dist"

if ! command -v dpkg-deb >/dev/null 2>&1; then
    command -v docker >/dev/null 2>&1 || { echo "dpkg-deb or docker is required" >&2; exit 1; }
    exec docker run --rm -v "$ROOT:/src" -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
        ubuntu:26.04 bash -c 'apt-get update -qq >/dev/null && apt-get install -y -qq git >/dev/null \
        && git config --global --add safe.directory /src && /src/packaging/debian/build-deb.sh "$@" \
        && chown -R "$HOST_UID:$HOST_GID" /src/dist' _ "$@"
fi

VERSION="${1:-$(git -C "$ROOT" describe --tags --abbrev=0 --match 'v*' 2>/dev/null | sed 's/^v//; s/-/~/')}"
[ -n "$VERSION" ] || { echo "no version given and no v* tag found" >&2; exit 1; }

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
PKG="$STAGE/pkg"
LIB="$PKG/usr/lib/smartlinux"

mkdir -p "$LIB" "$PKG/usr/bin" "$PKG/usr/share/applications" "$PKG/DEBIAN" \
         "$PKG/usr/share/doc/smartlinux" "$PKG/usr/share/icons/hicolor/scalable/apps" \
         "$PKG/usr/share/polkit-1/actions"

# Application code, exactly as committed (no stray files from the working tree)
git -C "$ROOT" archive HEAD smartlinux | tar -x -C "$LIB"
find "$LIB" -name '__pycache__' -type d -prune -exec rm -rf {} +

# Root helper for the USB compatibility mode (run only through pkexec) + its polkit action
git -C "$ROOT" show HEAD:packaging/common/usb-mode-helper > "$LIB/usb-mode-helper"
chmod 755 "$LIB/usb-mode-helper"
git -C "$ROOT" show HEAD:packaging/common/org.smartlinux.usbmode.policy \
    > "$PKG/usr/share/polkit-1/actions/org.smartlinux.usbmode.policy"
chmod 644 "$PKG/usr/share/polkit-1/actions/org.smartlinux.usbmode.policy"

cat > "$PKG/usr/bin/smartlinux" <<'LAUNCHER'
#!/bin/sh
PYTHONPATH=/usr/lib/smartlinux${PYTHONPATH:+:$PYTHONPATH} exec python3 -m smartlinux.main "$@"
LAUNCHER
chmod 755 "$PKG/usr/bin/smartlinux"

cat > "$PKG/usr/share/applications/smartlinux.desktop" <<'DESKTOP'
[Desktop Entry]
Name=SmartLinux
GenericName=Disk Diagnostic Tool
Comment=S.M.A.R.T. Disk Health Diagnostics & Monitoring
Exec=smartlinux
Icon=smartlinux
Terminal=false
Type=Application
Categories=System;HardwareSettings;Utility;
Keywords=smart;disk;ssd;hdd;nvme;health;telemetry;diagnostic;storage;
StartupWMClass=smartlinux
DESKTOP

for s in 32 48 64 128 256 512; do
    install -Dm644 "$LIB/smartlinux/assets/icon_${s}x${s}.png" \
        "$PKG/usr/share/icons/hicolor/${s}x${s}/apps/smartlinux.png"
done
install -m644 "$LIB/smartlinux/assets/icon.svg" "$PKG/usr/share/icons/hicolor/scalable/apps/smartlinux.svg"

# Debian policy wants /usr/share/doc/<pkg>/copyright
{
    echo "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/"
    echo "Upstream-Name: smartlinux"
    echo "Source: https://github.com/juniorbarchini-oss/smartlinux"
    echo "License: MIT"
    echo
    echo "License: MIT"
    git -C "$ROOT" show HEAD:LICENSE | sed 's/^$/ ./; s/^/ /'
} > "$PKG/usr/share/doc/smartlinux/copyright"

INSTALLED_KB=$(du -sk "$PKG" | cut -f1)
cat > "$PKG/DEBIAN/control" <<CONTROL
Package: smartlinux
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Installed-Size: $INSTALLED_KB
Depends: python3 (>= 3.10), python3-pyside6.qtcore, python3-pyside6.qtgui, python3-pyside6.qtwidgets, python3-paramiko, python3-keyring, smartmontools
Recommends: pkexec | policykit-1, udisks2, lsof
Suggests: putty-tools
Maintainer: juniorbarchini-oss <juniorbarchini-oss@users.noreply.github.com>
Homepage: https://github.com/juniorbarchini-oss/smartlinux
Description: S.M.A.R.T. disk health diagnostics for local and remote drives
 SmartLinux reads S.M.A.R.T. telemetry from local (SATA, NVMe, USB) and remote
 (SSH) drives on demand, keeps a per-disk history keyed by serial number and
 exports reports. Install putty-tools to reach very old devices that only offer
 ssh-dss host keys. USB adapters that hide S.M.A.R.T. in UAS mode can be read in
 an opt-in compatibility mode (needs pkexec and udisks2).
CONTROL

# Python writes __pycache__ here at runtime; dpkg does not know those files, so clean
# the directory on remove/purge instead of leaving an orphan tree behind.
cat > "$PKG/DEBIAN/postrm" <<'POSTRM'
#!/bin/sh
set -e
case "$1" in
    remove|purge) rm -rf /usr/lib/smartlinux ;;
esac
exit 0
POSTRM
chmod 755 "$PKG/DEBIAN/postrm"

mkdir -p "$OUT"
find "$PKG" -type d -exec chmod 755 {} +
dpkg-deb --root-owner-group --build "$PKG" "$OUT/smartlinux_${VERSION}_all.deb" >/dev/null
echo "built: $OUT/smartlinux_${VERSION}_all.deb"
