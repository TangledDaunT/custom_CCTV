# CCTV Control

Private, authenticated CCTV monitoring for a low-power Linux camera server. It
provides an Android-first web dashboard, motion-triggered recordings, protected
event playback, and optional WhatsApp notifications.

## Workflow

### 1. Develop on your Mac

Create local-only development secrets before running the server:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
export CCTV_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
export CCTV_BOOTSTRAP_PASSWORD='change-this-development-password'
export CCTV_COOKIE_SECURE=0
export VIDEO_DIR=/tmp/cctv-recordings
python3 app.py
```

Then open http://localhost:5000 and sign in as `admin`. Never reuse the local
password or set `CCTV_COOKIE_SECURE=0` in production.

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

The first install deliberately creates `/etc/cctv/cctv.env` and stops. Edit it
with strong unique values for `CCTV_SECRET_KEY` and `CCTV_BOOTSTRAP_PASSWORD`,
then run `sudo bash install.sh` again. The file is mode `0600` and must never
be committed to Git.

The service binds only to `127.0.0.1:5000`. Copy and adapt
[`Caddyfile.example`](Caddyfile.example), then use Caddy/Tailscale HTTPS to
reach the dashboard. Do not expose port 5000 directly to the Internet.

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

## Viewing on Android

Open the HTTPS Caddy hostname in Chrome, sign in, then use Chrome's **Add to
Home screen** option. The dashboard is touch-first and supports event playback
directly on Android.

## Notes
- Runtime configuration lives in `/etc/cctv/cctv.env`; see
  [`cctv.env.example`](cctv.env.example). Camera resolution, FPS, schedule,
  media location, and WhatsApp recipients are environment settings.
- The initial account is the bootstrap admin. The environment bootstrap
  password is used only while no database users exist; remove it after the
  first successful login.
- Object-detection model files are never downloaded at runtime. Provision them
  in the model directory deliberately; otherwise alerts use the safe
  motion-only fallback.
- Event thumbnails and recordings require an authenticated session and are
  authorized by event ID, not filesystem filename.
