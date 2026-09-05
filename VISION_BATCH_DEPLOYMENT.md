# Vision Batch Processor Deployment Guide

## ⏰ **Schedule**
- **Daily**: 11:00 AM - 12:30 PM (90 minutes max)
- **Process**: All pending images in one batch
- **Transcriber**: PAUSED during processing (SIGSTOP/SIGCONT)

---

## 🔄 **Workflow**

```
11:00 AM → Start batch processor
    ↓
Pause transcriber (SIGSTOP)
    ↓
Send WhatsApp: "🔍 Vision batch started"
    ↓
Process all pending images
    ↓
Generate analysis JSON files
    ↓
Log all processed images
    ↓
Send WhatsApp: "✅ Vision batch complete"
    ↓
Resume transcriber (SIGCONT)
    ↓
Send WhatsApp: "▶️ Transcriber RESUMED"
    ↓
Finish (or at 12:30 PM max)
```

---

## 📱 **WhatsApp Alerts**

**Sent to:** +91 77754008079

### Alert 1: Start
```
🔍 Vision batch started
Time: 11:00 AM
Transcriber: PAUSED
Model: minicpm-v
```

### Alert 2: Completion
```
✅ Vision batch complete!
Images processed: 15
People detected: 23
Objects found: 45
Duration: 234s
Time: 11:04
```

### Alert 3: Resume
```
▶️ Transcriber RESUMED
```

---

## 🚀 **Deployment Steps**

### Step 1: Install Dependencies

```bash
# On laptop
# Install Ollama vision model
ollama pull minicpm-v

# Test model
ollama run minicpm-v "describe a car"
```

Expected output: Description of a car

### Step 2: Find Transcriber PID

```bash
# Find running transcriber process
pgrep -f "daemon_v2.py" | head -1

# Should output: 2337278 (or similar)
```

Update `CONFIG["transcriber_pid"]` in script if different.

### Step 3: Install Script

```bash
# Copy to system location
sudo cp vision_batch_processor.py /usr/local/bin/
sudo chmod +x /usr/local/bin/vision_batch_processor.py

# Create directories
sudo mkdir -p /var/log/cctv /var/lib/cctv
sudo chown shreyansh:shreyansh /var/log/cctv /var/lib/cctv
```

### Step 4: Manual Test (Before Automation)

```bash
# Test pause/resume manually first
python3 vision_batch_processor.py
```

Verify:
- ✓ Transcriber pauses (check with `ps aux | grep daemon_v2`)
- ✓ WhatsApp messages received
- ✓ Images processed
- ✓ Transcriber resumes
- ✓ Analysis files created in `/media/shreyansh/LINUX_SHARE/cctv_analysis/`

### Step 5: Install Systemd Service

```bash
# Copy service files
sudo cp vision-batch.service /etc/systemd/system/
sudo cp vision-batch.timer /etc/systemd/system/

# Reload systemd
sudo systemctl daemon-reload

# Enable timer (starts automatically at 11 AM daily)
sudo systemctl enable vision-batch.timer

# Start timer
sudo systemctl start vision-batch.timer

# Check timer status
sudo systemctl list-timers vision-batch.timer
```

---

## 🧪 **Testing**

### Test 1: Manual Run

```bash
# Run once manually
sudo /usr/local/bin/vision_batch_processor.py

# Check logs
tail -f /var/log/cctv/vision_batch.log
```

### Test 2: Check Transcriber Pause/Resume

```bash
# In terminal 1: Monitor transcriber
watch -n 1 'ps aux | grep daemon_v2'

# In terminal 2: Run processor
sudo /usr/local/bin/vision_batch_processor.py

# Observe:
# - Process status changes to "T" (stopped) during processing
# - Process status changes back to "R/S" (running) after completion
```

### Test 3: Verify WhatsApp Alerts

Check phone for 3 messages:
1. "🔍 Vision batch started"
2. "✅ Vision batch complete!"
3. "▶️ Transcriber RESUMED"

### Test 4: Check Analysis Output

```bash
# List analysis files
ls -la /media/shreyansh/LINUX_SHARE/cctv_analysis/

# View one analysis
cat /media/shreyansh/LINUX_SHARE/cctv_analysis/*_analysis_*.json | jq . | less
```

Expected JSON structure:
```json
{
  "timestamp": "2026-09-06T11:05:23",
  "time_of_day": "morning",
  "people_detected": {
    "count": 2,
    "persons": [
      {
        "person_id": "Person 1",
        "position": "center",
        "activity": "walking",
        "appearance": "male, dark shirt, jeans",
        "facing_camera": true
      }
    ]
  },
  "objects_detected": [
    {
      "object": "car",
      "type": "vehicle",
      "position": "background",
      "confidence": 0.92
    }
  ],
  "scene_description": "Person walking near parked car",
  "confidence_score": 0.89
}
```

---

## 📊 **Monitoring**

### View Logs

```bash
# Batch processor logs
sudo tail -f /var/log/cctv/vision_batch.log

# Processed images log
tail -f /var/log/cctv/vision_processed_images.log

# Systemd logs
sudo journalctl -u vision-batch -f
```

### Check Timer Schedule

```bash
sudo systemctl list-timers | grep vision-batch
```

Output should show:
```
NEXT                         LEFT          LAST                         PASSED
Sat 2026-09-06 11:00:00 IST  9h left       n/a                          n/a
```

### Manual Trigger (For Testing)

```bash
# Run immediately (bypass timer)
sudo systemctl start vision-batch

# Check status
sudo systemctl status vision-batch
```

---

## ⚠️ **Important Notes**

### 1. Transcriber PID

If transcriber restarts (PID changes):
```bash
# Find new PID
pgrep -f daemon_v2.py

# Update config file
sudo nano /usr/local/bin/vision_batch_processor.py
# Change: "transcriber_pid": 2337278 to new PID
```

### 2. Time Limit

- **Max duration**: 90 minutes (11:00-12:30 PM)
- If processing takes longer, batch will stop at 12:30 PM
- Remaining images processed next day

### 3. Early Completion

- If batch finishes early (e.g., 11:45 AM)
- Transcriber resumes immediately
- No need to wait until 12:30 PM

### 4. Failure Recovery

If script crashes:
- Transcriber will NOT resume automatically
- Manual resume: `kill -CONT $(pgrep -f daemon_v2.py)`
- Check logs: `/var/log/cctv/vision_batch.log`

---

## 🔧 **Troubleshooting**

### Issue: Transcriber Not Pausing

```bash
# Check if PID is correct
pgrep -f daemon_v2.py

# Test pause manually
kill -STOP $(pgrep -f daemon_v2.py)

# Check status
ps aux | grep daemon_v2 | grep T

# Resume manually
kill -CONT $(pgrep -f daemon_v2.py)
```

### Issue: WhatsApp Not Sending

```bash
# Test wacli manually
wacli send text --to 917754008079 --message "Test"

# Check wacli path
which wacli
# Should be: /usr/local/bin/wacli
```

### Issue: Ollama Model Not Found

```bash
# List available models
ollama list

# Pull minicpm-v
ollama pull minicpm-v

# Test model
ollama run minicpm-v "Describe this image"
```

### Issue: No Images Processed

```bash
# Check snapshot directory
ls -la /media/shreyansh/LINUX_SHARE/cctv_snapshots/

# Check state file (already processed)
cat /var/lib/cctv/vision_processed.json

# Manually clear state to reprocess ALL
rm /var/lib/cctv/vision_processed.json
```

---

## 📈 **Performance Expectations**

### Processing Speed

- **MiniCPM-V model**: 5-10 seconds per image
- **Throughput**: ~6-12 images per minute
- **90-minute capacity**: ~100-200 images

### Resource Usage

During processing (transcriber paused):
- **CPU**: 80% (all cores)
- **RAM**: 4-5GB
- **Disk**: Minimal I/O

---

## 🎯 **Quick Commands**

```bash
# View next scheduled run
sudo systemctl list-timers vision-batch.timer

# Run now (test)
sudo systemctl start vision-batch

# View logs live
sudo journalctl -u vision-batch -f

# Check status
sudo systemctl status vision-batch

# Disable timer
sudo systemctl stop vision-batch.timer

# Enable timer
sudo systemctl start vision-batch.timer
```

---

## ✅ **Verification Checklist**

After deployment, verify:

- [ ] MiniCPM-V model installed
- [ ] Script executable at `/usr/local/bin/vision_batch_processor.py`
- [ ] Directories created: `/var/log/cctv`, `/var/lib/cctv`
- [ ] Service files installed
- [ ] Timer enabled and showing next run at 11:00 AM
- [ ] WhatsApp working (send test message)
- [ ] Transcriber PID correct in config
- [ ] Manual test successful
- [ ] WhatsApp alerts received
- [ ] Analysis files generated
- [ ] Transcriber resumes after processing

---

**Status:** Ready for deployment

**Next Run:** Tomorrow at 11:00 AM (automatic)

**Manual Test:** Run anytime with `sudo /usr/local/bin/vision_batch_processor.py`
