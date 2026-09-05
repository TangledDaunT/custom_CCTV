#!/bin/bash
# Setup script for the laptop (100.99.161.57)
# Run this on the laptop to prepare the external disk for receiving snapshots

set -euo pipefail

REMOTE_DIR="${CCTV_REMOTE_DIR:-/mnt/external/cctv_snapshots}"
LOG_DIR="/var/log/cctv"
LIB_DIR="/var/lib/cctv"

echo "=== CCTV Snapshot Receiver Setup (Laptop) ==="
echo "External disk mount: /mnt/external"
echo "Remote directory: $REMOTE_DIR"

# Check if external disk is mounted
if ! mountpoint -q /mnt/external; then
    echo "ERROR: /mnt/external is not mounted"
    echo "Please mount the external disk first:"
    echo "  sudo mkdir -p /mnt/external"
    echo "  sudo mount /dev/sdX1 /mnt/external  # Replace sdX1 with actual device"
    exit 1
fi

# Check available space
AVAIL_GB=$(df -BG /mnt/external | awk 'NR==2 {print $4}' | sed 's/G//')
echo "Available space on external disk: ${AVAIL_GB}G"

if [ "$AVAIL_GB" -lt 50 ]; then
    echo "WARNING: Less than 50GB available on external disk"
fi

# Create directory structure
echo "Creating directories..."
sudo mkdir -p "$REMOTE_DIR"
sudo mkdir -p "$LOG_DIR"
sudo mkdir -p "$LIB_DIR"

# Set permissions (allow SSH user to write)
echo "Setting permissions..."
sudo chown -R "$USER:$USER" "$REMOTE_DIR"
sudo chmod 755 "$REMOTE_DIR"
sudo chown "$USER:$USER" "$LOG_DIR"
sudo chown "$USER:$USER" "$LIB_DIR"

# Create directory structure for organized storage
sudo -u "$USER" mkdir -p "$REMOTE_DIR"/{daily,events,manual}
echo "Created subdirectories: daily, events, manual"

# Create log rotation config
echo "Creating log rotation config..."
sudo tee /etc/logrotate.d/cctv-laptop > /dev/null << 'EOF'
/var/log/cctv/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    create 0644 shreyansh shreyansh
}
EOF

# Create a simple receiver script for log organization
cat > "$HOME/cctv_organize_snapshots.sh" << 'SCRIPT'
#!/bin/bash
# Organize synced snapshots by date
REMOTE_DIR="${CCTV_REMOTE_DIR:-/mnt/external/cctv_snapshots}"
TODAY=$(date +%Y-%m-%d)

# Create daily directory if not exists
mkdir -p "$REMOTE_DIR/daily/$TODAY"

# Move any loose snapshots to today's directory
find "$REMOTE_DIR" -maxdepth 1 -type f \( -name "*.jpg" -o -name "*.png" \) -mmin -60 -exec mv {} "$REMOTE_DIR/daily/$TODAY/" \; 2>/dev/null || true

# Log to syslog
logger -t cctv-organize "Organized snapshots to $REMOTE_DIR/daily/$TODAY"
SCRIPT

chmod +x "$HOME/cctv_organize_snapshots.sh"

# Add cron job to organize snapshots every 10 minutes
(crontab -l 2>/dev/null | grep -v "cctv_organize_snapshots.sh" || true;
 echo "*/10 * * * * $HOME/cctv_organize_snapshots.sh") | crontab -

echo "✅ Setup complete!"
echo ""
echo "Verification:"
echo "  ls -la $REMOTE_DIR"
echo "  df -h /mnt/external"
echo ""
echo "The laptop is now ready to receive snapshots from the server."
echo "Snapshots will be organized into daily subdirectories automatically."
