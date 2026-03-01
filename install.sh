#!/usr/bin/env bash
# LinuxVoice — one-shot installer
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${GREEN}[+]${NC} $*"; }
warn()    { echo -e "${YELLOW}[!]${NC} $*"; }
die()     { echo -e "${RED}[✗]${NC} $*"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# 1. Detect package manager
# ---------------------------------------------------------------------------
if command -v dnf &>/dev/null; then
    PKG=dnf
elif command -v apt-get &>/dev/null; then
    PKG=apt
elif command -v pacman &>/dev/null; then
    PKG=pacman
else
    die "Unsupported distro — install dependencies manually (see README)."
fi
info "Detected package manager: $PKG"

# ---------------------------------------------------------------------------
# 2. Install system packages
# ---------------------------------------------------------------------------
info "Installing system dependencies (sudo required)..."
case "$PKG" in
    dnf)
        sudo dnf install -y \
            xdotool xclip \
            portaudio-devel \
            python3-tkinter \
            python3-devel gcc
        ;;
    apt)
        sudo apt-get update -qq
        sudo apt-get install -y \
            xdotool xclip \
            portaudio19-dev \
            python3-tk \
            python3-dev gcc
        ;;
    pacman)
        sudo pacman -S --needed --noconfirm \
            xdotool xclip \
            portaudio \
            tk \
            python gcc
        ;;
esac

# ---------------------------------------------------------------------------
# 3. Add user to 'input' group (needed for Wayland global hotkey via evdev)
# ---------------------------------------------------------------------------
if ! groups "$USER" | grep -qw input; then
    info "Adding $USER to 'input' group..."
    sudo usermod -aG input "$USER"
    warn "Group change requires re-login (or use 'sg input' — see below)."
    INPUT_GROUP_ADDED=1
else
    info "Already in 'input' group."
    INPUT_GROUP_ADDED=0
fi

# ---------------------------------------------------------------------------
# 4. Install Python dependencies
# ---------------------------------------------------------------------------
info "Installing Python packages..."
pip3 install --user \
    faster-whisper \
    sounddevice \
    soundfile \
    numpy \
    pynput \
    evdev \
    Pillow \
    pystray

# ---------------------------------------------------------------------------
# 5. Install 'linuxvoice' command
# ---------------------------------------------------------------------------
info "Installing 'linuxvoice' command to /usr/local/bin..."
sudo ln -sf "$SCRIPT_DIR/linuxvoice" /usr/local/bin/linuxvoice

# ---------------------------------------------------------------------------
# 6. Install & enable systemd user service
# ---------------------------------------------------------------------------
SERVICE_DIR="$HOME/.config/systemd/user"
mkdir -p "$SERVICE_DIR"

# Patch ExecStart to use the real python3 path and this repo's location
PYTHON3="$(command -v python3)"
sed "s|%h/linuxvoice|$SCRIPT_DIR|g; s|/usr/bin/python3|$PYTHON3|g" \
    "$SCRIPT_DIR/linuxvoice.service" > "$SERVICE_DIR/linuxvoice.service"

systemctl --user daemon-reload
systemctl --user enable linuxvoice.service
info "Systemd service installed and enabled."

# ---------------------------------------------------------------------------
# 6. Done
# ---------------------------------------------------------------------------
echo
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  LinuxVoice installed successfully!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo

if [[ "${INPUT_GROUP_ADDED:-0}" == "1" ]]; then
    warn "You were just added to the 'input' group."
    warn "Until you log out and back in, launch with:"
    echo
    echo "    sg input -c \"python3 $SCRIPT_DIR/main.py\""
    echo
    warn "After re-login you can just run:"
    echo
    echo "    linuxvoice"
else
    info "Run with:"
    echo
    echo "    linuxvoice"
fi

echo
info "Or start the background service:"
echo
echo "    systemctl --user start linuxvoice"
echo "    journalctl --user -fu linuxvoice   # view logs"
echo
info "Hold Left Ctrl to record. Release to transcribe and type."
