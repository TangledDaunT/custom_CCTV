import cv2
import time
import logging
import logging.handlers
import threading
import subprocess
import os
import socket
from datetime import datetime
from flask import Flask, Response, jsonify, render_template_string
from motion import MotionDetector

# ── Logging — rotate logs so they never fill the disk ─────────────────────────
os.makedirs("/var/log/cctv", exist_ok=True)
handler = logging.handlers.RotatingFileHandler(
    "/var/log/cctv/cctv.log", maxBytes=5 * 1024 * 1024, backupCount=3
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[handler, logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
CAMERA_INDEX   = 0
FRAME_WIDTH    = 1280
FRAME_HEIGHT   = 720
JPEG_QUALITY   = 80
TARGET_FPS     = 10

# Motion sensitivity — tuned high (lower = more sensitive)
MOTION_MIN_AREA         = 500    # was 1500 — catches smaller movements
MOTION_COOLDOWN_SECONDS = 10
MOTION_FRAMES_TRIGGER   = 2      # was 3 — fires faster
MOTION_BLUR_SIZE        = 11     # was 21 — less blur = finer detail picked up
MOTION_VAR_THRESHOLD    = 16     # was 40 — MOG2 more sensitive to subtle changes

# Schedule — alerts only between 11PM and 6AM
SCHEDULE_START_HOUR = 23
SCHEDULE_END_HOUR   = 6

# WhatsApp
WACLI_PATH    = "/home/linuxbrew/.linuxbrew/bin/wacli"
ALERT_NUMBERS = [
    "917754008079",
    "919415512543",
    "917678815222",
]
SNAPSHOT_PATH = "/tmp/cctv_snapshot.jpg"

# State file — persists stop/start across reboots
STATE_FILE = "/tmp/cctv_alerts_enabled"

app = Flask(__name__)

# ── Shared state ──────────────────────────────────────────────────────────────
_frame_lock    = threading.Lock()
_latest_frame  = None
_camera_ok     = False
_snapshot_lock = threading.Lock()

# Alerts enabled flag — load from state file on startup
def _load_alert_state():
    # Default ON. If stopped before reboot, state file contains "0"
    if os.path.exists(STATE_FILE):
        try:
            return open(STATE_FILE).read().strip() != "0"
        except Exception:
            pass
    return True

_alerts_enabled      = _load_alert_state()
_alerts_enabled_lock = threading.Lock()

def _save_alert_state(enabled: bool):
    try:
        with open(STATE_FILE, "w") as f:
            f.write("1" if enabled else "0")
    except Exception as e:
        logger.error(f"Failed to save alert state: {e}")

def alerts_enabled() -> bool:
    with _alerts_enabled_lock:
        return _alerts_enabled

def set_alerts_enabled(val: bool):
    global _alerts_enabled
    with _alerts_enabled_lock:
        _alerts_enabled = val
    _save_alert_state(val)
    status = "ENABLED" if val else "DISABLED"
    logger.info(f"Alerts {status} via WhatsApp command")

# ── Motion detector ───────────────────────────────────────────────────────────
detector = MotionDetector(
    min_area=MOTION_MIN_AREA,
    cooldown_seconds=MOTION_COOLDOWN_SECONDS,
    motion_frames_trigger=MOTION_FRAMES_TRIGGER,
    blur_size=MOTION_BLUR_SIZE,
    var_threshold=MOTION_VAR_THRESHOLD,
)

# ── Schedule helper ───────────────────────────────────────────────────────────
def is_within_schedule() -> bool:
    hour = datetime.now().hour
    return hour >= SCHEDULE_START_HOUR or hour < SCHEDULE_END_HOUR

def alerts_should_fire() -> bool:
    return alerts_enabled() and is_within_schedule()

# ── WhatsApp helpers ──────────────────────────────────────────────────────────
def _wacli_send_text(number: str, message: str):
    try:
        result = subprocess.run(
            [WACLI_PATH, "send", "text", "--to", number, "--message", message],
            timeout=15, capture_output=True, text=True,
        )
        if result.returncode == 0:
            logger.info(f"✅ Sent to {number}")
        else:
            logger.error(f"❌ wacli text error {number}: {result.stderr.strip()}")
    except subprocess.TimeoutExpired:
        logger.error(f"⏱ wacli timeout {number}")
    except Exception as e:
        logger.error(f"❌ wacli exception {number}: {e}")


def _wacli_send_file(number: str, path: str, caption: str):
    try:
        result = subprocess.run(
            [WACLI_PATH, "send", "file", "--to", number, "--file", path, "--caption", caption],
            timeout=20, capture_output=True, text=True,
        )
        if result.returncode == 0:
            logger.info(f"✅ Snapshot sent to {number}")
        else:
            logger.warning(f"⚠ File send failed {number}, falling back to text: {result.stderr.strip()}")
            _wacli_send_text(number, caption)
    except subprocess.TimeoutExpired:
        logger.error(f"⏱ wacli file timeout {number}")
        _wacli_send_text(number, caption)
    except Exception as e:
        logger.error(f"❌ wacli file exception {number}: {e}")


def send_whatsapp_alert(snapshot_frame):
    ts      = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    message = f"🚨 Motion detected at {ts}"

    snapshot_saved = False
    with _snapshot_lock:
        try:
            cv2.imwrite(SNAPSHOT_PATH, snapshot_frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            snapshot_saved = True
        except Exception as e:
            logger.error(f"Snapshot write failed: {e}")

    for number in ALERT_NUMBERS:
        if snapshot_saved and os.path.exists(SNAPSHOT_PATH):
            _wacli_send_file(number, SNAPSHOT_PATH, message)
        else:
            _wacli_send_text(number, message)


def send_whatsapp_status(message: str):
    """Send a plain status message (stop/start confirmation) to all numbers."""
    for number in ALERT_NUMBERS:
        _wacli_send_text(number, message)


# ── WhatsApp command listener ─────────────────────────────────────────────────
def whatsapp_command_listener():
    """
    Poll wacli for incoming messages every 5 seconds.
    Responds to 'stop' and 'start' from any of the registered numbers.
    """
    logger.info("WhatsApp command listener started")
    while True:
        try:
            result = subprocess.run(
                [WACLI_PATH, "receive", "--limit", "5"],
                timeout=10, capture_output=True, text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().splitlines():
                    line_lower = line.strip().lower()
                    # Only act on messages from our registered numbers
                    sender_match = any(n in line for n in ALERT_NUMBERS)
                    if not sender_match:
                        continue

                    if "stop" in line_lower:
                        if alerts_enabled():
                            set_alerts_enabled(False)
                            send_whatsapp_status("🔕 CCTV alerts STOPPED. Send 'start' to resume.")
                        # else already stopped — ignore

                    elif "start" in line_lower:
                        if not alerts_enabled():
                            set_alerts_enabled(True)
                            send_whatsapp_status("🔔 CCTV alerts STARTED. Send 'stop' to pause.")
                        # else already running — ignore

        except subprocess.TimeoutExpired:
            pass  # Normal — no messages
        except Exception as e:
            logger.error(f"Command listener error: {e}")

        time.sleep(5)


# ── Motion callbacks ──────────────────────────────────────────────────────────
@detector.on_motion_start
def handle_motion_start(timestamp, contours, frame):
    if not alerts_should_fire():
        reason = "alerts disabled" if not alerts_enabled() else "outside schedule"
        logger.info(f"Motion detected — skipping alert ({reason})")
        return
    logger.info(f"🚨 Motion! {len(contours)} region(s) — alerting")
    send_whatsapp_alert(frame)


@detector.on_motion_end
def handle_motion_end(timestamp, duration):
    logger.info(f"✅ Motion ended after {duration:.1f}s")


# ── Camera capture loop ───────────────────────────────────────────────────────
def open_camera():
    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
    cap.set(cv2.CAP_PROP_FPS,          TARGET_FPS)
    cap.set(cv2.CAP_PROP_BUFFERSIZE,   1)
    return cap


def capture_loop():
    global _latest_frame, _camera_ok
    frame_interval = 1.0 / TARGET_FPS

    while True:
        cap = open_camera()
        if not cap.isOpened():
            logger.warning("Camera not available — retrying in 5s")
            _camera_ok = False
            time.sleep(5)
            continue

        logger.info("Camera opened successfully")
        _camera_ok = True

        while True:
            t0 = time.monotonic()
            ret, frame = cap.read()

            if not ret:
                logger.warning("Frame grab failed — reconnecting")
                _camera_ok = False
                break

            annotated, score, motion_active = detector.process_frame(frame)

            # Status overlay at bottom of frame
            h = annotated.shape[0]
            if not alerts_enabled():
                overlay = "ALERTS STOPPED (send 'start' on WhatsApp)"
                color   = (0, 0, 200)
            elif not is_within_schedule():
                overlay = "ALERTS PAUSED (6AM-11PM)"
                color   = (100, 100, 255)
            else:
                overlay = None

            if overlay:
                cv2.putText(annotated, overlay, (10, h - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

            ok, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            if ok:
                with _frame_lock:
                    _latest_frame = buf.tobytes()

            elapsed    = time.monotonic() - t0
            sleep_for  = frame_interval - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)

        cap.release()
        time.sleep(2)


# ── MJPEG stream ──────────────────────────────────────────────────────────────
def generate_stream():
    while True:
        with _frame_lock:
            frame = _latest_frame
        if frame is None:
            time.sleep(0.05)
            continue
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        )
        time.sleep(1.0 / TARGET_FPS)


# ── Web UI ────────────────────────────────────────────────────────────────────
INDEX_HTML = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CCTV — {{ hostname }}</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{background:#0d0d0d;color:#e0e0e0;font-family:monospace}
    header{padding:12px 20px;background:#111;border-bottom:1px solid #222;
           display:flex;align-items:center;gap:12px}
    header h1{font-size:1rem;letter-spacing:.1em;color:#00ff88;flex:1}
    #dot{width:10px;height:10px;border-radius:50%;background:#555;flex-shrink:0}
    #dot.live  {background:#00ff88;box-shadow:0 0 6px #00ff88}
    #dot.motion{background:#ff3333;box-shadow:0 0 10px #ff3333}
    main{display:flex;flex-direction:column;align-items:center;padding:16px;gap:10px}
    #feed{max-width:100%;border:1px solid #1e1e1e;border-radius:4px}
    .banner{padding:7px 18px;border-radius:4px;font-size:.8rem;
            font-weight:bold;letter-spacing:.05em;text-align:center}
    #motion-banner{background:#ff3333;color:#fff;display:none}
    #state-banner {background:#1a1a1a;color:#aaa}
    #stats{font-size:.72rem;color:#555;display:flex;gap:20px;flex-wrap:wrap;justify-content:center}
    #stats span{color:#888}
    .btn{padding:6px 14px;border:none;border-radius:3px;cursor:pointer;
         font-family:monospace;font-size:.8rem;font-weight:bold}
    #btn-stop {background:#ff3333;color:#fff}
    #btn-start{background:#00ff88;color:#000}
    #btns{display:flex;gap:10px}
  </style>
</head>
<body>
<header>
  <div id="dot"></div>
  <h1>CCTV / {{ hostname }}</h1>
</header>
<main>
  <img id="feed" src="/video_feed" alt="Live feed">
  <div id="motion-banner" class="banner">⚠ MOTION DETECTED</div>
  <div id="state-banner"  class="banner">—</div>
  <div id="btns">
    <button class="btn" id="btn-stop"  onclick="setAlerts(false)">⏹ Stop Alerts</button>
    <button class="btn" id="btn-start" onclick="setAlerts(true)" >▶ Start Alerts</button>
  </div>
  <div id="stats">
    Events: <span id="s-ev">—</span> &nbsp;|&nbsp;
    Score: <span id="s-sc">—</span>% &nbsp;|&nbsp;
    Frames: <span id="s-fr">—</span>
  </div>
</main>
<script>
  const dot    = document.getElementById('dot');
  const motion = document.getElementById('motion-banner');
  const state  = document.getElementById('state-banner');

  async function poll(){
    try{
      const d = await fetch('/stats').then(r=>r.json());
      dot.className = d.motion_active ? 'motion' : 'live';
      motion.style.display = d.motion_active ? 'block' : 'none';
      if(!d.alerts_enabled)
        state.textContent = '🔕 Alerts STOPPED — send start on WhatsApp or click ▶';
      else if(!d.schedule_active)
        state.textContent = '🟡 Alerts PAUSED — schedule active 11PM–6AM';
      else
        state.textContent = '🟢 Alerts ACTIVE (11PM–6AM schedule)';
      document.getElementById('s-ev').textContent = d.total_events;
      document.getElementById('s-sc').textContent = d.avg_motion_score;
      document.getElementById('s-fr').textContent = d.frames_processed;
    }catch(e){ dot.className=''; }
  }

  async function setAlerts(val){
    await fetch('/alerts', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({enabled: val})});
    poll();
  }

  document.getElementById('feed').onload = ()=>dot.classList.add('live');
  poll(); setInterval(poll, 2000);
</script>
</body>
</html>"""


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template_string(INDEX_HTML, hostname=socket.gethostname())

@app.route("/video_feed")
def video_feed():
    return Response(generate_stream(), mimetype="multipart/x-mixed-replace; boundary=frame")

@app.route("/health")
def health():
    return jsonify({"status": "ok", "camera": _camera_ok})

@app.route("/stats")
def stats():
    s = detector.get_stats()
    s["camera_ok"]      = _camera_ok
    s["alerts_enabled"] = alerts_enabled()
    s["schedule_active"] = is_within_schedule()
    return jsonify(s)

@app.route("/alerts", methods=["POST"])
def toggle_alerts():
    from flask import request
    data = request.get_json(force=True)
    val  = bool(data.get("enabled", True))
    set_alerts_enabled(val)
    return jsonify({"alerts_enabled": val})

@app.route("/reset_background", methods=["POST"])
def reset_background():
    detector.reset_background()
    return jsonify({"status": "background model reset"})


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Capture thread
    threading.Thread(target=capture_loop, daemon=True, name="capture").start()
    # WhatsApp command listener
    threading.Thread(target=whatsapp_command_listener, daemon=True, name="wa-listener").start()
    logger.info(f"CCTV starting on {socket.gethostname()} — alerts={'ON' if alerts_enabled() else 'OFF'}")
    app.run(host="0.0.0.0", port=5000, threaded=True)