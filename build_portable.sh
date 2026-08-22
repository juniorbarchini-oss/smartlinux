#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "=========================================="
echo "  Building SmartLinux Standalone Portable "
echo "=========================================="

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -r requirements.txt pyinstaller
fi

.venv/bin/pyinstaller --noconfirm --clean \
    --name "SmartLinux" \
    --windowed \
    --add-data "smartlinux/assets:smartlinux/assets" \
    --icon "smartlinux/assets/icon.png" \
    smartlinux/main.py

echo ""
echo "✓ Standalone binary bundle generated in: $DIR/dist/SmartLinux/"
echo "  You can copy the 'SmartLinux' folder or zip it for any USB drive / distribution."
echo "=========================================="
