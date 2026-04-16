#!/bin/bash
# Install ipod-sync on Raspberry Pi (any model running Raspberry Pi OS / Debian)
set -e

echo "=== ipod-sync: Pi installation ==="

# --- System dependencies ---
echo "Installing system dependencies..."
sudo apt update
sudo apt install -y \
    ffmpeg \
    libgpod-dev \
    libgpod-common \
    pmount \
    udisks2 \
    usbutils

# --- udev rule: auto-mount iPod on USB connect ---
echo "Installing udev rule for iPod..."
sudo tee /etc/udev/rules.d/99-ipod.rules > /dev/null << 'EOF'
# Apple iPod Classic — auto-mount when connected via USB
ACTION=="add", SUBSYSTEM=="block", ATTRS{idVendor}=="05ac", ENV{DEVTYPE}=="partition", RUN+="/usr/bin/pmount --umask=000 /dev/%k ipod"
ACTION=="remove", SUBSYSTEM=="block", ATTRS{idVendor}=="05ac", RUN+="/usr/bin/pumount /media/ipod"
EOF
sudo udevadm control --reload-rules
echo "  -> /etc/udev/rules.d/99-ipod.rules installed"

# --- gamdl ---
echo "Installing gamdl..."
pip install -q --break-system-packages gamdl

# --- ipod-sync ---
echo "Installing ipod-sync..."
cd "$(dirname "$0")/.."
pip install -q --break-system-packages -e .

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
    echo "  -> Cookies file created at: $COOKIES_FILE"
    echo "     Replace REPLACE_WITH_YOUR_TOKEN with your real token"
else
    echo "  -> Cookies file already exists: $COOKIES_FILE"
fi

# --- sudoers: allow mount/umount without password (needed for HFS+ iPods) ---
echo "Installing sudoers rule for iPod mount..."
echo "$USER ALL=(root) NOPASSWD: /bin/mount, /bin/umount, /usr/bin/pmount, /usr/bin/pumount" | sudo tee /etc/sudoers.d/ipod-mount > /dev/null
sudo chmod 440 /etc/sudoers.d/ipod-mount
echo "  -> /etc/sudoers.d/ipod-mount installed"

# --- systemd service ---
PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
IPOD_SYNC=$(which ipod-sync 2>/dev/null || echo "$HOME/.local/bin/ipod-sync")
SERVICE_FILE="/etc/systemd/system/ipod-sync.service"

echo "Installing systemd service..."
sudo tee "$SERVICE_FILE" > /dev/null << SERVICEEOF
[Unit]
Description=ipod-sync daemon — periodic Apple Music download and iPod auto-sync
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
Environment=PATH=/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin
Environment=PYTHONPATH=$HOME/.local/lib/python${PYTHON_VERSION}/site-packages
Environment=HOME=$HOME
WorkingDirectory=$HOME
ExecStart=$IPOD_SYNC daemon start --foreground
Restart=on-failure
RestartSec=30
StandardOutput=append:$HOME/.config/ipod-sync/daemon.log
StandardError=append:$HOME/.config/ipod-sync/daemon.log

[Install]
WantedBy=multi-user.target
SERVICEEOF

sudo systemctl daemon-reload
sudo systemctl enable ipod-sync.service
echo "  -> Service installed and enabled: $SERVICE_FILE"
echo "  -> Start now: sudo systemctl start ipod-sync"

echo ""
echo "=== Installation complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit ~/.config/ipod-sync/cookies.txt"
echo "     Replace REPLACE_WITH_YOUR_TOKEN with your media-user-token from music.apple.com"
echo ""
echo "  2. Configure the daemon:"
echo "     ipod-sync setup"
echo ""
echo "  3. Start the daemon:"
echo "     sudo systemctl start ipod-sync"
echo "     ipod-sync daemon status"
