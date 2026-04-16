#!/bin/bash
# Install ipod-sync on Raspberry Pi 5 (aarch64 / armv7)
set -e

echo "=== ipod-sync: instalacion Pi ==="

# --- System dependencies ---
echo "Instalando dependencias del sistema..."
sudo apt update
sudo apt install -y \
    ffmpeg \
    libgpod-dev \
    libgpod-common \
    pmount \
    udisks2 \
    usbutils

# --- udev rule: auto-mount iPod on connect ---
echo "Instalando udev rule para iPod..."
sudo tee /etc/udev/rules.d/99-ipod.rules > /dev/null << 'EOF'
# Apple iPod Classic — auto-mount when connected
ACTION=="add", SUBSYSTEM=="block", ATTRS{idVendor}=="05ac", ENV{ID_FS_TYPE}=="vfat", RUN+="/usr/bin/pmount --umask=000 %k ipod"
ACTION=="remove", SUBSYSTEM=="block", ATTRS{idVendor}=="05ac", RUN+="/usr/bin/pumount ipod"
EOF
sudo udevadm control --reload-rules
echo "  -> /etc/udev/rules.d/99-ipod.rules instalada"

# --- gamdl ---
echo "Instalando gamdl..."
pip install -q gamdl

# --- ipod-sync ---
echo "Instalando ipod-sync..."
cd "$(dirname "$0")/.."
pip install -e .

echo ""
echo "=== Instalacion completada ==="
echo ""
echo "Proximos pasos:"
echo "  1. Exporta tus cookies de music.apple.com (formato Netscape)"
echo "     y guardalas en: ~/.config/ipod-sync/cookies.txt"
echo "  2. Ejecuta: ipod-sync download --list-playlists"
