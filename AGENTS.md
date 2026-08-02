# Repository agent guidance — custom_CCTV

Purpose: help AI coding agents get productive quickly with minimal context.

Quick summary
- Language: Python (Flask). Serves an MJPEG live camera feed on port 5000.
- Run mode: local dev via `python app.py`; production uses systemd (service name: `cctv`).
- Dependencies: `requirements.txt`. OpenCV is installed on the target Linux box via system package (see `requirements.txt`).

Quick start (development)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

Deployment (server)
- First-time deployment is a one-liner: `sudo bash install.sh` — see [README.md](README.md) for full steps.

Key files
- [app.py](app.py) — main application, camera capture, motion detection and web UI.
- [motion.py](motion.py) — motion detection logic and callbacks used by `app.py`.
- [install.sh](install.sh) — install script that sets up system packages and the systemd service.
- [cctv.service](cctv.service) — systemd unit used on the Linux server.
- [requirements.txt](requirements.txt) — Python dependency hints; OpenCV handled by `install.sh`.
- [README.md](README.md) — detailed developer and deployment instructions.

Guidance for AI agents
- Follow "link, don't embed": prefer linking to existing docs (see [README.md](README.md)) rather than duplicating long instructions.
- Keep changes minimal and focused: small PRs and preserve runtime behavior on the target (low-power x86 CPUs).
- Local testing: run `python3 app.py` if you have a webcam; be mindful that `app.py` writes logs to `/var/log/cctv` and may need privileges on Linux.
- Runtime-sensitive code (camera capture, OpenCV flags, system paths, `WACLI_PATH`) should be changed only with explicit testing guidance.
- Branch naming: `modernize/<short-desc>` or `fix/<short-desc>` for one-off changes.

What to create next (suggestions)
- `.github/copilot-instructions.md` or a per-area skill if you want richer automation (tests, CI, packaging).

For more context, read the project's README before making infra or service-level changes.
