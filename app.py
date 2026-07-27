"""
Simple CCTV live feed server.
Streams MJPEG from a USB webcam (e.g. Logitech C270) over HTTP.

View it at: http://<server-ip-or-tailscale-ip>:5000
"""

import time
import cv2
from flask import Flask, Response, render_template_string

app = Flask(__name__)

# ---- Config ----
CAMERA_INDEX = 0        # /dev/video0 - change if you have multiple cameras
FRAME_WIDTH = 1280
FRAME_HEIGHT = 720
JPEG_QUALITY = 80        # 0-100, lower = less CPU/bandwidth, more compression artifacts
TARGET_FPS = 10          # C270 + Celeron: keep this modest to save CPU

INDEX_HTML = """
<!doctype html>
<html>
  <head>
    <title>Live CCTV Feed</title>
    <style>
      body { background: #111; margin: 0; display: flex; justify-content: center; align-items: center; height: 100vh; }
      img { max-width: 100%; max-height: 100%; border: 2px solid #333; }
    </style>
  </head>
  <body>
    <img src="/video_feed">
  </body>
</html>
"""


def get_camera():
    """Open the camera with retry, so a transient USB glitch doesn't kill the process."""
    while True:
        cap = cv2.VideoCapture(CAMERA_INDEX)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
        if cap.isOpened():
            return cap
        print("Camera not available, retrying in 3s...")
        cap.release()
        time.sleep(3)


def generate_frames():
    cap = get_camera()
    frame_interval = 1.0 / TARGET_FPS
    encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]

    while True:
        start = time.time()
        success, frame = cap.read()

        if not success:
            # Camera dropped - reopen it instead of crashing the stream
            print("Frame read failed, reopening camera...")
            cap.release()
            cap = get_camera()
            continue

        ok, buffer = cv2.imencode(".jpg", frame, encode_params)
        if not ok:
            continue

        frame_bytes = buffer.tobytes()
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"
        )

        # Throttle to target FPS so we don't pin the CPU
        elapsed = time.time() - start
        sleep_time = frame_interval - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)


@app.route("/")
def index():
    return render_template_string(INDEX_HTML)


@app.route("/video_feed")
def video_feed():
    return Response(
        generate_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    # host="0.0.0.0" so it's reachable over Tailscale/LAN, not just localhost
    app.run(host="0.0.0.0", port=5000, threaded=True)
