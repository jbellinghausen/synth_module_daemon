#!/bin/bash
#
# Install hw_daemon as a systemd service that starts at boot.
#
# Usage:
#   daemon/install.sh              - Create venv, install deps, enable + start service
#   daemon/install.sh --uninstall  - Stop, disable and remove the service
#
# Extra daemon arguments can be set with HW_DAEMON_ARGS, e.g.:
#   HW_DAEMON_ARGS="--config hw_config.json --log-level DEBUG" daemon/install.sh

set -e

SERVICE=hw-daemon
DAEMON_DIR="$(cd "$(dirname "$0")" && pwd)"
DIR="$(dirname "$DAEMON_DIR")"  # repo root
UNIT_DST="/etc/systemd/system/${SERVICE}.service"

if [ "$1" = "--uninstall" ]; then
    sudo systemctl disable --now "$SERVICE" 2>/dev/null || true
    sudo rm -f "$UNIT_DST"
    sudo systemctl daemon-reload
    echo "Removed $SERVICE service"
    exit 0
fi

if [ ! -x "$DIR/venv/bin/python" ]; then
    echo "Creating venv..."
    python3 -m venv "$DIR/venv"
fi
echo "Installing dependencies..."
"$DIR/venv/bin/pip" install -q -r "$DAEMON_DIR/requirements.txt"
# Editable so a git pull updates the protocol module without reinstalling
"$DIR/venv/bin/pip" install -q -e "$DIR/clients/python"

echo "Installing $UNIT_DST..."
sed -e "s|@USER@|$(id -un)|g" \
    -e "s|@DIR@|$DIR|g" \
    -e "s|@ARGS@|${HW_DAEMON_ARGS}|g" \
    "$DAEMON_DIR/systemd/${SERVICE}.service" | sudo tee "$UNIT_DST" > /dev/null

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE"
sudo systemctl restart "$SERVICE"
echo "Done. Check status with: systemctl status $SERVICE"
