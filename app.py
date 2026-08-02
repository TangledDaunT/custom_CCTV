import cv2
import time
import logging
import logging.handlers
import threading
import subprocess
import os
import json
import socket
from collections import deque
from datetime import datetime
from flask import Flask, Response, jsonify, render_template_string, send_from_directory, request
from motion import MotionDetector
from db import ensure_db, insert_event, list_events
from model_utils import ensure_mobilenet, get_model_dir
import shutil
import tempfile
import os.path
import pathlib

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

# Video recording for alerts
# Use the attached disk where OS isn't installed: /mnt/cctv-recordings
VIDEO_DIR                = "/mnt/cctv-recordings/cctv_videos"  # override with env VIDEO_DIR if needed
VIDEO_DURATION_SECONDS   = 30
PREBUFFER_SECONDS        = 5
VIDEO_CLEANUP_SECONDS    = 3600  # run cleanup every hour
VIDEO_MAX_AGE_SECONDS    = 24 * 3600  # files older than this are deleted
FFMPEG_CRF              = os.environ.get("FFMPEG_CRF", "23")
FFMPEG_PRESET           = os.environ.get("FFMPEG_PRESET", "veryfast")

# Allow disabling object-detection filtering for testing (set to '1' to disable)
DISABLE_OBJECT_FILTER = os.environ.get("DISABLE_OBJECT_FILTER", "0") == "1"

# Auth token for control endpoints (set via env CCTV_ADMIN_TOKEN)
ADMIN_TOKEN = os.environ.get("CCTV_ADMIN_TOKEN", "changeme-token")

# Motion sensitivity — tuned high (lower = more sensitive)
MOTION_MIN_AREA         = 8000   # only large objects (person/car sized)
MOTION_COOLDOWN_SECONDS = 120    # max 1 alert per 2 minutes
MOTION_FRAMES_TRIGGER   = 8      # needs 8 consecutive frames = 0.8s of solid motion  # was 3 — fires faster
MOTION_BLUR_SIZE        = 11     # was 21 — less blur = finer detail picked up
MOTION_VAR_THRESHOLD    = 16     # was 40 — MOG2 more sensitive to subtle changes

# Schedule — alerts only between 11PM and 6AM
SCHEDULE_START_HOUR = 0
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
_frame_buffer  = deque(maxlen=TARGET_FPS * PREBUFFER_SECONDS)
_latest_bgr    = None
_buffer_lock   = threading.Lock()

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

# Load object detection model (MobileNet-SSD) for person/vehicle filtering
_dnn_net = None
try:
    _dnn_net = ensure_mobilenet(VIDEO_DIR)
    if _dnn_net is not None:
        logger.info("MobileNet-SSD loaded for object filtering")
    else:
        logger.warning("MobileNet-SSD not available; falling back to motion-only alerts")
except Exception as e:
    logger.error(f"Failed to initialize object detection model: {e}")


def detect_person_vehicle(frame, net, conf_thresh=0.4):
    """Return True if a person/vehicle is detected in the frame using MobileNet-SSD."""
    # Allow an operator override to bypass object filtering during tests
    if DISABLE_OBJECT_FILTER:
        logger.info("DISABLE_OBJECT_FILTER=1 — bypassing object detection")
        return True

    if net is None:
        return True  # no model -> allow
    try:
        blob = cv2.dnn.blobFromImage(cv2.resize(frame, (300, 300)), 0.007843, (300, 300), 127.5)
        net.setInput(blob)
        detections = net.forward()
        # class IDs of interest (MobileNet-SSD): person=15, car=7, bus=6, motorbike=14, train=19
        interesting = {15, 7, 6, 14, 19}
        h, w = frame.shape[:2]
        for i in range(detections.shape[2]):
            conf = float(detections[0, 0, i, 2])
            if conf < conf_thresh:
                continue
            cls = int(detections[0, 0, i, 1])
            if cls in interesting:
                # bounding box check
                box = detections[0, 0, i, 3:7] * [w, h, w, h]
                (startX, startY, endX, endY) = box.astype("int")
                area = max(0, endX - startX) * max(0, endY - startY)
                if area >= 500:  # reasonable size
                    return True
        return False
    except Exception as e:
        logger.error(f"Object detection error: {e}")
        return True

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


def _wacli_send_video(number: str, path: str, caption: str):
    """Send a video file via wacli; fall back to text on failure."""
    try:
        result = subprocess.run(
            [WACLI_PATH, "send", "file", "--to", number, "--file", path, "--caption", caption],
            timeout=40, capture_output=True, text=True,
        )
        if result.returncode == 0:
            logger.info(f"✅ Video sent to {number}")
        else:
            logger.warning(f"⚠ Video send failed {number} (rc={result.returncode})")
            logger.debug(f"wacli stderr: {result.stderr}")
            logger.debug(f"wacli stdout: {result.stdout}")
            _wacli_send_text(number, caption)
    except subprocess.TimeoutExpired:
        logger.error(f"⏱ wacli video timeout {number}")
        _wacli_send_text(number, caption)
    except Exception as e:
        logger.error(f"❌ wacli video exception {number}: {e}")
        _wacli_send_text(number, caption)


def send_whatsapp_alert(snapshot_frame):
    # legacy single-frame alert kept for compatibility
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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


def _ensure_video_dir():
    try:
        os.makedirs(VIDEO_DIR, exist_ok=True)
        # ensure DB exists in video dir
        try:
            ensure_db(VIDEO_DIR)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Failed to create video dir {VIDEO_DIR}: {e}")


def _record_event_and_send(start_ts, contours, initial_frame, avg_score=0.0):
    """Record a short video covering the event: include PREBUFFER then live frames for VIDEO_DURATION_SECONDS.
    After recording, send the video via WhatsApp to all recipients.
    """
    _ensure_video_dir()

    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename_base = f"event_{ts_str}"
    avi_path = os.path.join(VIDEO_DIR, filename_base + ".avi")
    mp4_path = os.path.join(VIDEO_DIR, filename_base + ".mp4")

    fourcc = cv2.VideoWriter_fourcc(*"MJPG")
    writer = None
    try:
        # Open writer to temporary AVI
        writer = cv2.VideoWriter(avi_path, fourcc, TARGET_FPS, (FRAME_WIDTH, FRAME_HEIGHT))
        if not writer or not writer.isOpened():
            logger.error(f"VideoWriter failed to open for {avi_path} — aborting recording")
            # ensure any partial file is removed
            try:
                if os.path.exists(avi_path):
                    os.remove(avi_path)
            except Exception:
                pass
            return

        # Write prebuffer frames
        with _buffer_lock:
            pre_frames = list(_frame_buffer)
        logger.info(f"Recording event — prebuffer frames: {len(pre_frames)} to {avi_path}")
        frames_written = 0
        for f in pre_frames:
            try:
                writer.write(f)
                frames_written += 1
            except Exception as e:
                logger.error(f"Error writing prebuffer frame: {e}")

        # Write live frames for duration
        end_time = time.time() + VIDEO_DURATION_SECONDS
        while time.time() < end_time:
            with _buffer_lock:
                f = _latest_bgr.copy() if _latest_bgr is not None else None
            if f is not None:
                try:
                    writer.write(f)
                    frames_written += 1
                except Exception as e:
                    logger.error(f"Error writing live frame: {e}")
            time.sleep(1.0 / max(1, TARGET_FPS))

        # finalize
        try:
            writer.release()
        except Exception:
            pass
        writer = None
        logger.info(f"Finished recording — frames_written={frames_written}; avi_exists={os.path.exists(avi_path)}")

        # Transcode to MP4 (H.264) using ffmpeg for good quality & size
        try:
            if os.path.exists(avi_path):
                cmd = [
                    "ffmpeg", "-y", "-i", avi_path,
                    "-c:v", "libx264", "-preset", FFMPEG_PRESET, "-crf", str(FFMPEG_CRF),
                    "-pix_fmt", "yuv420p", mp4_path,
                ]
                try:
                    res = subprocess.run(cmd, timeout=120, check=False, capture_output=True, text=True)
                    if res.returncode != 0:
                        logger.error(f"ffmpeg failed rc={res.returncode}")
                        logger.error(f"ffmpeg stderr: {res.stderr}")
                        logger.debug(f"ffmpeg stdout: {res.stdout}")
                    else:
                        size = os.path.getsize(mp4_path) if os.path.exists(mp4_path) else 0
                        logger.info(f"ffmpeg transcode succeeded: {mp4_path} ({size} bytes)")
                except subprocess.TimeoutExpired:
                    logger.error("ffmpeg transcode timed out")
                except Exception as e:
                    logger.error(f"ffmpeg transcode exception: {e}")
                # remove the avi to save space
                try:
                    os.remove(avi_path)
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"ffmpeg transcode failed: {e}")

        # create thumbnail
        thumb_path = None
        try:
            if os.path.exists(mp4_path):
                thumb_path = os.path.join(VIDEO_DIR, filename_base + "_thumb.jpg")
                cap = cv2.VideoCapture(mp4_path)
                ret, f = cap.read()
                if ret:
                    cv2.imwrite(thumb_path, f, [cv2.IMWRITE_JPEG_QUALITY, 70])
                cap.release()
        except Exception as e:
            logger.error(f"Thumbnail creation failed: {e}")

        # send via whatsapp
        caption = f"🚨 Motion event recorded at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (score {avg_score:.3f})"
        for number in ALERT_NUMBERS:
            if os.path.exists(mp4_path):
                _wacli_send_video(number, mp4_path, caption)
            else:
                _wacli_send_text(number, caption)

        # record event in DB
        try:
            insert_event(VIDEO_DIR, mp4_path, thumb_path, avg_score)
        except Exception as e:
            logger.error(f"Failed to insert event into DB: {e}")

    except Exception as e:
        logger.error(f"Failed to record/send event video: {e}")
    finally:
        try:
            if writer is not None:
                writer.release()
        except Exception:
            pass


def start_record_and_alert(contours, frame, avg_score=0.0):
    # Immediate status message
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = f"🚨 Motion detected at {ts} — recording {VIDEO_DURATION_SECONDS}s video"
    for n in ALERT_NUMBERS:
        _wacli_send_text(n, msg)

    # Spawn recording thread to create and send video
    t = threading.Thread(target=_record_event_and_send, args=(time.time(), contours, frame, avg_score), daemon=True)
    t.start()


def _video_cleanup_loop():
    _ensure_video_dir()
    while True:
        try:
            now = time.time()
            for fn in os.listdir(VIDEO_DIR):
                path = os.path.join(VIDEO_DIR, fn)
                try:
                    if not os.path.isfile(path):
                        continue
                    mtime = os.path.getmtime(path)
                    if (now - mtime) > VIDEO_MAX_AGE_SECONDS:
                        os.remove(path)
                        logger.info(f"Deleted old video: {path}")
                except Exception as e:
                    logger.error(f"Cleanup error for {path}: {e}")
        except Exception as e:
            logger.error(f"Video cleanup loop error: {e}")
        time.sleep(VIDEO_CLEANUP_SECONDS)


def send_whatsapp_status(message: str):
    """Send a plain status message (stop/start confirmation) to all numbers."""
    for number in ALERT_NUMBERS:
        _wacli_send_text(number, message)


def _check_token_from_request(req):
    # Check Authorization header or ?token= param
    auth = req.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth.split(" ", 1)[1].strip()
        return token == ADMIN_TOKEN
    token = req.args.get("token")
    if token:
        return token == ADMIN_TOKEN
    return False


def require_admin(fn):
    def wrapper(*args, **kwargs):
        if not _check_token_from_request(request):
            return jsonify({"error": "unauthorized"}), 401
        return fn(*args, **kwargs)
    wrapper.__name__ = fn.__name__
    return wrapper


# ── WhatsApp command listener ─────────────────────────────────────────────────
# Authorised senders — only these JIDs can control the system
COMMAND_JIDS = {f"{n}@s.whatsapp.net" for n in ALERT_NUMBERS}

def whatsapp_command_listener():
    """
    Poll wacli's local SQLite DB every 5s for new messages.
    Uses: wacli messages search --json
    Only reacts to exact 'stop' or 'start' from authorised JIDs,
    and only to messages newer than the last check time.
    """
    logger.info("WhatsApp command listener started (DB polling mode)")

    # Start from now — ignore all historical messages
    last_check = datetime.utcnow()

    while True:
        try:
            for keyword in ("stop", "start"):
                result = subprocess.run(
                    [WACLI_PATH, "messages", "search", keyword, "--json"],
                    timeout=10, capture_output=True, text=True,
                )
                if result.returncode != 0 or not result.stdout.strip():
                    continue

                data = json.loads(result.stdout)
                messages = data.get("data", {}).get("messages", [])

                for msg in messages:
                    # Only from authorised JIDs
                    sender = msg.get("SenderJID", "")
                    if sender not in COMMAND_JIDS:
                        continue

                    # Only exact command — not "stop the cron job" etc.
                    text = msg.get("Text", "").strip().lower()
                    if text != keyword:
                        continue

                    # Only newer than last check
                    ts_str = msg.get("Timestamp", "")
                    try:
                        msg_time = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ")
                    except ValueError:
                        continue

                    if msg_time <= last_check:
                        continue

                    # Valid new command — act on it
                    if keyword == "stop" and alerts_enabled():
                        set_alerts_enabled(False)
                        logger.info(f"🔕 Alerts STOPPED by {sender}")
                        send_whatsapp_status("🔕 CCTV alerts STOPPED. Send 'start' to resume.")

                    elif keyword == "start" and not alerts_enabled():
                        set_alerts_enabled(True)
                        logger.info(f"🔔 Alerts STARTED by {sender}")
                        send_whatsapp_status("🔔 CCTV alerts STARTED. Send 'stop' to pause.")

            # Advance the watermark
            last_check = datetime.utcnow()

        except json.JSONDecodeError as e:
            logger.error(f"Command listener JSON parse error: {e}")
        except subprocess.TimeoutExpired:
            logger.warning("wacli messages search timed out")
        except Exception as e:
            logger.error(f"Command listener error: {e}")

        time.sleep(5)


# ── Motion callbacks ──────────────────────────────────────────────────────────
@detector.on_motion_start
def handle_motion_start(timestamp, contours, frame, avg_score=0.0):
    if not alerts_should_fire():
        reason = "alerts disabled" if not alerts_enabled() else "outside schedule"
        logger.info(f"Motion detected — skipping alert ({reason})")
        return
    logger.info(f"🚨 Motion! {len(contours)} region(s) — evaluating for person/vehicle")

    # Apply object filter: ignore pure noise if no person/vehicle detected
    try:
        ok = detect_person_vehicle(frame, _dnn_net, conf_thresh=0.4)
        if not ok:
            logger.info("Motion suppressed: no person/vehicle detected")
            return
    except Exception as e:
        logger.error(f"Object filter failed: {e}")

    logger.info("Person/vehicle detected — recording and alerting")
    start_record_and_alert(contours, frame, avg_score)


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

                # keep raw annotated BGR frame in buffer for video recording
                try:
                    with _buffer_lock:
                        _frame_buffer.append(annotated.copy())
                        # also update latest bgr for recorder
                        global _latest_bgr
                        _latest_bgr = annotated.copy()
                except Exception:
                    pass

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
    <div id="events-list" style="width:100%;max-width:900px;margin-top:12px;color:#ccc">
        <h3 style="font-size:.9rem;color:#9ad">Recent Events</h3>
        <div id="events" style="display:flex;flex-wrap:wrap;gap:10px"></div>
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

    async function loadEvents(){
        try{
            const res = await fetch('/events');
            const items = await res.json();
            const container = document.getElementById('events');
            container.innerHTML = '';
            for(const it of items){
                const div = document.createElement('div');
                div.style.width='160px';div.style.textAlign='center';
                const img = document.createElement('img');
                img.style.width='160px'; img.style.height='90px'; img.style.objectFit='cover';
                if(it.thumb_path){
                    img.src = '/thumbs/' + it.thumb_path.split('/').pop();
                } else img.src='data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="160" height="90"><rect width="100%" height="100%" fill="#222"/></svg>';
                const p = document.createElement('div'); p.style.fontSize='.75rem'; p.style.color='#aaa'; p.textContent = it.ts + ' (' + Math.round(it.score*100)/100 + ')';
                div.appendChild(img); div.appendChild(p);
                container.appendChild(div);
            }
        }catch(e){ }
    }

  document.getElementById('feed').onload = ()=>dot.classList.add('live');
  poll(); setInterval(poll, 2000);
    loadEvents(); setInterval(loadEvents, 60*1000);
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
    if not _check_token_from_request(request):
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(force=True)
    val  = bool(data.get("enabled", True))
    set_alerts_enabled(val)
    return jsonify({"alerts_enabled": val})

@app.route("/reset_background", methods=["POST"])
def reset_background():
    if not _check_token_from_request(request):
        return jsonify({"error": "unauthorized"}), 401
    detector.reset_background()
    return jsonify({"status": "background model reset"})


@app.route("/events")
def events():
    items = list_events(VIDEO_DIR)
    return jsonify(items)


@app.route('/thumbs/<path:filename>')
def thumbs(filename):
    # serve thumbnail images from video dir
    return send_from_directory(VIDEO_DIR, filename)


@app.route('/videos/<path:filename>')
def get_video(filename):
    # protected access to raw video files
    if not _check_token_from_request(request):
        return jsonify({"error": "unauthorized"}), 401
    return send_from_directory(VIDEO_DIR, filename)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Capture thread
    threading.Thread(target=capture_loop, daemon=True, name="capture").start()
    # WhatsApp command listener
    threading.Thread(target=whatsapp_command_listener, daemon=True, name="wa-listener").start()
    # Video cleanup thread
    threading.Thread(target=_video_cleanup_loop, daemon=True, name="video-cleanup").start()
    logger.info(f"CCTV starting on {socket.gethostname()} — alerts={'ON' if alerts_enabled() else 'OFF'}")
    app.run(host="0.0.0.0", port=5000, threaded=True)