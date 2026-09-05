# CCTV Snapshot Sync - Quick Start Guide

## One-Command Deployment

```bash
sudo bash deploy_sync_system.sh
```

This script automates the entire setup process.

## What Gets Installed

### On Server (Shreyansh server: 100.94.49.20)
- `/usr/local/bin/cctv_sync_snapshots.py` - Sync script
- `/etc/systemd/system/cctv-sync.service` - Service definition
- `/var/log/cctv/cctv_sync.log` - Sync logs
- SSH key at `~/.ssh/id_rsa_cctv_sync`

### On Laptop (Shreyansh HP laptop: 100.99.161.57)
- `/mnt/external/cctv_snapshots/` - Snapshot storage
- `/mnt/external/cctv_snapshots/daily/` - Daily organized snapshots
- Cron job to organize snapshots every 10 minutes

## Prerequisites

Before running deployment, ensure:

1. **Server** has CCTV system running:
   ```bash
   sudo systemctl status cctv  # Should be active
   ```

2. **Laptop** has external disk mounted:
   ```bash
   mountpoint -q /mnt/external && echo "Mounted" || echo "NOT mounted"
   ```

3. **Tailscale** is running on both machines:
   ```bash
   tailscale status  # Should show both IPs
   ```

4. **SSH** access works:
   ```bash
   ssh shreyansh@100.99.161.57 "echo OK"  # Should work with password
   ```

## Manual Deployment (if needed)

If the automated script fails, follow these steps:

### Step 1: Setup SSH Key
```bash
# Generate key
ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa_cctv_sync -N ""

# Copy to laptop
ssh-copy-id -i ~/.ssh/id_rsa_cctv_sync.pub shreyansh@100.99.161.57

# Test
ssh -i ~/.ssh/id_rsa_cctv_sync shreyansh@100.99.161.57 "echo SSH_OK"
```

### Step 2: Setup Laptop
```bash
# Copy setup script
scp setup_laptop.sh shreyansh@100.99.161.57:/tmp/

# Run on laptop
ssh shreyansh@100.99.161.57 "bash /tmp/setup_laptop.sh"
```

### Step 3: Configure Environment
```bash
# Add to /etc/cctv/cctv.env
sudo tee -a /etc/cctv/cctv.env > /dev/null << 'EOF'

# Snapshot sync to laptop configuration
CCTV_LAPTOP_HOST=100.99.161.57
CCTV_LAPTOP_USER=shreyansh
CCTV_REMOTE_DIR=/mnt/external/cctv_snapshots
EOF
```

### Step 4: Install Service
```bash
# Install script
sudo cp sync_to_laptop.py /usr/local/bin/cctv_sync_snapshots.py
sudo chmod +x /usr/local/bin/cctv_sync_snapshots.py

# Install service
sudo cp cctv-sync.service cctv-sync.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable cctv-sync.timer
sudo systemctl start cctv-sync.timer
```

## Verification

### Check Service Status
```bash
sudo systemctl status cctv-sync cctv-sync.timer
```

### Test Manual Sync
```bash
# Create test snapshot
sudo touch /mnt/cctv-recordings/cctv_videos/test_$(date +%Y%m%d_%H%M%S).jpg

# Run manual sync
sudo cctv_sync_snapshots.py --once

# Check laptop
ssh shreyansh@100.99.161.57 "ls -la /mnt/external/cctv_snapshots/ | grep test"
```

### View Logs
```bash
# Follow sync logs
sudo tail -f /var/log/cctv/cctv_sync.log

# View structured JSON
sudo tail -n 5 /var/log/cctv/cctv_sync.log | jq .
```

## Common Operations

### Manual Sync
```bash
sudo cctv_sync_snapshots.py --once
```

### Test SSH
```bash
sudo cctv_sync_snapshots.py --test-ssh
```

### Restart Service
```bash
sudo systemctl restart cctv-sync
```

### Stop Service
```bash
sudo systemctl stop cctv-sync
```

### Disable Auto-start
```bash
sudo systemctl disable cctv-sync.timer
```

## File Locations

### Server
- **Script**: `/usr/local/bin/cctv_sync_snapshots.py`
- **Config**: `/etc/cctv/cctv.env`
- **Logs**: `/var/log/cctv/cctv_sync.log`
- **State**: `/var/lib/cctv/sync_state.json`
- **SSH Key**: `~/.ssh/id_rsa_cctv_sync`

### Laptop
- **Snapshots**: `/mnt/external/cctv_snapshots/`
- **Daily**: `/mnt/external/cctv_snapshots/daily/YYYY-MM-DD/`
- **Events**: `/mnt/external/cctv_snapshots/events/`

## Troubleshooting

### SSH Connection Failed
```bash
# Check Tailscale
tailscale ping 100.99.161.57

# Regenerate SSH key
ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa_cctv_sync -N ""

# Re-copy key
ssh-copy-id -i ~/.ssh/id_rsa_cctv_sync.pub shreyansh@100.99.161.57
```

### Service Won't Start
```bash
# Check logs
sudo journalctl -u cctv-sync -n 50

# Check permissions
ls -la /usr/local/bin/cctv_sync_snapshots.py
ls -la /var/log/cctv/
ls -la /var/lib/cctv/

# Fix permissions if needed
sudo chmod +x /usr/local/bin/cctv_sync_snapshots.py
sudo mkdir -p /var/log/cctv /var/lib/cctv
sudo chmod 755 /var/log/cctv /var/lib/cctv
```

### Snapshots Not Syncing
```bash
# Check if snapshots exist
ls -la /mnt/cctv-recordings/cctv_videos/*.jpg

# Check environment
grep CCTV_LAPTOP /etc/cctv/cctv.env

# Test manual sync
sudo cctv_sync_snapshots.py --once
```

### WhatsApp Alerts Not Sent
```bash
# Check alert numbers
grep CCTV_ALERT_NUMBERS /etc/cctv/cctv.env

# Verify wacli
which wacli

# Test manually
wacli send text --to YOUR_NUMBER --message "Test"
```

## What Gets Synced

### Included (✅)
- Event thumbnails: `event_YYYYMMDD_HHMMSS_thumb.jpg`
- Manual snapshots: `snapshot_YYYYMMDD_HHMMSS.jpg`
- Live snapshots: `cctv_snapshot.jpg`
- PNG files: `*.png`

### Excluded (❌)
- Videos: `*.mp4`, `*.avi`, `*.mkv`
- Thumbs: `*_thumb.jpg` (event thumbnails ARE synced, manual thumbs aren't)

## Retention

### Server (Automatic)
- **Videos**: Deleted after 24 hours by `cctv` service
- **Snapshots**: Deleted after 24 hours by sync system

### Laptop (Indefinite)
- **All snapshots**: Retained forever
- **Organization**: Auto-organized by date
- **No deletion**: Manual cleanup only

## Support

- **Logs**: `/var/log/cctv/cctv_sync.log`
- **Documentation**: `CCTV_SYNC_SYSTEM.md`
- **Testing**: `TESTING_CHECKLIST.md`
- **Service**: `systemctl status cctv-sync`
