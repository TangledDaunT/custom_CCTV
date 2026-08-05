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
from flask import Flask, Response, abort, jsonify, redirect, render_template, request, send_file, session, url_for
from motion import MotionDetector
from db import (
    audit,
    authenticate_user,
    ensure_db,
    event_by_id,
    insert_event,
    list_events,
    provision_single_admin,
    flag_event,
    insert_notification,
    set_notification_pref,
    get_notification_prefs,
)
from model_utils import ensure_mobilenet
import tempfile
import os.path
import functools
import secrets
import hmac
import hashlib
import base64
from flask import send_from_directory

# ── Logging — rotate logs so they never fill the disk ─────────────────────────
LOG_DIR = os.environ.get("CCTV_LOG_DIR", "/var/log/cctv")
try:
    os.makedirs(LOG_DIR, exist_ok=True)
except PermissionError:
    # Allows local development without weakening the production service path.
    LOG_DIR = os.path.join(tempfile.gettempdir(), "cctv-logs")
    os.makedirs(LOG_DIR, exist_ok=True)
handler = logging.handlers.RotatingFileHandler(
    os.path.join(LOG_DIR, "cctv.log"), maxBytes=5 * 1024 * 1024, backupCount=3
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[handler, logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
CAMERA_INDEX   = int(os.environ.get("CCTV_CAMERA_INDEX", "0"))
FRAME_WIDTH    = int(os.environ.get("CCTV_FRAME_WIDTH", "1280"))
FRAME_HEIGHT   = int(os.environ.get("CCTV_FRAME_HEIGHT", "720"))
JPEG_QUALITY   = int(os.environ.get("CCTV_JPEG_QUALITY", "80"))
TARGET_FPS     = int(os.environ.get("CCTV_TARGET_FPS", "10"))

# Video recording for alerts
# Use the attached disk where OS isn't installed: /mnt/cctv-recordings
VIDEO_DIR                = os.environ.get("VIDEO_DIR", "/mnt/cctv-recordings/cctv_videos")
VIDEO_DURATION_SECONDS   = 30
PREBUFFER_SECONDS        = 5
VIDEO_CLEANUP_SECONDS    = 3600  # run cleanup every hour
VIDEO_MAX_AGE_SECONDS    = 24 * 3600  # files older than this are deleted
FFMPEG_CRF              = os.environ.get("FFMPEG_CRF", "23")
FFMPEG_PRESET           = os.environ.get("FFMPEG_PRESET", "veryfast")

# A motion contour is only a candidate; alerts require a confirmed object over
# several frames. These defaults favour precision over alert volume.
PERSON_CONFIDENCE = float(os.environ.get("CCTV_PERSON_CONFIDENCE", "0.70"))
VEHICLE_CONFIDENCE = float(os.environ.get("CCTV_VEHICLE_CONFIDENCE", "0.80"))
VERIFY_SAMPLES = int(os.environ.get("CCTV_VERIFY_SAMPLES", "6"))
VERIFY_REQUIRED = int(os.environ.get("CCTV_VERIFY_REQUIRED", "4"))
VERIFY_INTERVAL_SECONDS = float(os.environ.get("CCTV_VERIFY_INTERVAL_SECONDS", "0.20"))
VERIFY_MIN_MOVEMENT_PX = int(os.environ.get("CCTV_VERIFY_MIN_MOVEMENT_PX", "12"))

# Browser authentication uses signed server sessions. It must be set in the
# root-owned production environment file; there is deliberately no fallback.
SECRET_KEY = os.environ.get("CCTV_SECRET_KEY")
COOKIE_SECURE = os.environ.get("CCTV_COOKIE_SECURE", "1") == "1"
BOOTSTRAP_USERNAME = os.environ.get("CCTV_BOOTSTRAP_USERNAME", "admin")
BOOTSTRAP_PASSWORD = os.environ.get("CCTV_BOOTSTRAP_PASSWORD")

# Motion sensitivity — tuned high (lower = more sensitive)
MOTION_MIN_AREA         = 8000   # only large objects (person/car sized)
MOTION_COOLDOWN_SECONDS = 120    # max 1 alert per 2 minutes
MOTION_FRAMES_TRIGGER   = 8      # needs 8 consecutive frames = 0.8s of solid motion  # was 3 — fires faster
MOTION_BLUR_SIZE        = 11     # was 21 — less blur = finer detail picked up
MOTION_VAR_THRESHOLD    = 16     # was 40 — MOG2 more sensitive to subtle changes

# Schedule — alerts only between 11PM and 6AM
SCHEDULE_START_HOUR = int(os.environ.get("CCTV_SCHEDULE_START_HOUR", "23"))
SCHEDULE_END_HOUR   = int(os.environ.get("CCTV_SCHEDULE_END_HOUR", "6"))

# WhatsApp
WACLI_PATH    = os.environ.get("WACLI_PATH", "/home/linuxbrew/.linuxbrew/bin/wacli")
ALERT_NUMBERS = [number.strip() for number in os.environ.get("CCTV_ALERT_NUMBERS", "").split(",") if number.strip()]
SNAPSHOT_PATH = os.environ.get("CCTV_SNAPSHOT_PATH", "/var/lib/cctv/cctv_snapshot.jpg")

# State file — persists stop/start across reboots
STATE_FILE = os.environ.get("CCTV_STATE_FILE", "/var/lib/cctv/alerts_enabled")

app = Flask(__name__)
app.config.update(
    SECRET_KEY=SECRET_KEY,
    SESSION_COOKIE_NAME="cctv_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=COOKIE_SECURE,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=8 * 60 * 60,
    MAX_CONTENT_LENGTH=64 * 1024,
)

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
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
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
        logger.error("MobileNet-SSD not available; alerts are fail-closed until the model is provisioned")
except Exception as e:
    logger.error(f"Failed to initialize object detection model: {e}")


def detect_person_vehicle(frame, net):
    """Return high-confidence person/vehicle detections; never trust raw motion."""
    if net is None:
        return []
    try:
        blob = cv2.dnn.blobFromImage(cv2.resize(frame, (300, 300)), 0.007843, (300, 300), 127.5)
        net.setInput(blob)
        detections = net.forward()
        # MobileNet-SSD classes. Higher vehicle threshold prevents static
        # motorcycles, gates and reflections being promoted to alerts.
        classes = {15: ("person", PERSON_CONFIDENCE), 7: ("car", VEHICLE_CONFIDENCE),
                   6: ("bus", VEHICLE_CONFIDENCE), 14: ("motorcycle", VEHICLE_CONFIDENCE),
                   19: ("train", VEHICLE_CONFIDENCE)}
        h, w = frame.shape[:2]
        confirmed = []
        for i in range(detections.shape[2]):
            conf = float(detections[0, 0, i, 2])
            cls = int(detections[0, 0, i, 1])
            if cls not in classes or conf < classes[cls][1]:
                continue
            box = detections[0, 0, i, 3:7] * [w, h, w, h]
            start_x, start_y, end_x, end_y = box.astype("int")
            area = max(0, end_x - start_x) * max(0, end_y - start_y)
            if area >= 500:
                confirmed.append({"label": classes[cls][0], "confidence": conf,
                                  "box": (start_x, start_y, end_x, end_y)})
        return confirmed
    except Exception as e:
        logger.error(f"Object detection error: {e}")
        return []


def verify_moving_object(initial_frame):
    """Require the same high-confidence object to persist and actually move."""
    detections_by_label = {}
    for sample_index in range(VERIFY_SAMPLES):
        if sample_index:
            time.sleep(VERIFY_INTERVAL_SECONDS)
            with _buffer_lock:
                frame = _latest_bgr.copy() if _latest_bgr is not None else None
        else:
            frame = initial_frame.copy()
        if frame is None:
            continue
        for detection in detect_person_vehicle(frame, _dnn_net):
            detections_by_label.setdefault(detection["label"], []).append(detection)

    for label, detections in detections_by_label.items():
        if len(detections) < VERIFY_REQUIRED:
            continue
        centers = [((d["box"][0] + d["box"][2]) / 2, (d["box"][1] + d["box"][3]) / 2) for d in detections]
        first_x, first_y = centers[0]
        movement = max(((x - first_x) ** 2 + (y - first_y) ** 2) ** 0.5 for x, y in centers)
        if movement >= VERIFY_MIN_MOVEMENT_PX:
            best = max(detections, key=lambda item: item["confidence"])
            best["movement_px"] = round(movement, 1)
            return best
        logger.info("Motion suppressed: %s was static across verification frames", label)
    return None

# ── Schedule helper ───────────────────────────────────────────────────────────
def is_within_schedule() -> bool:
    hour = datetime.now().hour
    if SCHEDULE_START_HOUR == SCHEDULE_END_HOUR:
        return True
    if SCHEDULE_START_HOUR < SCHEDULE_END_HOUR:
        return SCHEDULE_START_HOUR <= hour < SCHEDULE_END_HOUR
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
            os.makedirs(os.path.dirname(SNAPSHOT_PATH), exist_ok=True)
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


ROLE_RANK = {"viewer": 1, "operator": 2, "admin": 3}


def _current_user():
    if not session.get("user_id"):
        return None
    return {"id": session["user_id"], "username": session["username"], "role": session["role"]}


def require_login(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not _current_user():
            return redirect(url_for("login", next=request.full_path if request.method == "GET" else None))
        return fn(*args, **kwargs)
    return wrapper


def require_role(role):
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            user = _current_user()
            if not user:
                return jsonify({"error": "authentication required"}), 401
            if ROLE_RANK.get(user["role"], 0) < ROLE_RANK[role]:
                return jsonify({"error": "insufficient permissions"}), 403
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def require_csrf():
    token = request.headers.get("X-CSRF-Token", "") or request.form.get("csrf_token", "")
    if not token or not secrets.compare_digest(token, session.get("csrf_token", "")):
        abort(400, description="Invalid CSRF token")


def initialize_security():
    """Validate secrets and create the first account only from deployment config."""
    if not SECRET_KEY or len(SECRET_KEY) < 32:
        raise RuntimeError("CCTV_SECRET_KEY must be set to a random value of at least 32 characters")
    _ensure_video_dir()
    if not BOOTSTRAP_PASSWORD:
        raise RuntimeError("CCTV_BOOTSTRAP_PASSWORD must be set for the single administrator account.")
    provision_single_admin(VIDEO_DIR, BOOTSTRAP_USERNAME, BOOTSTRAP_PASSWORD)
    audit(VIDEO_DIR, BOOTSTRAP_USERNAME, "single_admin_provisioned")
    logger.info("Single CCTV administrator provisioned")


@app.before_request
def enforce_authenticated_application():
    """Keep every application route private; only the credential form is public."""
    if request.endpoint == "login":
        return None
    if _current_user():
        return None
    # Dashboard assets are not public either. The login view uses inline CSS.
    if request.endpoint == "static":
        abort(404)
    return redirect(url_for("login", next=request.full_path if request.method == "GET" else None))


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
    logger.info(f"Motion candidate: {len(contours)} region(s) — verifying object movement")
    if _dnn_net is None:
        logger.error("Motion suppressed: object model unavailable (fail-closed)")
        return
    verified = verify_moving_object(frame)
    if not verified:
        logger.info("Motion suppressed: no moving person/vehicle confirmed across frames")
        return
    logger.info("Verified %s (%.0f%% confidence, %spx movement) — recording and alerting",
                verified["label"], verified["confidence"] * 100, verified["movement_px"])
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
@require_login
def index():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return render_template("dashboard.html", hostname=socket.gethostname(), user=_current_user(), csrf_token=session["csrf_token"])


@app.route("/login", methods=["GET", "POST"])
def login():
    if _current_user():
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        username, password = request.form.get("username", ""), request.form.get("password", "")
        user = authenticate_user(VIDEO_DIR, username, password)
        if user:
            session.clear()
            session.update(user_id=user["id"], username=user["username"], role=user["role"], csrf_token=secrets.token_urlsafe(32))
            session.permanent = True
            audit(VIDEO_DIR, user["username"], "login")
            target = request.args.get("next", "")
            return redirect(target if target.startswith("/") and not target.startswith("//") else url_for("index"))
        audit(VIDEO_DIR, username.strip()[:64] or None, "login_failed")
        error = "Invalid username or password."
    return render_template("login.html", error=error)


@app.route("/logout", methods=["POST"])
@require_login
def logout():
    require_csrf()
    audit(VIDEO_DIR, _current_user()["username"], "logout")
    session.clear()
    return redirect(url_for("login"))


@app.route("/video_feed")
@require_login
def video_feed():
    return Response(generate_stream(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/health")
@require_login
def health():
    return jsonify({"status": "ok", "camera": _camera_ok})


@app.route("/stats")
@require_login
def stats():
    s = detector.get_stats()
    s.update(
        camera_ok=_camera_ok,
        alerts_enabled=alerts_enabled(),
        schedule_active=is_within_schedule(),
        schedule_label=f"{SCHEDULE_START_HOUR:02d}:00–{SCHEDULE_END_HOUR:02d}:00",
    )
    return jsonify(s)


@app.route("/alerts", methods=["POST"])
@require_role("operator")
def toggle_alerts():
    require_csrf()
    data = request.get_json(force=True)
    val = bool(data.get("enabled", True))
    set_alerts_enabled(val)
    audit(VIDEO_DIR, _current_user()["username"], "alerts_enabled" if val else "alerts_disabled")
    return jsonify({"alerts_enabled": val})


@app.route("/reset_background", methods=["POST"])
@require_role("operator")
def reset_background():
    require_csrf()
    detector.reset_background()
    audit(VIDEO_DIR, _current_user()["username"], "background_reset")
    return jsonify({"status": "background model reset"})


@app.route("/events")
@require_login
def events():
    items = list_events(VIDEO_DIR, request.args.get("limit", 30, type=int), request.args.get("offset", 0, type=int))
    for item in items:
        item["thumbnail_url"] = url_for("event_thumbnail", event_id=item["id"]) if item["thumb_path"] else None
        item["video_url"] = url_for("event_video", event_id=item["id"])
        item.pop("video_path", None)
        item.pop("thumb_path", None)
    return jsonify(items)


def _event_media(event_id, field):
    event = event_by_id(VIDEO_DIR, event_id)
    if not event or not event.get(field):
        abort(404)
    media_path, root = os.path.realpath(event[field]), os.path.realpath(VIDEO_DIR)
    if os.path.commonpath([root, media_path]) != root or not os.path.isfile(media_path):
        abort(404)
    return media_path


@app.route("/events/<int:event_id>/thumbnail")
@require_login
def event_thumbnail(event_id):
    return send_file(_event_media(event_id, "thumb_path"), conditional=True, max_age=3600)


@app.route("/events/<int:event_id>/video")
@require_login
def event_video(event_id):
    return send_file(_event_media(event_id, "video_path"), conditional=True, as_attachment=request.args.get("download") == "1")


# ── Entry point ───────────────────────────────────────────────────────────────
_services_started = False
_services_lock = threading.Lock()


def start_services():
    """Start the camera workers once (Gunicorn must run exactly one worker)."""
    global _services_started
    with _services_lock:
        if _services_started:
            return
        initialize_security()
        threading.Thread(target=capture_loop, daemon=True, name="capture").start()
        threading.Thread(target=whatsapp_command_listener, daemon=True, name="wa-listener").start()
        threading.Thread(target=_video_cleanup_loop, daemon=True, name="video-cleanup").start()
        _services_started = True
        logger.info("CCTV background services started")


if __name__ == "__main__":
    initialize_security()
    start_services()
    logger.info(f"CCTV starting on {socket.gethostname()} — alerts={'ON' if alerts_enabled() else 'OFF'}")
    # Caddy is the network-facing service. Flask binds only to loopback.
    app.run(host="127.0.0.1", port=5000, threaded=True)
