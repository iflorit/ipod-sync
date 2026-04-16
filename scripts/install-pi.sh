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

# --- Create config dir and cookies template ---
COOKIES_FILE="$HOME/.config/ipod-sync/cookies.txt"
mkdir -p "$HOME/.config/ipod-sync"

if [ ! -f "$COOKIES_FILE" ]; then
    cat > "$COOKIES_FILE" << 'COOKIESEOF'
# Netscape HTTP Cookie File
# https://curl.se/docs/http-cookies.html
#
# ipod-sync only needs the media-user-token cookie from music.apple.com.
#
# How to get it:
#   1. Log in to https://music.apple.com in your browser
#   2. Install "Get cookies.txt LOCALLY" (Chrome) or "cookies.txt" (Firefox)
#   3. Export cookies from music.apple.com in Netscape format
#   4. Copy the "media-user-token" line here, replacing the example below
#
# Format: domain<TAB>flag<TAB>path<TAB>secure<TAB>expiry<TAB>name<TAB>value
#
.music.apple.com	TRUE	/	TRUE	1893456000	media-user-token	REPLACE_WITH_YOUR_TOKEN
COOKIESEOF
    echo "  -> Fichero de cookies creado en: $COOKIES_FILE"
    echo "     Reemplaza REPLACE_WITH_YOUR_TOKEN con tu token real"
else
    echo "  -> Fichero de cookies ya existe: $COOKIES_FILE"
fi

echo ""
echo "=== Instalacion completada ==="
echo ""
echo "Proximos pasos:"
echo "  1. Edita ~/.config/ipod-sync/cookies.txt:"
echo "     Reemplaza REPLACE_WITH_YOUR_TOKEN con tu media-user-token de music.apple.com"
echo "  2. Ejecuta: ipod-sync download --list-playlists"
