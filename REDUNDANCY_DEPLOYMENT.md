# CCTV Redundancy & Cross-Monitoring Deployment Guide

## 🎯 **Features Implemented**

### 1. **HDD/SSD Failover System**
- ✅ HDD disconnect detection (automatic)
- ✅ Fallback to internal SSD when HDD unmounted
- ✅ Zero data loss with checksum verification
- ✅ Automatic recovery when HDD reconnects
- ✅ Transfer all fallback data to HDD on reconnection
- ✅ Delete SSD data after successful transfer

### 2. **Cross-Machine Monitoring**
- ✅ Server monitors Laptop health
- ✅ Laptop monitors Server health
- ✅ Power failure detection
- ✅ Service crash detection
- ✅ WhatsApp alerts to +91 7754008079

### 3. **Health Alerts**
- ✅ High CPU usage (>90%)
- ✅ High memory usage (>95%)
- ✅ High disk usage (>95%)
- ✅ Service down
- ✅ Host unreachable (power failure)
- ✅ Host recovery notification

---

## 🔄 **How It Works**

### **Storage Failover Flow**

```
HDD Connected
    ↓
Save snapshots to HDD
(/media/shreyansh/LINUX_SHARE/cctv_snapshots)
    ↓
HDD Disconnected (detected immediately)
    ↓
Switch to SSD fallback
(~/cctv_fallback/snapshots)
    ↓
Send WhatsApp: "HDD DISCONNECTED"
    ↓
Continue saving to SSD (no data loss)
    ↓
HDD Reconnected (detected)
    ↓
Send WhatsApp: "HDD RECONNECTED - Starting recovery"
    ↓
Move all SSD data to HDD
    ├─ Copy files with checksum
    ├─ Verify integrity
    └─ Delete from SSD
    ↓
Send WhatsApp: "RECOVERY COMPLETE"
    ↓
Continue saving to HDD
```

### **Cross-Monitoring Flow**

```
Home Server (100.94.49.20)
    ↓ (via SSH/Tailscale)
Monitors Laptop (100.99.161.57)
    ├─ CPU usage
    ├─ Memory usage
    ├─ Service status
    └─ Reachability
    ↓
If ISSUE detected → Send WhatsApp from SERVER

HP Laptop (100.99.161.57)
    ↓ (via SSH/Tailscale)
Monitors Server (100.94.49.20)
    ├─ CPU usage
    ├─ Memory usage
    ├─ Service status
    └─ Reachability
    ↓
If ISSUE detected → Send WhatsApp from LAPTOP
```

---

## 📂 **Files Created**

### On Both Machines:

```
~/bin/
  ├─ sync_redundancy_manager.py    (Failover manager)
  └─ cross_machine_monitor.py       (Cross-monitor)

~/.config/systemd/user/
  ├─ monitor-health.service          (Health checker)
  ├─ monitor-health.timer            (30s interval)
  └─ cross-monitor.service           (Cross-machine monitor)

~/cctv_fallback/
  ├─ snapshots/                      (SSD fallback)
  ├─ analysis/                       (Analysis fallback)
  └─ .fallback_active               (Marker when using SSD)

~/.local/log/cctv/
  ├─ redundancy.log                   (Failover logs)
  ├─ transfer_log.json                (Transfer audit)
  └─ cross_monitor.log                (Monitoring logs)

~/.local/lib/cctv/
  ├─ storage_state.json               (Current mode)
  ├─ checksums.json                   (File integrity)
  └─ monitor_state.json                (Monitoring state)
```

---

## 🚀 **Deployment Steps**

### **Step 1: Install Dependencies**

```bash
# On both machines (server and laptop)
# Install psutil for system monitoring
pip3 install --user psutil

# Verify wacli works
wacli send text --to 917754008079 --message "Test from $(hostname)"
```

### **Step 2: Install Scripts**

**On Laptop:**
```bash
cd ~/custom_cctv

# Copy scripts
mkdir -p ~/bin
cp sync_redundancy_manager.py ~/bin/
cp cross_machine_monitor.py ~/bin/
chmod +x ~/bin/*.py

# Copy systemd files
mkdir -p ~/.config/systemd/user
cp monitor-health.service ~/.config/systemd/user/
cp monitor-health.timer ~/.config/systemd/user/
cp cross-monitor.service ~/.config/systemd/user/

# Create directories
mkdir -p ~/cctv_fallback/{snapshots,analysis}
mkdir -p ~/.local/log/cctv
mkdir -p ~/.local/lib/cctv
```

**On Server:**
```bash
# SSH into server
ssh shreyansh@100.94.49.20

# Clone repo
git clone https://github.com/TangledDaunT/custom_CCTV.git ~/custom_cctv
cd ~/custom_cctv

# Copy scripts
mkdir -p ~/bin
cp sync_redundancy_manager.py ~/bin/
cp cross_machine_monitor.py ~/bin/
chmod +x ~/bin/*.py

# Copy systemd files
mkdir -p ~/.config/systemd/user
cp cross-monitor.service ~/.config/systemd/user/

# Create directories
mkdir -p ~/.local/log/cctv
mkdir -p ~/.local/lib/cctv
```

### **Step 3: Setup SSH Keys**

Already configured:
- Server → Laptop: `~/.ssh/id_rsa_cctv_deploy`
- Laptop → Server: Already setup

### **Step 4: Enable Services**

**On Laptop:**
```bash
# Reload systemd
systemctl --user daemon-reload

# Enable health monitor
systemctl --user enable monitor-health.timer
systemctl --user start monitor-health.timer

# Enable cross-machine monitor
systemctl --user enable cross-monitor
systemctl --user start cross-monitor

# Check status
systemctl --user list-timers
systemctl --user status cross-monitor
```

**On Server:**
```bash
# Reload systemd
systemctl --user daemon-reload

# Enable cross-machine monitor
systemctl --user enable cross-monitor
systemctl --user start cross-monitor

# Check status
systemctl --user status cross-monitor
```

---

## 🧪 **Testing**

### **Test 1: HDD Disconnect Simulation**

```bash
# On laptop
# Unmount external disk
sudo umount /media/shreyansh/LINUX_SHARE

# Check logs
tail -f ~/.local/log/cctv/redundancy.log

# Expected: 
# - "HDD DISCONNECTED"
# - WhatsApp message sent
# - Fallback mode activated

# Create test file
touch /mnt/cctv-recordings/cctv_videos/test.jpg
# Should be saved to ~/cctv_fallback/snapshots/
```

### **Test 2: HDD Reconnect Recovery**

```bash
# Remount HDD
sudo mount /dev/sdb3 /media/shreyansh/LINUX_SHARE

# Watch logs
tail -f ~/.local/log/cctv/redundancy.log

# Expected:
# - "HDD RECONNECTED"
# - Recovery starts
# - Files moved from SSD to HDD
# - WhatsApp: "RECOVERY COMPLETE"
```

### **Test 3: Cross-Monitoring**

```bash
# On laptop, check server status
~/bin/cross_machine_monitor.py --once

# Expected: JSON with server status
# {
#   "hostname": "shreyansh-server",
#   "cpu": 15.2,
#   "memory": 45.3,
#   "reachable": true
# }

# Test power failure alert (stop server briefly)
# SSH into server and run: sudo systemctl stop cctv

# Expected on phone:
# "🚨 HOST DOWN - Machine: Home Server"
```

### **Test 4: High CPU Alert**

```bash
# Stress test CPU
stress --cpu 4 --timeout 120

# Expected WhatsApp alert:
# "⚠️ HIGH CPU WARNING - CPU: 95%"
```

---

## 📊 **WhatsApp Alert Examples**

### HDD Disconnected
```
⚠️ HDD DISCONNECTED
Switching to SSD fallback storage
New data will be saved to SSD until HDD reconnects
```

### HDD Reconnected
```
🔌 HDD RECONNECTED
Starting fallback data recovery...
```

### Recovery Complete
```
✅ RECOVERY COMPLETE
Moved: 15 snapshots
Moved: 15 analyses  
Failed: 0
Total: 12.5 MB
```

### Host Down (Power Failure)
```
🚨 HOST DOWN
Machine: Home Server
IP: 100.94.49.20
Status: UNREACHABLE
Time: 14:32:15
Power failure or network issue?
```

### Host Recovered
```
✅ HOST RECOVERED
Machine: Home Server
IP: 100.94.49.20
Status: ONLINE
Uptime: up 5 minutes
```

### Service Down
```
⚠️ SERVICE DOWN  
Machine: Home Server
Service: cctv
Status: STOPPED
Time: 14:35:20
```

### High CPU Warning
```
⚠️ HIGH CPU WARNING
Machine: shreyansh-HP-Laptop
CPU: 94.5%
Memory: 62.3%
Time: 11:15:30
```

---

## ⚙️ **Configuration**

### Thresholds (Edit in script):

```python
# In cross_machine_monitor.py
"cpu_threshold": 90,        # Alert if CPU > 90%
"memory_threshold": 95,     # Alert if memory > 95%
"disk_threshold": 95,       # Alert if disk > 95%
"check_interval": 60,      # Check every 60 seconds
"offline_threshold": 180,   # Alert if offline > 3 min
```

### Alert Cooldowns:

```python
# Prevents spam (in minutes)
high_cpu_alert: 30 minutes
high_memory_alert: 30 minutes
service_down_alert: 10 minutes
offline_alert: 5 minutes
```

---

## 📋 **Integration with Existing Sync**

The redundancy manager automatically integrates with your existing sync:

1. **Sync script** runs every 5 minutes
2. **Redundancy manager** provides active storage directory
3. **If HDD unmounted** → Returns SSD fallback path
4. **Sync continues** normally using fallback
5. **When HDD returns** → All data moved automatically

---

## 🔍 **Monitoring Commands**

### Check Current Storage Mode

```bash
python3 ~/bin/sync_redundancy_manager.py

# Output:
{
  "mode": "primary",  # or "fallback"
  "hdd_available": true,
  "active_snapshot_dir": "/media/.../LINUX_SHARE/cctv_snapshots",
  "disk_free_gb": 150
}
```

### Check Cross-Monitor Status

```bash
# Single check
python3 ~/bin/cross_machine_monitor.py --once

# View logs
tail -f ~/.local/log/cctv/cross_monitor.log
```

### Check Service Status

```bash
# On laptop
systemctl --user status monitor-health
systemctl --user status cross-monitor

# On server  
systemctl --user status cross-monitor
```

### View Transfer History

```bash
tail -f ~/.local/log/cctv/transfer_log.json

# Each entry:
{
  "timestamp": "2026-09-06T11:05:23",
  "source": "/home/shreyansh/cctv_fallback/snapshots/test.jpg",
  "destination": "/media/.../cctv_snapshots/test.jpg",
  "checksum": "abc123...",
  "verified": true
}
```

---

## 🛡️ **Data Integrity Guarantees**

### Checksum Verification

Every file transfer includes:

1. **Source checksum** (SHA256)
2. **Copy operation**
3. **Destination checksum**
4. **Verification** (compare checksums)
5. **If mismatch** → Delete corrupted copy, retry

```bash
# Check file integrity
cat ~/.local/lib/cctv/checksums.json

{
  "/media/.../snapshot.jpg": {
    "checksum": "a1b2c3d4...",
    "timestamp": "2026-09-06T11:05:23",
    "size": 102400
  }
}
```

### Zero Data Loss

- ✓ Never delete source before verifying destination
- ✓ Checksum on every transfer
- ✓ Retry on corruption
- ✓ All transfers logged
- ✓ State tracked across power cycles

---

## ⚠️ **Troubleshooting**

### Issue: HDD not detected

```bash
# Check mount
mountpoint /media/shreyansh/LINUX_SHARE

# If not mounted, mount manually
sudo mount /dev/sdb3 /media/shreyansh/LINUX_SHARE

# Check logs
tail ~/.local/log/cctv/redundancy.log
```

### Issue: Fallback data not moving to HDD

```bash
# Check if recovery script ran
cat ~/.local/lib/cctv/storage_state.json

# Manual recovery
python3 ~/bin/sync_redundancy_manager.py --recover
```

### Issue: No WhatsApp alerts

```bash
# Test wacli
wacli send text --to 917754008079 --message "Test"

# Check path
which wacli

# Manual test
python3 -c "from sync_redundancy_manager import WhatsAppNotifier; WhatsAppNotifier.send('Manual test')"
```

### Issue: Cross-monitor can't SSH

```bash
# Test SSH manually
ssh -i ~/.ssh/id_rsa_cctv_deploy shreyansh@100.94.49.20 "echo OK"

# Check SSH key exists
ls -la ~/.ssh/id_rsa_cctv_deploy

# Check Tailscale
tailscale status
```

---

## 📈 **Performance Impact**

### Memory Usage:
- Redundancy manager: ~50MB
- Cross-monitor: ~30MB
- Total: ~80MB (negligible)

### CPU Usage:
- Health checks: <1% (once per 30s)
- Cross-monitor: <1% (once per 60s)
- Transfer operations: Brief spike during recovery

### Disk I/O:
- Normal: None (only checks mountpoint)
- During recovery: Reads/writes to move files

---

## 🎯 **Summary**

✅ **Redundancy System:**
- Automatic HDD → SSD failover
- Zero data loss with checksums
- Automatic recovery on reconnection
- All transfers logged

✅ **Cross-Monitoring:**
- Each machine watches the other
- Power failure detection
- Service crash detection
- Resource alerts (CPU/memory/disk)

✅ **WhatsApp Integration:**
- All alerts to +91 7754008079
- Alerts from both machines
- Prevented spam with cooldowns

✅ **Survival Mode:**
- Services restart on crash
- State persists across power cycles
- Automatic recovery after power failure

**Status: Ready for production deployment** 🚀
