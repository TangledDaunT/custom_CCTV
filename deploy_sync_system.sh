#!/bin/bash
# Deployment script for CCTV sync system
# Run this on the home server (Shreyansh server: 100.94.49.20)

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_NAME="cctv_sync_snapshots.py"

echo "=== CCTV Snapshot Sync System Deployment ==="
echo "This will deploy the sync system to both machines."
echo ""

# Check if running on server
echo "Step 1: Verifying server environment..."
if [ ! -f "/etc/cctv/cctv.env" ]; then
    echo "ERROR: /etc/cctv/cctv.env not found"
    echo "Please run 'sudo bash install.sh' first to set up the CCTV system."
    exit 1
fi

# Load environment variables
set -a
source /etc/cctv/cctv.env
set +a

# Check required environment variables
if [ -z "${CCTV_LAPTOP_HOST:-}" ]; then
    echo "ERROR: CCTV_LAPTOP_HOST not set in /etc/cctv/cctv.env"
    echo "Please add: CCTV_LAPTOP_HOST=100.99.161.57"
    exit 1
fi

if [ -z "${CCTV_LAPTOP_USER:-}" ]; then
    echo "WARNING: CCTV_LAPTOP_USER not set, using 'shreyansh'"
    CCTV_LAPTOP_USER="shreyansh"
fi

if [ -z "${CCTV_REMOTE_DIR:-}" ]; then
    echo "WARNING: CCTV_REMOTE_DIR not set, using '/mnt/external/cctv_snapshots'"
    CCTV_REMOTE_DIR="/mnt/external/cctv_snapshots"
fi

echo "✅ Environment loaded"
echo "  Laptop: ${CCTV_LAPTOP_USER}@${CCTV_LAPTOP_HOST}"
echo "  Remote dir: ${CCTV_REMOTE_DIR}"

# Install sync script on server
echo ""
echo "Step 2: Installing sync script on server..."
sudo cp "$REPO_DIR/sync_to_laptop.py" /usr/local/bin/$SCRIPT_NAME
sudo chmod +x /usr/local/bin/$SCRIPT_NAME
echo "✅ Sync script installed to /usr/local/bin/$SCRIPT_NAME"

# Install systemd service
echo ""
echo "Step 3: Installing systemd service..."
sudo cp "$REPO_DIR/cctv-sync.service" /etc/systemd/system/
sudo cp "$REPO_DIR/cctv-sync.timer" /etc/systemd/system/
sudo systemctl daemon-reload
echo "✅ Systemd service installed"

# Setup SSH key-based authentication
echo ""
echo "Step 4: Setting up SSH key authentication..."
SSH_KEY="$HOME/.ssh/id_rsa_cctv_sync"

if [ ! -f "$SSH_KEY" ]; then
    echo "Generating SSH key pair..."
    ssh-keygen -t rsa -b 4096 -f "$SSH_KEY" -N "" -C "cctv-sync@$(hostname)"
    echo "✅ SSH key generated: $SSH_KEY"
else
    echo "✅ SSH key already exists: $SSH_KEY"
fi

# Test SSH connection
echo ""
echo "Step 5: Testing SSH connection to laptop..."
if ssh -i "$SSH_KEY" -o ConnectTimeout=10 -o BatchMode=yes \
    "${CCTV_LAPTOP_USER}@${CCTV_LAPTOP_HOST}" "echo SSH_OK" 2>/dev/null | grep -q "SSH_OK"; then
    echo "✅ SSH connection successful (passwordless auth working)"
else
    echo "⚠ SSH connection failed or requires password"
    echo ""
    echo "Please copy the SSH key to the laptop:"
    echo "  ssh-copy-id -i $SSH_KEY.pub ${CCTV_LAPTOP_USER}@${CCTV_LAPTOP_HOST}"
    echo ""
    echo "Or manually add to laptop's ~/.ssh/authorized_keys:"
    echo "  cat $SSH_KEY.pub"
    read -p "Press Enter after you've copied the SSH key..."

    # Test again
    if ! ssh -i "$SSH_KEY" -o ConnectTimeout=10 -o BatchMode=yes \
        "${CCTV_LAPTOP_USER}@${CCTV_LAPTOP_HOST}" "echo SSH_OK" 2>/dev/null | grep -q "SSH_OK"; then
        echo "ERROR: SSH authentication still failing"
        exit 1
    fi
    echo "✅ SSH connection now successful"
fi

# Setup laptop
echo ""
echo "Step 6: Setting up laptop..."
scp "$REPO_DIR/setup_laptop.sh" "${CCTV_LAPTOP_USER}@${CCTV_LAPTOP_HOST}:/tmp/"
ssh "${CCTV_LAPTOP_USER}@${CCTV_LAPTOP_HOST}" "bash /tmp/setup_laptop.sh"
echo "✅ Laptop setup complete"

# Create required directories on server
echo ""
echo "Step 7: Creating required directories on server..."
sudo mkdir -p /var/log/cctv
sudo mkdir -p /var/lib/cctv
sudo chown root:root /var/log/cctv /var/lib/cctv
sudo chmod 755 /var/log/cctv /var/lib/cctv
echo "✅ Directories created"

# Create log rotation config
echo ""
echo "Step 8: Setting up log rotation..."
sudo tee /etc/logrotate.d/cctv-sync > /dev/null << 'EOF'
/var/log/cctv/cctv_sync.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    create 0644 root root
    postrotate
        systemctl reload cctv-sync > /dev/null 2>&1 || true
    endscript
}
EOF
echo "✅ Log rotation configured"

# Add environment variables to cctv.env if missing
echo ""
echo "Step 9: Updating environment configuration..."
if ! grep -q "CCTV_LAPTOP_HOST" /etc/cctv/cctv.env; then
    echo "Adding sync configuration to /etc/cctv/cctv.env"
    sudo tee -a /etc/cctv/cctv.env > /dev/null << EOF

# Snapshot sync to laptop configuration
CCTV_LAPTOP_HOST=${CCTV_LAPTOP_HOST}
CCTV_LAPTOP_USER=${CCTV_LAPTOP_USER}
CCTV_REMOTE_DIR=${CCTV_REMOTE_DIR}
EOF
    echo "✅ Configuration updated"
else
    echo "✅ Configuration already present"
fi

# Enable and start service
echo ""
echo "Step 10: Enabling and starting sync service..."
sudo systemctl enable cctv-sync.service cctv-sync.timer
sudo systemctl start cctv-sync.timer
echo "✅ Service enabled"

# Test sync
echo ""
echo "Step 11: Testing sync..."
if sudo /usr/local/bin/$SCRIPT_NAME --test-ssh; then
    echo "✅ SSH test passed"
else
    echo "⚠ SSH test failed - check configuration"
fi

# Status check
echo ""
echo "=== Deployment Complete! ==="
echo ""
echo "Service status:"
echo "  sudo systemctl status cctv-sync"
echo "  sudo systemctl status cctv-sync.timer"
echo ""
echo "Manual sync:"
echo "  sudo $SCRIPT_NAME --once"
echo ""
echo "View logs:"
echo "  sudo tail -f /var/log/cctv/cctv_sync.log"
echo "  sudo journalctl -u cctv-sync -f"
echo ""
echo "On laptop (${CCTV_LAPTOP_HOST}):"
echo "  ls -la ${CCTV_REMOTE_DIR}/"
echo ""
