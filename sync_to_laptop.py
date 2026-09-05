#!/usr/bin/env python3
"""
CCTV Snapshot Sync Service
Syncs snapshot images (not videos) from home server to external hard disk on laptop.
Generates structured JSON logs and sends WhatsApp alerts.
"""

import os
import sys
import json
import time
import logging
import argparse
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

# Configuration
CONFIG = {
    "server_video_dir": os.environ.get("VIDEO_DIR", "/mnt/cctv-recordings/cctv_videos"),
    "log_file": os.environ.get("CCTV_SYNC_LOG", "/var/log/cctv_sync.log"),
    "state_file": os.environ.get("CCTV_SYNC_STATE", "/var/lib/cctv/sync_state.json"),
    "wacli_path": os.environ.get("WACLI_PATH", "/home/linuxbrew/.linuxbrew/bin/wacli"),
    "alert_numbers": [n.strip() for n in os.environ.get("CCTV_ALERT_NUMBERS", "").split(",") if n.strip()],

    # Laptop SSH connection (via Tailscale)
    "laptop_host": os.environ.get("CCTV_LAPTOP_HOST", "100.99.161.57"),
    "laptop_user": os.environ.get("CCTV_LAPTOP_USER", "shreyansh"),
    "laptop_remote_dir": os.environ.get("CCTV_REMOTE_DIR", "/mnt/external/cctv_snapshots"),
    "ssh_key_path": os.environ.get("CCTV_SSH_KEY", os.path.expanduser("~/.ssh/id_rsa_cctv_sync")),

    # Sync settings
    "max_age_hours": 24,  # Only sync files younger than this
    "batch_size": 100,    # Max files per sync run
    "retry_count": 3,
    "retry_delay": 10,
}

# Setup structured JSON logging
class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_obj = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj)

def setup_logging():
    logger = logging.getLogger("cctv_sync")
    logger.setLevel(logging.INFO)

    # File handler with JSON format
    os.makedirs(os.path.dirname(CONFIG["log_file"]), exist_ok=True)
    fh = logging.FileHandler(CONFIG["log_file"])
    fh.setFormatter(JSONFormatter())
    logger.addHandler(fh)

    # Console handler for manual runs
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(ch)

    return logger

logger = setup_logging()


class SyncState:
    """Manages persistent sync state to avoid re-syncing old files."""

    def __init__(self, state_file: str):
        self.state_file = state_file
        self.state = self._load()

    def _load(self) -> Dict:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load state file: {e}")
        return {"last_sync": None, "synced_files": []}

    def save(self):
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        with open(self.state_file, 'w') as f:
            json.dump(self.state, f, indent=2)

    def mark_synced(self, filename: str):
        """Add file to synced list."""
        if filename not in self.state["synced_files"]:
            self.state["synced_files"].append(filename)
            # Keep only last 1000 files in state to prevent unbounded growth
            if len(self.state["synced_files"]) > 1000:
                self.state["synced_files"] = self.state["synced_files"][-1000:]

    def is_synced(self, filename: str) -> bool:
        """Check if file was already synced."""
        # Check remote directory instead of state to avoid duplicates
        return False

    def update_last_sync(self):
        self.state["last_sync"] = datetime.utcnow().isoformat() + "Z"
        self.save()


class WhatsAppNotifier:
    """Send WhatsApp alerts for sync events."""

    @staticmethod
    def send_text(number: str, message: str):
        try:
            result = subprocess.run(
                [CONFIG["wacli_path"], "send", "text", "--to", number, "--message", message],
                timeout=15, capture_output=True, text=True
            )
            if result.returncode == 0:
                logger.info(f"WhatsApp alert sent to {number}")
                return True
            else:
                logger.error(f"WhatsApp send failed: {result.stderr}")
                return False
        except Exception as e:
            logger.error(f"WhatsApp send error: {e}")
            return False

    @staticmethod
    def send_file(number: str, file_path: str, caption: str):
        try:
            result = subprocess.run(
                [CONFIG["wacli_path"], "send", "file", "--to", number,
                 "--file", file_path, "--caption", caption],
                timeout=20, capture_output=True, text=True
            )
            if result.returncode == 0:
                logger.info(f"WhatsApp file sent to {number}")
                return True
            else:
                logger.warning(f"WhatsApp file send failed, falling back to text")
                return WhatsAppNotifier.send_text(number, caption)
        except Exception as e:
            logger.error(f"WhatsApp file send error: {e}")
            return WhatsAppNotifier.send_text(number, caption)

    @classmethod
    def alert_new_snapshots(cls, count: int, snapshot_paths: List[str]):
        """Send alert for new synced snapshots."""
        if not CONFIG["alert_numbers"] or count == 0:
            return

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = f"📸 {count} new CCTV snapshot(s) synced to external disk at {timestamp}"

        # Send text alert to all numbers
        for number in CONFIG["alert_numbers"]:
            cls.send_text(number, message)

        # Optionally send first snapshot as preview
        if snapshot_paths and os.path.exists(snapshot_paths[0]):
            for number in CONFIG["alert_numbers"][:1]:  # Only to first number
                cls.send_file(number, snapshot_paths[0],
                             f"📸 Latest snapshot from {timestamp}")


class SnapshotSyncer:
    """Main sync logic."""

    def __init__(self):
        self.state = SyncState(CONFIG["state_file"])

    def find_snapshots(self) -> List[Path]:
        """Find snapshot files (jpg/png) in video directory, excluding videos."""
        video_dir = Path(CONFIG["server_video_dir"])
        if not video_dir.exists():
            logger.error(f"Video directory does not exist: {video_dir}")
            return []

        snapshots = []
        cutoff_time = time.time() - (CONFIG["max_age_hours"] * 3600)

        # Find all image files (exclude videos)
        for ext in ['*.jpg', '*.jpeg', '*.png']:
            for img_path in video_dir.glob(ext):
                # Skip thumbnails (we want main snapshots)
                if '_thumb' in img_path.name:
                    continue

                # Check age
                if img_path.stat().st_mtime >= cutoff_time:
                    snapshots.append(img_path)

        # Sort by modification time (oldest first)
        snapshots.sort(key=lambda p: p.stat().st_mtime)

        logger.info(f"Found {len(snapshots)} snapshots to sync")
        return snapshots

    def test_ssh_connection(self) -> bool:
        """Test SSH connection to laptop."""
        logger.info(f"Testing SSH connection to {CONFIG['laptop_host']}...")

        cmd = [
            "ssh",
            "-i", CONFIG["ssh_key_path"],
            "-o", "ConnectTimeout=10",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            f"{CONFIG['laptop_user']}@{CONFIG['laptop_host']}",
            "echo", "SSH_OK"
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode == 0 and "SSH_OK" in result.stdout:
                logger.info("SSH connection successful")
                return True
            else:
                logger.error(f"SSH connection failed: {result.stderr}")
                return False
        except subprocess.TimeoutExpired:
            logger.error("SSH connection timed out")
            return False
        except Exception as e:
            logger.error(f"SSH connection error: {e}")
            return False

    def sync_snapshots(self, snapshots: List[Path]) -> Dict:
        """Sync snapshots to laptop using rsync."""
        if not snapshots:
            return {"success": True, "synced": 0, "failed": 0}

        if not self.test_ssh_connection():
            return {"success": False, "synced": 0, "failed": len(snapshots),
                   "error": "SSH connection failed"}

        # Ensure remote directory exists
        try:
            subprocess.run([
                "ssh", "-i", CONFIG["ssh_key_path"],
                f"{CONFIG['laptop_user']}@{CONFIG['laptop_host']}",
                f"mkdir -p {CONFIG['laptop_remote_dir']}"
            ], check=True, timeout=10, capture_output=True)
        except Exception as e:
            logger.error(f"Failed to create remote directory: {e}")
            return {"success": False, "synced": 0, "failed": len(snapshots)}

        # Batch sync using rsync
        synced_count = 0
        failed_count = 0
        synced_paths = []

        # Process in batches
        for i in range(0, len(snapshots), CONFIG["batch_size"]):
            batch = snapshots[i:i + CONFIG["batch_size"]]

            # Create temporary file list
            temp_file = f"/tmp/rsync_list_{os.getpid()}.txt"
            with open(temp_file, 'w') as f:
                for p in batch:
                    f.write(f"{p}\n")

            try:
                # Use rsync with --files-from
                cmd = [
                    "rsync", "-av", "--timeout=60",
                    "-e", f"ssh -i {CONFIG['ssh_key_path']}",
                    "--include=*.jpg", "--include=*.jpeg", "--include=*.png",
                    "--exclude=*.mp4", "--exclude=*.avi", "--exclude=*.mkv",
                    "--files-from=" + temp_file,
                    "--no-relative",
                    f"{CONFIG['laptop_user']}@{CONFIG['laptop_host']}:{CONFIG['laptop_remote_dir']}/"
                ]

                for attempt in range(CONFIG["retry_count"]):
                    try:
                        result = subprocess.run(
                            cmd, capture_output=True, text=True, timeout=120
                        )

                        if result.returncode == 0:
                            synced_count += len(batch)
                            synced_paths.extend(batch)
                            logger.info(f"Synced batch of {len(batch)} snapshots")
                            break
                        else:
                            logger.warning(f"Rsync attempt {attempt+1} failed: {result.stderr}")
                            if attempt < CONFIG["retry_count"] - 1:
                                time.sleep(CONFIG["retry_delay"])
                            else:
                                failed_count += len(batch)
                                logger.error(f"Failed to sync batch: {result.stderr}")

                    except subprocess.TimeoutExpired:
                        logger.warning(f"Rsync attempt {attempt+1} timed out")
                        if attempt < CONFIG["retry_count"] - 1:
                            time.sleep(CONFIG["retry_delay"])
                        else:
                            failed_count += len(batch)
                            logger.error("Rsync timed out after all retries")

            except Exception as e:
                logger.error(f"Sync error: {e}")
                failed_count += len(batch)
            finally:
                # Clean up temp file
                try:
                    os.remove(temp_file)
                except:
                    pass

        return {
            "success": failed_count == 0,
            "synced": synced_count,
            "failed": failed_count,
            "paths": [str(p) for p in synced_paths]
        }

    def run_sync(self) -> Dict:
        """Main sync execution."""
        start_time = time.time()

        logger.info("=== Starting CCTV snapshot sync ===")

        # Find snapshots
        snapshots = self.find_snapshots()

        # Sync to laptop
        result = self.sync_snapshots(snapshots)

        # Update state
        self.state.update_last_sync()

        # Send alerts for successful syncs
        if result["synced"] > 0:
            WhatsAppNotifier.alert_new_snapshots(result["synced"], result.get("paths", []))

        # Log summary
        elapsed = time.time() - start_time
        summary = {
            "event": "sync_complete",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "duration_seconds": round(elapsed, 2),
            "snapshots_found": len(snapshots),
            "synced": result["synced"],
            "failed": result["failed"],
            "success": result["success"]
        }
        logger.info(json.dumps(summary))

        return summary


def main():
    parser = argparse.ArgumentParser(description="CCTV Snapshot Sync Service")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--interval", type=int, default=300,
                       help="Run interval in seconds (default: 300)")
    parser.add_argument("--test-ssh", action="store_true", help="Test SSH connection")
    args = parser.parse_args()

    syncer = SnapshotSyncer()

    if args.test_ssh:
        success = syncer.test_ssh_connection()
        sys.exit(0 if success else 1)

    if args.once:
        result = syncer.run_sync()
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["success"] else 1)

    # Continuous mode
    logger.info(f"Starting sync service (interval={args.interval}s)")
    while True:
        try:
            syncer.run_sync()
        except Exception as e:
            logger.error(f"Sync loop error: {e}")

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
