# Vision Analyzer - Resource Requirements and Deployment Guide

## 📊 Current System Status (HP Laptop)

### Hardware Specs
- **CPU**: Intel Core i3-7100U @ 2.40GHz (2 cores, 4 threads)
- **RAM**: 8GB total
- **Storage**: 187GB available on external HDD
- **GPU**: None (Intel integrated graphics only)

### Current Resource Usage

| Process | CPU | RAM | Purpose |
|---------|-----|-----|---------|
| **Transcriber Script** | 150% | 2.6GB (34%) | Voice journaling with OpenAI Whisper + Llama |
| **CCTV Sync Service** | <1% | 10MB | Snapshot sync from server |
| **Vision Analyzer** | TBD | TBD | Image analysis (new) |
| **System** | 13% | 300MB | OS + desktop |
| **Available** | 37% | 2.8GB | Free resources |

---

## 🔍 Vision Model Resource Requirements

### **Recommended Models for Intel i3 (No GPU)**

#### **Option 1: LLAVA 7B (Recommended)**
- **Model Size**: 4.7GB
- **RAM Required**: 6-8GB total system RAM
- **CPU**: Moderate usage (30-50%)
- **Speed**: 5-15 seconds per image
- **Quality**: Good general vision model
- **Pros**: Balanced performance/quality
- **Cons**: May be slow on CPU

**Resource Calculation:**
```
System baseline: 500MB
Transcriber: 2.6GB
LLAVA model: 4.7GB (when loaded)
Processing overhead: 500MB
-----------------------------------
Total needed: ~8.3GB
Available: 2.8GB
Shortfall: ~5.5GB ⚠️
```

**Verdict**: ⚠️ **NOT RECOMMENDED** - Insufficient RAM with transcriber running

#### **Option 2: MiniCPM-V (Better choice)**
- **Model Size**: 2.5GB
- **RAM Required**: 4-5GB total system RAM
- **CPU**: Lower usage (20-30%)
- **Speed**: 3-8 seconds per image
- **Quality**: Good for security analysis
- **Pros**: Efficient, fast, designed for edge devices
- **Cons**: Less detailed than larger models

**Resource Calculation:**
```
System baseline: 500MB
Transcriber: 2.6GB
MiniCPM-V model: 2.5GB
Processing overhead: 300MB
-----------------------------------
Total needed: ~5.9GB
Available: 2.8GB
Shortfall: ~3.1GB ⚠️
```

**Verdict**: ⚠️ **STILL TIGHT** - Need to optimize transcriber memory

#### **Option 3: Moondream 2 (Best for low resources)**
- **Model Size**: 1.7GB
- **RAM Required**: 3-4GB total system RAM
- **CPU**: Low usage (10-20%)
- **Speed**: 2-5 seconds per image
- **Quality**: Acceptable for basic detection
- **Pros**: Very efficient, fast, edge-optimized
- **Cons**: Less detailed analysis

**Resource Calculation:**
```
System baseline: 500MB
Transcriber: 2.6GB
Moondream model: 1.7GB
Processing overhead: 200MB
-----------------------------------
Total needed: ~5.0GB
Available: 2.8GB
Shortfall: ~2.2GB ⚠️
```

**Verdict**: ✅ **FEASIBLE** if we reduce transcriber memory or use swap

---

## 💡 Recommended Solution

### **Approach 1: Optimized Setup (Recommended)**

1. **Use Moondream 2 model** (1.7GB)
2. **Add 4GB swap space** for safety
3. **Process images sequentially** (one at a time)
4. **Limit processing to off-peak hours** (optional)

**Performance Expectations:**
- **Processing Time**: 2-5 seconds per image
- **CPU Load**: 20-30% during processing
- **Memory**: ~5GB usage (with swap as backup)
- **Throughput**: ~1 image every 10 seconds (including file watching)

### **Approach 2: Aggressive Optimization**

If Approach 1 still causes issues:

1. **Schedule vision analysis during inactive hours**
   - Run analysis batch overnight when transcriber less active
   - Or pause transcriber briefly for image analysis

2. **Use lightweight face detection first**
   - Use OpenCV Haar Cascades for basic person detection
   - Only run full vision analysis if person detected

3. **Reduce transcriber memory footprint**
   - Use smaller Whisper model
   - Limit transcriber to 2GB RAM

---

## 🛠️ Deployment Steps

### Step 1: Install Vision Model

```bash
# On laptop
# Install llava (if enough RAM)
ollama pull llava:7b

# OR install moondream (recommended)
ollama pull moondream

# OR install minicpm-v (alternative)
ollama pull minicpm-v
```

### Step 2: Install Dependencies

```bash
# Already installed: opencv-python
# Ensure other dependencies
pip3 install --user Pillow pybase64
```

### Step 3: Add Swap Space (Recommended)

```bash
# Create 4GB swap file for safety
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# Make permanent
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### Step 4: Create Systemd Service

```bash
# Copy service file
sudo cp vision-analyzer.service /etc/systemd/system/

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable vision-analyzer
sudo systemctl start vision-analyzer
```

---

## 📈 Performance Monitoring

### Check Resource Usage

```bash
# Real-time monitoring
htop

# Or use the monitoring script
./monitor_resources.sh
```

### Expected Performance Metrics

**Idle State:**
- CPU: 0-5%
- RAM: 100MB (model not loaded)

**Processing State:**
- CPU: 20-50% (1-2 cores)
- RAM: 1-2GB spike
- Duration: 2-5 seconds per image

**Queue Management:**
- Max concurrent: 1 (to prevent RAM exhaustion)
- Watch interval: 5 seconds
- Automatic duplicate detection

---

## 🎯 JSON Analysis Output Format

### Example Output

```json
{
  "timestamp": "2026-09-06T01:15:23",
  "time_of_day": "night",
  "lighting": "poor",
  "people_detected": {
    "count": 2,
    "persons": [
      {
        "person_id": "Person 1",
        "position": "left",
        "activity": "walking",
        "appearance": "male, dark clothing, backpack",
        "facing_camera": true
      },
      {
        "person_id": "Person 2",
        "position": "center",
        "activity": "standing",
        "appearance": "female, light jacket",
        "facing_camera": false
      }
    ]
  },
  "objects_detected": [
    {
      "object": "car",
      "type": "vehicle",
      "position": "right background",
      "confidence": 0.92
    },
    {
      "object": "backpack",
      "type": "object",
      "position": "left foreground",
      "confidence": 0.85
    }
  ],
  "scene_description": "Two people walking through parking lot at night with poor lighting conditions",
  "security_alerts": [
    "Multiple people detected after hours",
    "Poor visibility may indicate security risk"
  ],
  "motion_likely": true,
  "confidence_score": 0.89,
  "source_file": "/media/shreyansh/LINUX_SHARE/cctv_snapshots/event_20260906_011523.jpg",
  "analyzed_at": "2026-09-06T01:15:28",
  "model_used": "moondream",
  "file_size_bytes": 102400
}
```

---

## ⚠️ Important Considerations

### 1. **Resource Constraints**

**Current State:**
- Your laptop is **already heavily loaded** (150% CPU, 34% RAM)
- Adding vision analysis will **stress the system further**
- **Risk**: System slowdown, OOM kills, transcriber disruption

**Mitigation:**
- Use smallest efficient model (Moondream)
- Add swap space
- Monitor resources closely
- Process one image at a time

### 2. **Face Recognition**

**NOT RECOMMENDED** for your current setup because:
- Requires additional 1-2GB RAM
- CPU intensive face encoding
- Need known face database
- Current system already under load

**Alternative:**
- Use basic "person detection" without individual identification
- Person 1, Person 2 labels are sufficient
- Analyze face features in the description field

### 3. **Performance Trade-offs**

| Model | Quality | Speed | RAM | Recommended |
|-------|---------|-------|-----|-------------|
| LLAVA 7B | Excellent | Slow (15s) | 8GB | ❌ Too heavy |
| MiniCPM-V | Good | Medium (5s) | 5GB | ⚠️ Tight |
| Moondream | Fair | Fast (3s) | 3GB | ✅ Best choice |

---

## 🔧 Alternative: Batch Processing

If real-time analysis proves too heavy:

### Approach: Nightly Batch Analysis

1. **Sync snapshots throughout the day** (current system)
2. **Run batch analysis at night** when transcriber less active
3. **Queue images for processing** during peak hours

**Benefits:**
- No real-time performance impact
- Can use larger models during low-activity hours
- Better resource utilization

**Implementation:**
- Change systemd service to run at specific times
- Or process in batch mode once per hour
- Use larger model when system is idle

---

## 📊 Monitoring During Deployment

### Critical Metrics to Watch

1. **Memory Pressure:**
   ```bash
   watch -n 1 'free -h && echo "---" && ps aux | grep -E "(daemon_v2|vision_analyzer)"'
   ```

2. **CPU Load:**
   ```bash
   htop -p $(pgrep -d',' -f "daemon_v2|vision_analyzer")
   ```

3. **OOM Events:**
   ```bash
   sudo dmesg -w | grep -i 'out of memory'
   ```

### Warning Signs

- ⚠️ Available RAM < 1GB
- ⚠️ Swap usage > 2GB
- ⚠️ System responsiveness slow
- ⚠️ Transcriber crashes

---

## 🎬 Decision Matrix

### Can I Run This Now?

**If system has:**
- More than 3GB free RAM → ✅ Yes, proceed
- 2-3GB free RAM → ⚠️ Add swap first
- Less than 2GB free RAM → ❌ Not recommended

**Your Current Status:**
- Available: 2.8GB
- Recommendation: ⚠️ **Proceed with caution**
- Required: Add 4GB swap + use Moondream model

---

## 📋 Pre-flight Checklist

Before starting vision analyzer:

- [ ] Check available RAM (> 2.5GB)
- [ ] Add swap space (4GB recommended)
- [ ] Install Moondream model (`ollama pull moondream`)
- [ ] Test Ollama: `ollama run moondream "describe an image"`
- [ ] Monitor transcriber stability
- [ ] Have contingency plan to stop if OOM occurs

---

## 🚀 Recommended Startup Sequence

1. **Add swap space** (one-time)
2. **Pull Moondream model** (one-time)
3. **Test with single image** (manual run)
4. **Monitor resources** during test
5. **If stable, start service**
6. **Watch logs for 30 minutes**
7. **If unstable, switch to batch mode**

---

## Summary

**Feasibility**: ⚠️ **Possible but requires optimization**

**Recommended Setup:**
- Model: Moondream (1.7GB)
- Swap: 4GB added
- Mode: Sequential processing (one at a time)
- Monitoring: Active resource watching

**Risk**: Moderate - system under load

**Alternative**: Batch processing during low-activity hours
