# CCTV Live Feed

Minimal live camera feed server (MJPEG over HTTP) built for a low-power Linux box
with a USB webcam. Runs forever via systemd - auto-starts on boot, auto-restarts
if it ever crashes.

## Workflow

### 1. Develop on your Mac
Edit `app.py` as needed. Test locally if you have a webcam on your Mac:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```
Then open http://localhost:5000 in your browser.

### 2. Push to GitHub
```bash
git add .
git commit -m "your change"
git push
```

### 3. Deploy on the Linux server (first time)
```bash
git clone <your-repo-url> cctv-app
cd cctv-app
sudo bash install.sh
```

That's it - one command (`sudo bash install.sh`) installs dependencies, sets up
a systemd service, and starts the app running forever.

### 4. Deploy updates (every time after)
```bash
cd cctv-app
git pull
sudo bash install.sh
```
Re-running `install.sh` is safe - it just updates the code and restarts the service.

## Useful commands on the server

```bash
sudo systemctl status cctv     # is it running?
sudo systemctl restart cctv    # restart manually
sudo journalctl -u cctv -f     # live logs
```

## Viewing the feed on your phone
Once Tailscale is set up on the server, open on your phone:
```
http://<tailscale-ip-of-server>:5000
```

## Notes
- Camera index is set to 0 (`/dev/video0`) in `app.py` - change `CAMERA_INDEX` if needed.
- Resolution/FPS/quality are tuned conservatively for a Celeron CPU - adjust
  `FRAME_WIDTH`, `FRAME_HEIGHT`, `TARGET_FPS`, `JPEG_QUALITY` in `app.py` if you
  want to push the hardware harder or lighter.
- This is live-feed only for now. Motion detection + WhatsApp alerts will be
  added as a next step without needing to restructure this.
