import cv2
import time
import logging
import threading
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

# Motion detection tuning — adjust these to suit your scene
MOTION_MIN_AREA          = 1500   # px² — raise if getting false positives from plants/curtains
MOTION_COOLDOWN_SECONDS  = 10     # seconds between alerts
MOTION_FRAMES_TRIGGER    = 3      # consecutive motion frames before firing event
MOTION_BLUR_SIZE         = 21     # Gaussian blur kernel — raise for noisier cameras

app = Flask(__name__)

# ── Shared state ──────────────────────────────────────────────────────────────
_frame_lock   = threading.Lock()
_latest_frame = None          # latest annotated JPEG bytes
_camera_ok    = False

detector = MotionDetector(
    min_area=MOTION_MIN_AREA,
    cooldown_seconds=MOTION_COOLDOWN_SECONDS,
    motion_frames_trigger=MOTION_FRAMES_TRIGGER,
    blur_size=MOTION_BLUR_SIZE,
)

# ── Motion callbacks ──────────────────────────────────────────────────────────

@detector.on_motion_start
def handle_motion_start(timestamp, contours, frame):
    """
    Called once when motion begins (after cooldown).
    Add WhatsApp / email / webhook alert here.
    """
    logger.info(f"🚨 Motion detected! {len(contours)} region(s) moving.")
    # TODO: WhatsApp alert via CallMeBot
    # import requests
    # requests.get("https://api.callmebot.com/whatsapp.php?phone=YOUR_NUMBER&text=Motion+detected!&apikey=YOUR_KEY")


@detector.on_motion_end
def handle_motion_end(timestamp, duration):
    logger.info(f"✅ Motion ended after {duration:.1f}s")


# ── Camera capture loop ───────────────────────────────────────────────────────

def open_camera():
    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
    cap.set(cv2.CAP_PROP_FPS,          TARGET_FPS)
    cap.set(cv2.CAP_PROP_BUFFERSIZE,   1)   # keep buffer tiny → low latency
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

            # ── Motion detection ───────────────────────────────────────────
            annotated, score, motion_active = detector.process_frame(frame)

            # ── Encode to JPEG ─────────────────────────────────────────────
            encode_params = [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
            ok, buf = cv2.imencode(".jpg", annotated, encode_params)
            if ok:
                with _frame_lock:
                    _latest_frame = buf.tobytes()

            # ── Throttle to TARGET_FPS ─────────────────────────────────────
            elapsed = time.monotonic() - t0
            sleep_for = frame_interval - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)

        cap.release()
        time.sleep(2)


# ── MJPEG stream generator ────────────────────────────────────────────────────

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


# ── Routes ────────────────────────────────────────────────────────────────────

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
    #status-dot.live { background: #00ff88; box-shadow: 0 0 6px #00ff88; }
    #status-dot.motion { background: #ff3333; box-shadow: 0 0 8px #ff3333; }
    main { display: flex; flex-direction: column; align-items: center; padding: 20px; }
    #feed { max-width: 100%; border: 1px solid #222; border-radius: 4px; }
    #stats { margin-top: 14px; font-size: 0.75rem; color: #666;
             display: flex; gap: 24px; flex-wrap: wrap; justify-content: center; }
    #stats span { color: #aaa; }
    #motion-banner { display: none; margin-top: 12px; padding: 8px 20px;
                     background: #ff3333; color: #fff; border-radius: 4px;
                     font-weight: bold; letter-spacing: 0.05em; }
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
    <div id="stats">
      Events: <span id="s-events">—</span>
      &nbsp;|&nbsp; Avg score: <span id="s-score">—</span>%
      &nbsp;|&nbsp; Frames: <span id="s-frames">—</span>
    </div>
  </main>
  <script>
    const dot    = document.getElementById('status-dot');
    const banner = document.getElementById('motion-banner');

    async function poll() {
      try {
        const r = await fetch('/stats');
        const d = await r.json();
        dot.className = d.motion_active ? 'motion' : 'live';
        banner.style.display = d.motion_active ? 'block' : 'none';
        document.getElementById('s-events').textContent = d.total_events;
        document.getElementById('s-score').textContent  = d.avg_motion_score;
        document.getElementById('s-frames').textContent = d.frames_processed;
      } catch(e) {
        dot.className = '';
      }
    }

    // Feed load = camera is live
    document.getElementById('feed').onload = () => dot.classList.add('live');

    poll();
    setInterval(poll, 2000);
  </script>
</body>
</html>"""


@app.route("/")
def index():
    import socket
    hostname = socket.gethostname()
    return render_template_string(INDEX_HTML, hostname=hostname)


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
    s["camera_ok"] = _camera_ok
    return jsonify(s)


@app.route("/reset_background", methods=["POST"])
def reset_background():
    """Call this if the camera scene changes and you're getting false positives."""
    detector.reset_background()
    return jsonify({"status": "background model reset"})


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    t = threading.Thread(target=capture_loop, daemon=True)
    t.start()
    logger.info("Capture thread started")
    app.run(host="0.0.0.0", port=5000, threaded=True)