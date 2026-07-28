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
apt-get install -y python3 python3-venv python3-pip python3-opencv

# Copy app code into /opt (keeps your git repo clean, separate from the running deployment)
# IMPORTANT: copy ALL .py files, not just app.py — motion.py (and any future
# modules) must sit alongside app.py or `import motion` fails at startup.
mkdir -p "$INSTALL_DIR"
cp -r "$REPO_DIR"/*.py "$REPO_DIR"/requirements.txt "$INSTALL_DIR"/

# Log directory used by app.py's RotatingFileHandler
mkdir -p /var/log/cctv
chown "$RUN_AS_USER":"$RUN_AS_USER" /var/log/cctv

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

WACLI_PATH="/home/linuxbrew/.linuxbrew/bin/wacli"
if [ ! -x "$WACLI_PATH" ]; then
    echo "==> WARNING: wacli not found at $WACLI_PATH — WhatsApp alerts will fail."
    echo "    Make sure wacli is installed at that path, or update WACLI_PATH in app.py."
fi

echo "==> Done. CCTV app is running forever as a systemd service."
echo "==> Check status:  sudo systemctl status cctv"
echo "==> View logs:      sudo journalctl -u cctv -f"
echo "==> View feed at:   http://<this-machine-ip>:5000"