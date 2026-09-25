#!/bin/bash
#
# Install hw_daemon as a systemd service that starts at boot.
#
# Usage:
#   daemon/install.sh              - Set up prerequisites, venv, service; start it
#   daemon/install.sh --uninstall  - Stop and remove the service and udev rule
#
# Safe to re-run (e.g. after a git pull that changes requirements).
#
# Daemon options live in /etc/default/hw-daemon (created on first install).
# You can also set them at install time, which overwrites that file:
#   HW_DAEMON_ARGS="--config hw_config.json --log-level DEBUG" daemon/install.sh

set -e

SERVICE=hw-daemon
DAEMON_DIR="$(cd "$(dirname "$0")" && pwd)"
DIR="$(dirname "$DAEMON_DIR")"  # repo root
SRC="$DAEMON_DIR/systemd"
UNIT_DST="/etc/systemd/system/${SERVICE}.service"
RESCAN_DST="/etc/systemd/system/${SERVICE}-midi-rescan.service"
UDEV_DST="/etc/udev/rules.d/90-${SERVICE}-midi.rules"
DEFAULTS_DST="/etc/default/${SERVICE}"

if [ "$1" = "--uninstall" ]; then
    sudo systemctl disable --now "$SERVICE" 2>/dev/null || true
    sudo rm -f "$UNIT_DST" "$RESCAN_DST" "$UDEV_DST"
    sudo systemctl daemon-reload
    sudo udevadm control --reload
    echo "Removed $SERVICE service and udev rule (kept $DEFAULTS_DST)"
    exit 0
fi

# --- Prerequisites ---

if [ "$(uname -m)" != "aarch64" ]; then
    echo "Warning: this isn't a 64-bit OS ($(uname -m)). Dependencies may need to"
    echo "compile from source; 64-bit Raspberry Pi OS is recommended."
fi

if ! python3 -c "import ensurepip, venv" 2>/dev/null; then
    echo "Installing python3-venv..."
    sudo apt-get update -q
    sudo apt-get install -y -q python3-venv
fi

if command -v raspi-config > /dev/null; then
    if [ "$(sudo raspi-config nonint get_i2c)" != "0" ]; then
        echo "Enabling I2C (needed for the DACs)..."
        sudo raspi-config nonint do_i2c 0
    fi
else
    echo "Warning: raspi-config not found; make sure I2C is enabled for the DACs."
fi

# --- Python environment ---

if [ ! -x "$DIR/venv/bin/python" ]; then
    echo "Creating venv..."
    python3 -m venv "$DIR/venv"
fi
echo "Installing dependencies..."
"$DIR/venv/bin/pip" install -q -r "$DAEMON_DIR/requirements.txt"
# Editable so a git pull updates the protocol module without reinstalling
"$DIR/venv/bin/pip" install -q -e "$DIR/clients/python"

# --- systemd + udev ---

echo "Installing $UNIT_DST..."
sed -e "s|@USER@|$(id -un)|g" -e "s|@DIR@|$DIR|g" \
    "$SRC/${SERVICE}.service" | sudo tee "$UNIT_DST" > /dev/null
sudo install -m 644 "$SRC/${SERVICE}-midi-rescan.service" "$RESCAN_DST"

if [ -n "${HW_DAEMON_ARGS+x}" ]; then
    echo "Writing $DEFAULTS_DST with HW_DAEMON_ARGS=\"$HW_DAEMON_ARGS\"..."
    sed -e "s|^HW_DAEMON_ARGS=.*|HW_DAEMON_ARGS=\"$HW_DAEMON_ARGS\"|" \
        "$SRC/${SERVICE}.default" | sudo tee "$DEFAULTS_DST" > /dev/null
elif [ ! -f "$DEFAULTS_DST" ]; then
    echo "Creating $DEFAULTS_DST..."
    sudo install -m 644 "$SRC/${SERVICE}.default" "$DEFAULTS_DST"
fi

echo "Installing $UDEV_DST (restart on MIDI hotplug)..."
sudo install -m 644 "$SRC/90-${SERVICE}-midi.rules" "$UDEV_DST"
sudo udevadm control --reload

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE"
sudo systemctl restart "$SERVICE"

sleep 2
if systemctl is-active --quiet "$SERVICE"; then
    echo "Done. $SERVICE is running and will start at boot."
else
    echo "Warning: $SERVICE isn't running. Recent log:"
    journalctl -u "$SERVICE" -n 20 --no-pager
    exit 1
fi
echo "Logs: journalctl -u $SERVICE -f"
