# CCTV Sync System Testing Checklist

## Pre-deployment Checklist

### Server (Shreyansh server: 100.94.49.20)
- [ ] CCTV system is running (`sudo systemctl status cctv`)
- [ ] Video directory exists (`ls -la /mnt/cctv-recordings/cctv_videos`)
- [ ] Tailscale is running (`tailscale status`)
- [ ] Can ping laptop (`ping 100.99.161.57`)

### Laptop (Shreyansh HP laptop: 100.99.161.57)
- [ ] Tailscale is running (`tailscale status`)
- [ ] External disk is mounted (`mountpoint -q /mnt/external`)
- [ ] Can ping server (`ping 100.94.49.20`)
- [ ] SSH server is running (`sudo systemctl status sshd`)

## Deployment Steps

### Step 1: Setup SSH Key Authentication
```bash
# On server
ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa_cctv_sync -N "" -C "cctv-sync@$(hostname)"
ssh-copy-id -i ~/.ssh/id_rsa_cctv_sync.pub shreyansh@100.99.161.57

# Test passwordless SSH
ssh -i ~/.ssh/id_rsa_cctv_sync shreyansh@100.99.161.57 "echo SSH_OK"
```
- [ ] SSH key generated
- [ ] Key copied to laptop
- [ ] Passwordless SSH works

### Step 2: Setup Laptop
```bash
# Run setup script on laptop
scp setup_laptop.sh shreyansh@100.99.161.57:/tmp/
ssh shreyansh@100.99.161.57 "bash /tmp/setup_laptop.sh"
```
- [ ] External disk mounted at `/mnt/external`
- [ ] Directory `/mnt/external/cctv_snapshots` created
- [ ] Proper permissions set
- [ ] Cron job added for organization

### Step 3: Deploy to Server
```bash
# On server
sudo bash deploy_sync_system.sh
```
- [ ] Sync script installed to `/usr/local/bin/cctv_sync_snapshots.py`
- [ ] Systemd service installed
- [ ] Environment variables configured in `/etc/cctv/cctv.env`
- [ ] Service enabled and started

## Functional Testing

### Test 1: Create Test Motion Event
```bash
# Trigger a test motion event (create test snapsho)
sudo touch /mnt/cctv-recordings/cctv_videos/test_snapshot_$(date +%Y%m%d_%H%M%S).jpg

# Or use the actual CCTV system to create an event
# Walk in front of camera, or use dashboard snapshot feature
```
- [ ] Test snapshot created
- [ ] Snapshot appears in `/mnt/cctv-recordings/cctv_videos`

### Test 2: Verify Sync Script Finds Snapshots
```bash
# Test sync script in dry-run mode
sudo /usr/local/bin/cctv_sync_snapshots.py --once
```
- [ ] Script runs without errors
- [ ] Logs show snapshots found
- [ ] JSON log entry created in `/var/log/cctv/cctv_sync.log`

### Test 3: Verify SSH Connection
```bash
# Test SSH independently
sudo /usr/local/bin/cctv_sync_snapshots.py --test-ssh
```
- [ ] SSH connection successful
- [ ] Laptop reachable via Tailscale

### Test 4: Manual Sync Test
```bash
# Force manual sync
sudo /usr/local/bin/cctv_sync_snapshots.py --once

# Check remote directory on laptop
ssh shreyansh@100.99.161.57 "ls -la /mnt/external/cctv_snapshots"
```
- [ ] Snapshots synced to laptop
- [ ] Files appear in correct directory
- [ ] No videos synced (only .jpg/.png)

### Test 5: Verify WhatsApp Alert
- [ ] WhatsApp message received on registered number
- [ ] Message shows correct snapshot count
- [ ] Timestamp matches sync time

### Test 6: Verify Automatic Sync
```bash
# Check service is running
sudo systemctl status cctv-sync

# Create new snapshot and wait 5 minutes
sudo touch /mnt/cctv-recordings/cctv_videos/new_snapshot_$(date +%Y%m%d_%H%M%S).jpg

# Wait 5-10 minutes, then check logs
sudo tail -f /var/log/cctv/cctv_sync.log
```
- [ ] Service running automatically
- [ ] New snapshot detected and synced
- [ ] Log entry shows sync

### Test 7: Verify File Deletion After 24 Hours
```bash
# Create test file with old timestamp
sudo touch -d "2 days ago" /mnt/cctv-recordings/cctv_videos/old_test.jpg

# Wait for cleanup cycle (or manually trigger)
sudo systemctl restart cctv

# Old file should be deleted after cleanup cycle runs
ls -la /mnt/cctv-recordings/cctv_videos/ | grep old_test || echo "File deleted ✓"
```
- [ ] Old files deleted from server
- [ ] Only files < 24 hours remain on server

### Test 8: Verify Indefinite Retention on Laptop
```bash
# Check that synced files remain on laptop
ssh shreyansh@100.99.161.57 "ls -la /mnt/external/cctv_snapshots"

# Even after server deletion, files should remain on laptop
```
- [ ] Files preserved on laptop
- [ ] No automatic deletion on laptop
- [ ] Files organized by date

### Test 9: Verify Video Exclusion
```bash
# Create test video file
sudo touch /mnt/cctv-recordings/cctv_videos/test_video.mp4

# Run sync
sudo /usr/local/bin/cctv_sync_snapshots.py --once

# Video should NOT be on laptop
ssh shreyansh@100.99.161.57 "ls /mnt/external/cctv_snapshots/*.mp4 2>/dev/null || echo 'No videos found ✓'"
```
- [ ] Video files NOT synced
- [ ] Only .jpg/.png files transferred

### Test 10: Verify Structured JSON Logging
```bash
# Check log format
sudo tail -n 5 /var/log/cctv/cctv_sync.log | jq .

# Should output valid JSON with structure:
# {
#   "timestamp": "2025-09-05T12:00:00Z",
#   "level": "INFO",
#   "logger": "cctv_sync",
#   "message": "..."
# }
```
- [ ] Logs are valid JSON
- [ ] Timestamps present
- [ ] Event type field present

## Service Management

### Start/Stop/Restart
```bash
sudo systemctl start cctv-sync     # Start service
sudo systemctl stop cctv-sync      # Stop service
sudo systemctl restart cctv-sync   # Restart service
sudo systemctl status cctv-sync    # Check status
```

### Enable/Disable Auto-start
```bash
sudo systemctl enable cctv-sync    # Enable on boot
sudo systemctl disable cctv-sync   # Disable on boot
```

### View Logs
```bash
sudo journalctl -u cctv-sync -f                     # Follow systemd logs
sudo tail -f /var/log/cctv/cctv_sync.log           # Follow sync log
sudo tail -f /var/log/cctv/cctv.log                 # Follow main CCTV log
```

## Troubleshooting

### Issue: SSH Connection Failed
```bash
# Check Tailscale connectivity
tailscale ping 100.99.161.57

# Try manual SSH
ssh -vv shreyansh@100.99.161.57

# Check SSH service on laptop
ssh shreyansh@100.99.161.57 "sudo systemctl status sshd"
```

### Issue: Snapshots Not Syncing
```bash
# Check if video directory has snapshots
ls -la /mnt/cctv-recordings/cctv_videos/*.jpg

# Check service logs
sudo journalctl -u cctv-sync -n 50

# Check permissions
ls -la /mnt/cctv-recordings/cctv_videos/
```

### Issue: WhatsApp Alerts Not Sent
```bash
# Check alert numbers configured
grep CCTV_ALERT_NUMBERS /etc/cctv/cctv.env

# Check wacli is available
which wacli
wacli --version

# Test wacli manually
wacli send text --to YOUR_NUMBER --message "Test"
```

### Issue: External Disk Full
```bash
# Check disk space on laptop
ssh shreyansh@100.99.161.57 "df -h /mnt/external"

# If full, manually archive old snapshots
ssh shreyansh@100.99.161.57 "du -sh /mnt/external/cctv_snapshots/*"
```

## Post-deployment Verification

### Final Checklist
- [ ] All tests passed
- [ ] Service running automatically
- [ ] Snapshots syncing every 5 minutes
- [ ] WhatsApp alerts working
- [ ] Logs rotating properly
- [ ] Files deleted from server after 24 hours
- [ ] Files retained indefinitely on laptop
- [ ] No videos synced (only images)

### Performance Monitoring
```bash
# Monitor sync performance over time
watch -n 60 'sudo tail -n 1 /var/log/cctv/cctv_sync.log | jq -r ".message"'

# Check sync frequency
sudo journalctl -u cctv-sync --since "1 hour ago" | grep "sync_complete" | wc -l
```

### Security Verification
```bash
# Verify SSH key auth only (no password)
ssh -o PasswordAuthentication=no -i ~/.ssh/id_rsa_cctv_sync shreyansh@100.99.161.57 "echo OK"

# Verify service running as non-root where possible
ps aux | grep cctv_sync

# Verify log permissions
ls -la /var/log/cctv/
```

---

## Quick Commands Reference

```bash
# One-line manual sync
sudo /usr/local/bin/cctv_sync_snapshots.py --once

# Test SSH connection
sudo /usr/local/bin/cctv_sync_snapshots.py --test-ssh

# View last 10 sync logs
sudo tail -n 10 /var/log/cctv/cctv_sync.log | jq .

# Check service health
sudo systemctl status cctv-sync cctv-sync.timer

# Real-time monitoring
sudo journalctl -u cctv-sync -f
```

---

**Deployment Date:** _______________
**Deployed By:** _______________
**All Tests Passed:** ☐ YES ☐ NO
**Notes:** _______________________________________
