#!/bin/bash
# One-command install/update for the CCTV app.
# Run this on the Linux server after `git clone` or `git pull`.
#
# Usage: sudo bash install.sh

set -e

INSTALL_DIR="/opt/cctv-app"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_AS_USER="${SUDO_USER:-$(whoami)}"

echo "==> Installing CCTV app to $INSTALL_DIR (running as user: $RUN_AS_USER)"

# System deps needed for OpenCV + camera access.
# IMPORTANT: python3-opencv comes from apt, NOT pip. The pip wheel
# (opencv-python-headless) is built with AVX2 and crashes with "Illegal
# instruction" on older CPUs (Celeron/Pentium) that lack AVX support.
apt-get update
apt-get install -y python3 python3-venv python3-pip python3-opencv ffmpeg

# Copy only executable application assets into /opt. Configuration and media
# deliberately live outside the release directory and survive updates.
mkdir -p "$INSTALL_DIR"
cp "$REPO_DIR"/*.py "$REPO_DIR"/requirements.txt "$INSTALL_DIR"/
rm -rf "$INSTALL_DIR/templates" "$INSTALL_DIR/static"
cp -R "$REPO_DIR/templates" "$REPO_DIR/static" "$INSTALL_DIR/"

# Log directory used by app.py's RotatingFileHandler
mkdir -p /var/log/cctv
chown "$RUN_AS_USER":"$RUN_AS_USER" /var/log/cctv
mkdir -p /var/lib/cctv
chown "$RUN_AS_USER":"$RUN_AS_USER" /var/lib/cctv

# The app will not start with placeholder secrets. Create the file once, then
# require an operator to set strong values before the first service start.
mkdir -p /etc/cctv
if [ ! -f /etc/cctv/cctv.env ]; then
    if [ -f "$REPO_DIR/cctv.env" ]; then
        cp "$REPO_DIR/cctv.env" /etc/cctv/cctv.env
    else
        cp "$REPO_DIR/cctv.env.example" /etc/cctv/cctv.env
    fi
    chmod 600 /etc/cctv/cctv.env
    echo "==> Created /etc/cctv/cctv.env. Set CCTV_SECRET_KEY and CCTV_BOOTSTRAP_PASSWORD, then run install.sh again."
    exit 1
fi
chmod 600 /etc/cctv/cctv.env

# Set up virtual environment with access to system packages (so it can see
# the apt-installed opencv instead of trying to pip-install a broken wheel)
if [ ! -d "$INSTALL_DIR/venv" ]; then
    python3 -m venv --system-site-packages "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

# Give the running user access to the webcam device
usermod -aG video "$RUN_AS_USER" || true

# Install systemd service with the correct user substituted in
sed "s/__RUN_AS_USER__/$RUN_AS_USER/" "$REPO_DIR/cctv.service" > /etc/systemd/system/cctv.service

systemctl daemon-reload
systemctl enable cctv
systemctl restart cctv

echo "==> Done. CCTV app is running forever as a systemd service."
echo "==> Check status:  sudo systemctl status cctv"
echo "==> View logs:      sudo journalctl -u cctv -f"
echo "==> The app listens only on 127.0.0.1:5000. Use Caddy/Tailscale HTTPS for access."
