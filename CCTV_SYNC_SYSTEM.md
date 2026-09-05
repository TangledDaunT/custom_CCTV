# CCTV Snapshot Sync System

Automated sync and retention system for CCTV motion capture snapshots. Transfers snapshots to external storage while maintaining 24-hour retention on the server.

## Overview

This system extends the CCTV application to:

1. **Save motion captures** on the home server (Shreyansh server)
2. **Auto-delete files after 24 hours** on the server
3. **Transfer snapshots** (not videos) to external hard disk on laptop
4. **Indefinite retention** on external disk for AI training
5. **Structured JSON logging** of all operations
6. **WhatsApp alerts** for each sync event

## Architecture

```
┌─────────────────────┐ (100.94.49.20)
│ Shreyansh Server    │
│ ┌─────────────────┐ │
│ │ CCTV App        │ │
│ │ Motion Capture │ │
│ │ Video Recorder  │ │
│ └─────────────────┘ │
│         │           │
│     ┌───▼───┐       │
│     │Sync   │       │
│     │Service│       │
│     └───┬───┘       │
└─────────┼───────────┘
          │ SSH/rsync
          │ (Tailscale)
          │
┌─────────▼───────────┐ (100.99.161.57)
│ Shreyansh HP Laptop │
│ ┌─────────────────┐ │
│ │External HDD     │ │
│ │/mnt/external    │ │
│ │Snapshots stored │ │
│ │indefinitely     │ │
│ └─────────────────┘ │
└─────────────────────┘
```

## File Flow

1. **Motion detected** → CCTV captures video + thumbnail
2. **Saved to server** → `/mnt/cctv-recordings/cctv_videos/`
3. **Sync service** → Copies snapshots (`.jpg`, `.png`) to laptop every 5 minutes
4. **Server cleanup** → Files older than 24 hours deleted from server
5. **External storage** → Snapshots retained indefinitely on `/mnt/external`

## Components

### Sync Script (`sync_to_laptop.py`)
- Finds snapshots in video directory
- Tests SSH connection via Tailscale
- Uses rsync to transfer files efficiently
- Excludes video files (`.mp4`, `.avi`, `.mkv`)
- Generates JSON structured logs
- Sends WhatsApp alerts

### Systemd Service (`cctv-sync.service`)
- Runs as background daemon
- Syncs every 5 minutes (configurable)
- Auto-restarts on failure
- Integrates with main CCTV service

### Laptop Setup (`setup_laptop.sh`)
- Prepares external disk directory
- Sets permissions for SSH user
- Creates organized directory structure
- Configures log rotation

### Deployment Script (`deploy_sync_system.sh`)
- Complete deployment automation
- SSH key setup
- Service installation
- Environment configuration
- Connection testing

## Installation

### Prerequisites

**Server (Shreyansh server: 100.94.49.20)**
- CCTV application installed and running
- Tailscale active on `100.94.49.20`
- SSH access to laptop
- Python 3.7+

**Laptop (Shreyansh HP laptop: 100.99.161.57)**
- Tailscale active on `100.99.161.57`
- External HDD mounted at `/mnt/external`
- SSH server running
- 200GB partition available

### Quick Deploy

```bash
# 1. Clone repository (if not already)
git clone <repo-url> cctv-app
cd cctv-app

# 2. Add sync configuration to environment
sudo tee -a /etc/cctv/cctv.env > /dev/null << EOF

# Snapshot sync to laptop configuration
CCTV_LAPTOP_HOST=100.99.161.57
CCTV_LAPTOP_USER=shreyansh
CCTV_REMOTE_DIR=/mnt/external/cctv_snapshots
EOF

# 3. Run deployment script
sudo bash deploy_sync_system.sh
```

The deployment script will:
1. Install sync script to `/usr/local/bin/`
2. Setup SSH key authentication
3. Configure laptop external disk
4. Install systemd service
5. Enable and start sync service
6. Run initial tests

### Manual Deployment

If you prefer manual setup:

```bash
# 1. Setup SSH keys
ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa_cctv_sync -N ""
ssh-copy-id -i ~/.ssh/id_rsa_cctv_sync.pub shreyansh@100.99.161.57

# 2. Setup laptop
scp setup_laptop.sh shreyansh@100.99.161.57:/tmp/
ssh shreyansh@100.99.161.57 "bash /tmp/setup_laptop.sh"

# 3. Install on server
sudo cp sync_to_laptop.py /usr/local/bin/cctv_sync_snapshots.py
sudo chmod +x /usr/local/bin/cctv_sync_snapshots.py

sudo cp cctv-sync.service cctv-sync.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable cctv-sync.timer
sudo systemctl start cctv-sync.timer
```

## Configuration

### Environment Variables

Add to `/etc/cctv/cctv.env`:

```bash
# Laptop connection
CCTV_LAPTOP_HOST=100.99.161.57           # Tailscale IP of laptop
CCTV_LAPTOP_USER=shreyansh               # SSH username
CCTV_REMOTE_DIR=/mnt/external/cctv_snapshots  # External disk path

# Sync settings (optional)
CCTV_SYNC_LOG=/var/log/cctv/cctv_sync.log
CCTV_SYNC_STATE=/var/lib/cctv/sync_state.json

# WhatsApp alerts (already configured)
CCTV_ALERT_NUMBERS=919876543210
WACLI_PATH=/home/linuxbrew/.linuxbrew/bin/wacli
```

### Sync Script Options

```bash
# Run once and exit
sudo cctv_sync_snapshots.py --once

# Test SSH connection
sudo cctv_sync_snapshots.py --test-ssh

# Continuous mode with custom interval (seconds)
sudo cctv_sync_snapshots.py --interval 600  # Every 10 minutes
```

## Service Management

### Status and Monitoring

```bash
# Check service status
sudo systemctl status cctv-sync
sudo systemctl status cctv-sync.timer

# View logs
sudo journalctl -u cctv-sync -f              # Systemd logs
sudo tail -f /var/log/cctv/cctv_sync.log     # Sync logs

# View structured JSON logs
sudo tail -n 10 /var/log/cctv/cctv_sync.log | jq .
```

### Manual Operations

```bash
# Trigger manual sync
sudo cctv_sync_snapshots.py --once

# Restart service
sudo systemctl restart cctv-sync

# Stop service
sudo systemctl stop cctv-sync

# Disable auto-start
sudo systemctl disable cctv-sync.timer
```

## File Organization

### Server Structure
```
/mnt/cctv-recordings/cctv_videos/
├── event_20250905_120000.mp4          # Video (24h retention)
├── event_20250905_120000_thumb.jpg    # Thumbnail (synced)
├── snapshot_20250905_130000.jpg       # Manual snapshot (synced)
└── cctv_snapshot.jpg                  # Live snapshot (synced)
```

### Laptop Structure
```
/mnt/external/cctv_snapshots/
├── daily/
│   └── 2025-09-05/
│       ├── event_20250905_120000_thumb.jpg
│       └── snapshot_20250905_130000.jpg
├── events/                            # Event thumbnails
└── manual/                            # Manual snapshots
```

## Log Format

Sync operations are logged as structured JSON:

```json
{
  "timestamp": "2025-09-05T12:00:00Z",
  "level": "INFO",
  "logger": "cctv_sync",
  "message": "Found 5 snapshots to sync",
  "event": "sync_complete",
  "duration_seconds": 3.2,
  "snapshots_found": 5,
  "synced": 5,
  "failed": 0,
  "success": true
}
```

## WhatsApp Alerts

When snapshots are synced, WhatsApp alerts are sent:

```
📸 5 new CCTV snapshot(s) synced to external disk at 2025-09-05 12:00:00
```

- Alerts sent to all numbers in `CCTV_ALERT_NUMBERS`
- Includes count and timestamp
- Optional: first snapshot sent as preview

## Retention Policy

### Server (Shreyansh server)
- **All files**: Deleted after 24 hours
- **Videos**: Auto-deleted by existing cleanup loop
- **Snapshots**: Deleted after sync confirms success

### Laptop (Shreyansh HP laptop)
- **Snapshots**: Retained indefinitely
- **Videos**: Never transferred
- **Organization**: Auto-organized by date

## Security

### SSH Key Authentication
- Dedicated SSH key for sync (`~/.ssh/id_rsa_cctv_sync`)
- Passwordless authentication required
- Connection via Tailscale only (no public IPs)

### Service Hardening
```ini
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
```

### Network Security
- All traffic via Tailscale mesh network
- No public SSH ports exposed
- End-to-end encryption

## Monitoring & Alerting

### Health Checks
```bash
# Quick health check
sudo systemctl is-active cctv-sync

# Last sync time
sudo tail -n 1 /var/log/cctv/cctv_sync.log | jq -r '.timestamp'

# Count synced files today
ssh shreyansh@100.99.161.57 "find /mnt/external/cctv_snapshots/daily/$(date +%Y-%m-%d) -type f | wc -l"
```

### Alerting Integration
- WhatsApp alerts on each sync
- Structured logs for external monitoring
- Systemd journal integration

## Troubleshooting

### Common Issues

**SSH Connection Failed**
```bash
# Verify Tailscale connectivity
tailscale ping 100.99.161.57

# Test SSH manually
ssh -vv shreyansh@100.99.161.57

# Check SSH key
ls -la ~/.ssh/id_rsa_cctv_sync
```

**Snapshots Not Syncing**
```bash
# Check for snapshot files
ls -la /mnt/cctv-recordings/cctv_videos/*.jpg

# Manual sync test
sudo cctv_sync_snapshots.py --once

# Check service logs
sudo journalctl -u cctv-sync -n 50
```

**WhatsApp Alerts Not Sent**
```bash
# Verify wacli
which wacli && wacli --version

# Test manually
wacli send text --to YOUR_NUMBER --message "Test"
```

**External Disk Full**
```bash
# Check disk space
ssh shreyansh@100.99.161.57 "df -h /mnt/external"

# Archive old data
ssh shreyansh@100.99.161.57 "tar -czf /mnt/external/archive_$(date +%Y%m%d).tar.gz /mnt/external/cctv_snapshots/daily/2025-08-*"
```

### Debug Mode
```bash
# Run with verbose output
sudo /usr/local/bin/cctv_sync_snapshots.py --once 2>&1 | tee /tmp/sync_debug.log
```

## Performance

### Expected Performance
- **Sync time**: 3-10 seconds per batch
- **Network**: Minimal bandwidth (thumbnails only)
- **Disk I/O**: Low (rsync delta transfers)
- **Memory**: ~50MB per sync run

### Optimizations
- Rsync delta transfers
- Batch processing
- Connection pooling
- Incremental state tracking

## Backup & Recovery

### Backup Snapshots on Laptop
```bash
# On laptop
tar -czf snapshots_backup_$(date +%Y%m%d).tar.gz -C /mnt/external cctv_snapshots/

# Move to cold storage
mv snapshots_backup_*.tar.gz /mnt/external/backups/
```

### Recovery After Server Reinstall
```bash
# Re-run deployment
sudo bash deploy_sync_system.sh

# Snapshots already on laptop will be preserved
# No need to re-sync old data
```

## Integration with AI Training Pipeline

### Accessing Snapshots
```bash
# List all snapshots
ssh shreyansh@100.99.161.57 "find /mnt/external/cctv_snapshots -name '*.jpg'"

# Copy to ML training server
rsync -av shreyansh@100.99.161.57:/mnt/external/cctv_snapshots/ /data/ml_dataset/
```

### Metadata Structure
```
/snapshots/
  ├── filename: event_20250905_120000_thumb.jpg
  ├── timestamp: extracted from filename
  ├── event_id: from CCTV database
  └── motion_score: stored in CCTV events table
```

## Testing

See [TESTING_CHECKLIST.md](TESTING_CHECKLIST.md) for comprehensive test procedures.

### Quick Test
```bash
# Create test snapshot
sudo touch /mnt/cctv-recordings/cctv_videos/test_$(date +%Y%m%d_%H%M%S).jpg

# Run sync
sudo cctv_sync_snapshots.py --once

# Verify on laptop
ssh shreyansh@100.99.161.57 "ls -la /mnt/external/cctv_snapshots/ | grep test"
```

## Support

### Logs Location
- Server sync logs: `/var/log/cctv/cctv_sync.log`
- Server CCTV logs: `/var/log/cctv/cctv.log`
- Laptop logs: `/var/log/cctv/` (if configured)

### Useful Commands
```bash
# Service status
sudo systemctl status cctv-sync cctv

# Recent syncs
sudo grep "sync_complete" /var/log/cctv/cctv_sync.log | tail -10

# Disk usage
ssh shreyansh@100.99.161.57 "du -sh /mnt/external/cctv_snapshots"
```

## License

Same as main CCTV application.

## Related Files
- `app.py` - Main CCTV application
- `sync_to_laptop.py` - Sync script
- `cctv-sync.service` - Systemd service
- `deploy_sync_system.sh` - Deployment automation
- `TESTING_CHECKLIST.md` - Test procedures
