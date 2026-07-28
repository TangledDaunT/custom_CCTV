import cv2
import time
import logging
import threading
import subprocess
import os
from datetime import datetime
from flask import Flask, Response, jsonify, render_template_string
from motion import MotionDetector

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
CAMERA_INDEX   = 0
FRAME_WIDTH    = 1280
FRAME_HEIGHT   = 720
JPEG_QUALITY   = 80
TARGET_FPS     = 10

# Motion detection tuning
MOTION_MIN_AREA         = 1500
MOTION_COOLDOWN_SECONDS = 10
MOTION_FRAMES_TRIGGER   = 3
MOTION_BLUR_SIZE        = 21

# Schedule — motion alerts only active between these hours (24h)
SCHEDULE_START_HOUR = 23   # 11 PM
SCHEDULE_END_HOUR   = 6    #  6 AM

# WhatsApp
WACLI_PATH    = "/home/linuxbrew/.linuxbrew/bin/wacli"
ALERT_NUMBERS = [
    "917754008079",
    "919415512543",
    "917678815222",
]
SNAPSHOT_PATH = "/tmp/cctv_snapshot.jpg"

app = Flask(__name__)

# ── Shared state ──────────────────────────────────────────────────────────────
_frame_lock    = threading.Lock()
_latest_frame  = None
_camera_ok     = False
_snapshot_lock = threading.Lock()

detector = MotionDetector(
    min_area=MOTION_MIN_AREA,
    cooldown_seconds=MOTION_COOLDOWN_SECONDS,
    motion_frames_trigger=MOTION_FRAMES_TRIGGER,
    blur_size=MOTION_BLUR_SIZE,
)

# ── Schedule helper ───────────────────────────────────────────────────────────

def is_within_schedule():
    """Returns True if current time is between 11PM and 6AM."""
    hour = datetime.now().hour
    return hour >= SCHEDULE_START_HOUR or hour < SCHEDULE_END_HOUR

# ── WhatsApp alert ────────────────────────────────────────────────────────────

def send_whatsapp_alert(snapshot_frame):
    """Send snapshot + alert text to all numbers via wacli."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    message = f"🚨 Motion detected at {ts}"

    with _snapshot_lock:
        try:
            cv2.imwrite(SNAPSHOT_PATH, snapshot_frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            snapshot_saved = True
        except Exception as e:
            logger.error(f"Failed to save snapshot: {e}")
            snapshot_saved = False

    for number in ALERT_NUMBERS:
        try:
            if snapshot_saved and os.path.exists(SNAPSHOT_PATH):
                cmd = [
                    WACLI_PATH, "send", "file",
                    "--to", number,
                    "--file", SNAPSHOT_PATH,
                    "--caption", message,
                ]
            else:
                cmd = [
                    WACLI_PATH, "send", "text",
                    "--to", number,
                    "--message", message,
                ]

            result = subprocess.run(cmd, timeout=15, capture_output=True, text=True)

            if result.returncode == 0:
                logger.info(f"✅ WhatsApp alert sent to {number}")
            else:
                logger.error(f"❌ wacli failed for {number}: {result.stderr.strip()}")

        except subprocess.TimeoutExpired:
            logger.error(f"⏱ wacli timed out for {number}")
        except Exception as e:
            logger.error(f"❌ Alert error for {number}: {e}")

# ── Motion callbacks ──────────────────────────────────────────────────────────

@detector.on_motion_start
def handle_motion_start(timestamp, contours, frame):
    if not is_within_schedule():
        logger.info("Motion detected but outside schedule (11PM–6AM) — skipping alert")
        return
    logger.info(f"🚨 Motion detected! {len(contours)} region(s) — sending WhatsApp alerts")
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
            logger.warning("Camera not available, retrying in 5s…")
            _camera_ok = False
            time.sleep(5)
            continue

        logger.info("Camera opened successfully")
        _camera_ok = True

        while True:
            t0 = time.monotonic()
            ret, frame = cap.read()

            if not ret:
                logger.warning("Frame grab failed — reconnecting camera")
                _camera_ok = False
                break

            annotated, score, motion_active = detector.process_frame(frame)

            if not is_within_schedule():
                cv2.putText(
                    annotated, "ALERTS PAUSED (6AM-11PM)",
                    (10, annotated.shape[0] - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (100, 100, 255), 1
                )

            ok, buf = cv2.imencode(
                ".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
            )
            if ok:
                with _frame_lock:
                    _latest_frame = buf.tobytes()

            elapsed = time.monotonic() - t0
            sleep_for = frame_interval - elapsed
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
            time.sleep(0.1)
            continue

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n"
            + frame +
            b"\r\n"
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
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: #0d0d0d; color: #e0e0e0; font-family: monospace; }
    header { padding: 12px 20px; background: #111; border-bottom: 1px solid #222;
             display: flex; align-items: center; gap: 16px; }
    header h1 { font-size: 1rem; letter-spacing: 0.1em; color: #00ff88; }
    #status-dot { width: 10px; height: 10px; border-radius: 50%; background: #555; }
    #status-dot.live   { background: #00ff88; box-shadow: 0 0 6px #00ff88; }
    #status-dot.motion { background: #ff3333; box-shadow: 0 0 8px #ff3333; }
    main { display: flex; flex-direction: column; align-items: center; padding: 20px; }
    #feed { max-width: 100%; border: 1px solid #222; border-radius: 4px; }
    #motion-banner { display: none; margin-top: 12px; padding: 8px 20px;
                     background: #ff3333; color: #fff; border-radius: 4px;
                     font-weight: bold; letter-spacing: 0.05em; }
    #schedule-banner { margin-top: 8px; padding: 6px 16px; border-radius: 4px;
                       font-size: 0.75rem; color: #888; background: #1a1a1a; }
    #stats { margin-top: 14px; font-size: 0.75rem; color: #666;
             display: flex; gap: 24px; flex-wrap: wrap; justify-content: center; }
    #stats span { color: #aaa; }
  </style>
</head>
<body>
  <header>
    <div id="status-dot"></div>
    <h1>CCTV / {{ hostname }}</h1>
  </header>
  <main>
    <img id="feed" src="/video_feed" alt="Live feed">
    <div id="motion-banner">⚠ MOTION DETECTED</div>
    <div id="schedule-banner">—</div>
    <div id="stats">
      Events: <span id="s-events">—</span>
      &nbsp;|&nbsp; Avg score: <span id="s-score">—</span>%
      &nbsp;|&nbsp; Frames: <span id="s-frames">—</span>
    </div>
  </main>
  <script>
    const dot    = document.getElementById('status-dot');
    const banner = document.getElementById('motion-banner');
    const sched  = document.getElementById('schedule-banner');

    async function poll() {
      try {
        const r = await fetch('/stats');
        const d = await r.json();
        dot.className = d.motion_active ? 'motion' : 'live';
        banner.style.display = d.motion_active ? 'block' : 'none';
        sched.textContent = d.alerts_active
          ? '🟢 Alerts active (11PM – 6AM)'
          : '🟡 Alerts paused — resumes at 11PM';
        document.getElementById('s-events').textContent = d.total_events;
        document.getElementById('s-score').textContent  = d.avg_motion_score;
        document.getElementById('s-frames').textContent = d.frames_processed;
      } catch(e) {
        dot.className = '';
      }
    }

    document.getElementById('feed').onload = () => dot.classList.add('live');
    poll();
    setInterval(poll, 2000);
  </script>
</body>
</html>"""


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    import socket
    return render_template_string(INDEX_HTML, hostname=socket.gethostname())

@app.route("/video_feed")
def video_feed():
    return Response(
        generate_stream(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )

@app.route("/health")
def health():
    return jsonify({"status": "ok", "camera": _camera_ok})

@app.route("/stats")
def stats():
    s = detector.get_stats()
    s["camera_ok"]     = _camera_ok
    s["alerts_active"] = is_within_schedule()
    return jsonify(s)

@app.route("/reset_background", methods=["POST"])
def reset_background():
    detector.reset_background()
    return jsonify({"status": "background model reset"})


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    t = threading.Thread(target=capture_loop, daemon=True)
    t.start()
    logger.info("Capture thread started")
    app.run(host="0.0.0.0", port=5000, threaded=True)