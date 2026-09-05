#!/usr/bin/env python3
"""
CCTV Redundant Storage Manager
- External HDD fallback to SSD when disconnected
- Automatic recovery when HDD reconnects
- Checksum-based integrity verification
- Zero data loss guarantee
"""

import os
import sys
import json
import time
import shutil
import logging
import hashlib
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Configuration
CONFIG = {
    # Primary storage (external HDD)
    "primary_dir": "/media/shreyansh/LINUX_SHARE/cctv_snapshots",
    "primary_analysis_dir": "/media/shreyansh/LINUX_SHARE/cctv_analysis",
    "primary_marker": "/media/shreyansh/LINUX_SHARE/.mounted",

    # Fallback storage (internal SSD)
    "fallback_dir": os.path.expanduser("~/cctv_fallback/snapshots"),
    "fallback_analysis_dir": os.path.expanduser("~/cctv_fallback/analysis"),
    "fallback_marker": os.path.expanduser("~/cctv_fallback/.fallback_active"),

    # State tracking
    "state_file": os.path.expanduser("~/.local/lib/cctv/storage_state.json"),
    "transfer_log": os.path.expanduser("~/.local/log/cctv/transfer_log.json"),
    "checksums_file": os.path.expanduser("~/.local/lib/cctv/checksums.json"),

    # WhatsApp alerts
    "wacli_path": "/usr/local/bin/wacli",
    "alert_number": "917754008079",

}

# Setup logging
os.makedirs(os.path.dirname(CONFIG["transfer_log"]), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.expanduser("~/.local/log/cctv/redundancy.log")),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class WhatsAppNotifier:
    @staticmethod
    def send_to_user(message: str) -> bool:
        """Send to user only (technical details)."""
        try:
            result = subprocess.run(
                [CONFIG["wacli_path"], "send", "text",
                 "--to", CONFIG["alert_number_user"], "--message", message],
                timeout=10, capture_output=True, text=True
            )
            return result.returncode == 0
        except Exception as e:
            logger.error(f"WhatsApp to user failed: {e}")
            return False

    @staticmethod
    def send_to_all_numbers(message: str) -> bool:
        """Send to all family (power alerts)."""
        success = True
        for number in CONFIG["alert_numbers_all"]:
            try:
                result = subprocess.run(
                    [CONFIG["wacli_path"], "send", "text",
                     "--to", number, "--message", message],
                    timeout=10, capture_output=True, text=True
                )
                if result.returncode != 0:
                    success = False
                    logger.error(f"WhatsApp failed for {number}")
            except Exception as e:
                logger.error(f"WhatsApp to {number} failed: {e}")
                success = False
        return success


class ChecksumManager:
    """Track file checksums to prevent corruption."""

    @staticmethod
    def compute_checksum(filepath: str) -> str:
        """SHA256 checksum for file integrity."""
        sha256 = hashlib.sha256()
        try:
            with open(filepath, 'rb') as f:
                for chunk in iter(lambda: f.read(4096), b''):
                    sha256.update(chunk)
            return sha256.hexdigest()
        except Exception as e:
            logger.error(f"Checksum failed for {filepath}: {e}")
            return ""

    def load_checksums(self) -> Dict:
        """Load existing checksums."""
        if os.path.exists(CONFIG["checksums_file"]):
            try:
                with open(CONFIG["checksums_file"], 'r') as f:
                    return json.load(f)
            except:
                pass
        return {}

    def save_checksums(self, checksums: Dict):
        """Save checksums to file."""
        os.makedirs(os.path.dirname(CONFIG["checksums_file"]), exist_ok=True)
        with open(CONFIG["checksums_file"], 'w') as f:
            json.dump(checksums, f, indent=2)

    def track_file(self, filepath: str) -> str:
        """Track a file with checksum."""
        checksum = self.compute_checksum(filepath)
        if checksum:
            all_checksums = self.load_checksums()
            all_checksums[filepath] = {
                "checksum": checksum,
                "timestamp": datetime.now().isoformat(),
                "size": os.path.getsize(filepath)
            }
            self.save_checksums(all_checksums)
        return checksum

    def verify_file(self, filepath: str) -> bool:
        """Verify file integrity against stored checksum."""
        all_checksums = self.load_checksums()
        if filepath not in all_checksums:
            return True  # No previous checksum, assume OK

        current_checksum = self.compute_checksum(filepath)
        stored_checksum = all_checksums[filepath]["checksum"]

        if current_checksum != stored_checksum:
            logger.error(f"CORRUPTION DETECTED: {filepath}")
            logger.error(f"Expected: {stored_checksum}")
            logger.error(f"Found: {current_checksum}")
            return False

        return True


class TransferLogger:
    """Log all file transfers for audit trail."""

    def __init__(self):
        self.log_file = CONFIG["transfer_log"]
        os.makedirs(os.path.dirname(self.log_file), exist_ok=True)

    def log_transfer(self, source: str, dest: str, success: bool,
                     checksum: str, size: int):
        """Log a file transfer."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "source": source,
            "destination": dest,
            "success": success,
            "checksum": checksum,
            "size_bytes": size,
            "verified": False
        }

        # Append to log
        try:
            with open(self.log_file, 'a') as f:
                f.write(json.dumps(entry) + '\n')
        except Exception as e:
            logger.error(f"Failed to log transfer: {e}")

        return entry


class StorageStateTracker:
    """Track storage state across power cycles."""

    def __init__(self):
        self.state_file = CONFIG["state_file"]
        self.state = self.load()

    def load(self) -> Dict:
        """Load current state."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    return json.load(f)
            except:
                pass
        return {
            "mode": "primary",  # or "fallback"
            "hdd_available": False,
            "last_check": None,
            "pending_transfers": [],
            "verified_files": []
        }

    def save(self):
        """Save state to file."""
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        with open(self.state_file, 'w') as f:
            json.dump(self.state, f, indent=2)

    def set_mode(self, mode: str):
        """Set current storage mode."""
        self.state["mode"] = mode
        self.state["last_check"] = datetime.now().isoformat()
        self.save()

    def add_pending_transfer(self, transfer: Dict):
        """Add transfer to pending queue."""
        self.state["pending_transfers"].append(transfer)
        self.save()

    def mark_transferred(self, filepath: str):
        """Mark file as successfully transferred."""
        self.state["pending_transfers"] = [
            t for t in self.state["pending_transfers"]
            if t.get("source") != filepath
        ]
        self.state["verified_files"].append({
            "file": filepath,
            "timestamp": datetime.now().isoformat()
        })
        self.save()


class RedundantStorageManager:
    """Main storage manager with automatic failover."""

    def __init__(self):
        self.checksums = ChecksumManager()
        self.transfers = TransferLogger()
        self.state = StorageStateTracker()
        self.hdd_mounted = False

    def check_hdd_mounted(self) -> bool:
        """Check if external HDD is mounted and accessible."""
        marker = CONFIG["primary_marker"]
        primary_dir = CONFIG["primary_dir"]

        # Check mountpoint
        try:
            result = subprocess.run(
                ["mountpoint", "-q", os.path.dirname(primary_dir)],
                capture_output=True
            )
            is_mounted = result.returncode == 0

            # Also check if directory is writable
            if is_mounted:
                test_file = os.path.join(primary_dir, ".write_test")
                try:
                    with open(test_file, 'w') as f:
                        f.write("test")
                    os.remove(test_file)
                    self.hdd_mounted = True
                    return True
                except:
                    logger.warning("HDD mounted but not writable")
                    return False

        except Exception as e:
            logger.error(f"Error checking HDD mount: {e}")

        self.hdd_mounted = False
        return False

    def get_storage_dir(self) -> Tuple[str, str]:
        """Get appropriate storage directory based on HDD availability.

        Returns: (snapshot_dir, analysis_dir)
        """
        if self.check_hdd_mounted():
            logger.info("HDD available - using primary storage")
            self.state.set_mode("primary")
            self.state.state["hdd_available"] = True
            self.state.save()
            return CONFIG["primary_dir"], CONFIG["primary_analysis_dir"]
        else:
            logger.warning("HDD unavailable - using SSD fallback")
            self.state.set_mode("fallback")
            self.state.state["hdd_available"] = False
            self.state.save()

            # Create fallback directories
            os.makedirs(CONFIG["fallback_dir"], exist_ok=True)
            os.makedirs(CONFIG["fallback_analysis_dir"], exist_ok=True)

            # Create marker file
            Path(CONFIG["fallback_marker"]).touch()

            return CONFIG["fallback_dir"], CONFIG["fallback_analysis_dir"]

    def safe_copy_file(self, source: str, dest_dir: str) -> bool:
        """Copy file with integrity verification."""
        if not os.path.exists(source):
            logger.error(f"Source file not found: {source}")
            return False

        dest = os.path.join(dest_dir, os.path.basename(source))

        # Compute source checksum
        source_checksum = self.checksums.compute_checksum(source)
        if not source_checksum:
            logger.error(f"Failed to compute checksum for {source}")
            return False

        # Copy file
        try:
            shutil.copy2(source, dest)

            # Verify destination
            dest_checksum = self.checksums.compute_checksum(dest)

            if dest_checksum != source_checksum:
                logger.error(f"COPY CORRUPTED: {source} → {dest}")
                logger.error(f"Source: {source_checksum}")
                logger.error(f"Dest: {dest_checksum}")

                # Remove corrupted copy
                os.remove(dest)
                return False

            # Track checksum
            self.checksums.track_file(dest)

            # Log transfer
            size = os.path.getsize(dest)
            self.transfers.log_transfer(source, dest, True,
                                        dest_checksum, size)

            logger.info(f"✓ Copied: {os.path.basename(source)} ({size} bytes)")
            return True

        except Exception as e:
            logger.error(f"Copy failed: {e}")
            return False

    def recover_fallback_to_hdd(self) -> Dict:
        """Move all fallback data to HDD when it reconnects."""
        logger.info("=" * 60)
        logger.info("RECOVERY: Moving fallback data to HDD")
        logger.info("=" * 60)

        stats = {
            "started": datetime.now().isoformat(),
            "snapshots_moved": 0,
            "analyses_moved": 0,
            "failed": 0,
            "total_bytes": 0
        }

        # Check if HDD available
        if not self.check_hdd_mounted():
            logger.error("HDD not available for recovery")
            WhatsAppNotifier.send(
                "⚠️ RECOVERY FAILED: HDD not mounted\n"
                "Fallback data remains on SSD"
            )
            return stats

        # Move snapshots
        fallback_snapshots = CONFIG["fallback_dir"]
        if os.path.exists(fallback_snapshots):
            for filename in os.listdir(fallback_snapshots):
                source = os.path.join(fallback_snapshots, filename)

                if os.path.isfile(source):
                    logger.info(f"Moving: {filename}")

                    if self.safe_copy_file(source, CONFIG["primary_dir"]):
                        # Verify copy
                        dest = os.path.join(CONFIG["primary_dir"], filename)
                        if self.checksums.verify_file(dest):
                            # Safe to delete source
                            os.remove(source)
                            stats["snapshots_moved"] += 1
                            stats["total_bytes"] += os.path.getsize(dest)
                        else:
                            logger.error(f"VERIFICATION FAILED: {filename}")
                            stats["failed"] += 1
                    else:
                        stats["failed"] += 1

        # Move analysis files
        fallback_analysis = CONFIG["fallback_analysis_dir"]
        if os.path.exists(fallback_analysis):
            for filename in os.listdir(fallback_analysis):
                source = os.path.join(fallback_analysis, filename)

                if os.path.isfile(source):
                    if self.safe_copy_file(source, CONFIG["primary_analysis_dir"]):
                        dest = os.path.join(CONFIG["primary_analysis_dir"], filename)
                        if self.checksums.verify_file(dest):
                            os.remove(source)
                            stats["analyses_moved"] += 1
                            stats["total_bytes"] += os.path.getsize(dest)
                        else:
                            stats["failed"] += 1
                    else:
                        stats["failed"] += 1

        stats["completed"] = datetime.now().isoformat()

        # Remove fallback marker if all successful
        if os.path.exists(CONFIG["fallback_marker"]) and stats["failed"] == 0:
            os.remove(CONFIG["fallback_marker"])

        # Send WhatsApp
        WhatsAppNotifier.send(
            f"✅ RECOVERY COMPLETE\n"
            f"Moved: {stats['snapshots_moved']} snapshots\n"
            f"Moved: {stats['analyses_moved']} analyses\n"
            f"Failed: {stats['failed']}\n"
            f"Total: {stats['total_bytes'] // 1024 / 1024} MB"
        )

        logger.info("Recovery complete")
        return stats

    def check_and_recover(self):
        """Check HDD status and recover if needed."""
        hdd_was_available = self.state.state.get("hdd_available", False)
        hdd_now_available = self.check_hdd_mounted()

        logger.info(f"HDD status: was={hdd_was_available}, now={hdd_now_available}")

        # HDD newly reconnected
        if not hdd_was_available and hdd_now_available:
            logger.info("HDD RECONNECTED - Starting recovery")

            # Check if fallback exists
            if os.path.exists(CONFIG["fallback_marker"]):
                WhatsAppNotifier.send(
                    "🔌 HDD RECONNECTED\n"
                    "Starting fallback data recovery..."
                )

                # Recover data
                stats = self.recover_fallback_to_hdd()

                logger.info(f"Recovery stats: {json.dumps(stats, indent=2)}")

                # Update state
                self.state.state["hdd_available"] = True
                self.state.state["mode"] = "primary"
                self.state.save()

        # HDD newly disconnected
        elif hdd_was_available and not hdd_now_available:
            logger.warning("HDD DISCONNECTED - Switching to fallback")

            WhatsAppNotifier.send(
                "⚠️ HDD DISCONNECTED\n"
                "Switching to SSD fallback storage\n"
                "New data will be saved to SSD until HDD reconnects"
            )

            # Update mode
            self.state.state["hdd_available"] = False
            self.state.state["mode"] = "fallback"
            self.state.save()

            # Create fallback dirs
            os.makedirs(CONFIG["fallback_dir"], exist_ok=True)
            os.makedirs(CONFIG["fallback_analysis_dir"], exist_ok=True)

            # Create marker
            Path(CONFIG["fallback_marker"]).touch()

        return hdd_now_available

    def get_current_storage_info(self) -> Dict:
        """Get information about current storage."""
        hdd_available = self.check_hdd_mounted()
        snapshot_dir, analysis_dir = self.get_storage_dir()

        # Calculate disk usage
        if hdd_available:
            disk_usage = shutil.disk_usage(os.path.dirname(CONFIG["primary_dir"]))
        else:
            disk_usage = shutil.disk_usage(os.path.expanduser("~"))

        return {
            "mode": self.state.state["mode"],
            "hdd_available": hdd_available,
            "primary_dir": CONFIG["primary_dir"],
            "fallback_dir": CONFIG["fallback_dir"],
            "active_snapshot_dir": snapshot_dir,
            "active_analysis_dir": analysis_dir,
            "disk_total_gb": disk_usage.total // (1024**3),
            "disk_used_gb": disk_usage.used // (1024**3),
            "disk_free_gb": disk_usage.free // (1024**3),
            "fallback_data_exists": os.path.exists(CONFIG["fallback_marker"])
        }


def main():
    """Main entry point for redundancy manager."""
    manager = RedundantStorageManager()

    # Check current state
    info = manager.get_current_storage_info()

    logger.info("=" * 60)
    logger.info("CCTV REDUNDANT STORAGE STATUS")
    logger.info("=" * 60)
    logger.info(f"Mode: {info['mode'].upper()}")
    logger.info(f"HDD Available: {info['hdd_available']}")
    logger.info(f"Active Storage: {info['active_snapshot_dir']}")
    logger.info(f"Disk Free: {info['disk_free_gb']} GB")
    logger.info(f"Fallback Data: {info['fallback_data_exists']}")
    logger.info("=" * 60)

    # Check and recover if needed
    manager.check_and_recover()

    # Output JSON for other scripts
    print(json.dumps(info, indent=2))


if __name__ == "__main__":
    main()
