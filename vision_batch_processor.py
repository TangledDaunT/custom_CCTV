#!/usr/bin/env python3
"""
CCTV Vision Batch Processor
Runs daily from 11:00 AM to 12:30 PM
Pauses transcriber during processing, sends WhatsApp alerts when done.
"""

import os
import sys
import json
import time
import signal
import logging
import base64
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

# Configuration
CONFIG = {
    "snapshot_dir": "/media/shreyansh/LINUX_SHARE/cctv_snapshots",
    "analysis_dir": "/media/shreyansh/LINUX_SHARE/cctv_analysis",
    "log_file": "/var/log/cctv/vision_batch.log",
    "state_file": "/var/lib/cctv/vision_processed.json",
    "transcriber_pid": 2337278,  # daemon_v2.py process
    "transcriber_name": "daemon_v2.py",
    "vision_model": "minicpm-v",  # Better model since transcriber paused
    "whatsapp_number": "917754008079",  # +91 77754008079
    "wacli_path": "/usr/local/bin/wacli",
    "max_processing_time": 90 * 60,  # 90 minutes max
    "processed_log": "/var/log/cctv/vision_processed_images.log",
}

# Setup logging
os.makedirs(os.path.dirname(CONFIG["log_file"]), exist_ok=True)
os.makedirs(CONFIG["analysis_dir"], exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(CONFIG["log_file"]),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class TranscriberManager:
    """Manage transcriber process pause/resume."""

    def __init__(self, pid: int, name: str):
        self.pid = pid
        self.name = name
        self.was_paused = False

    def find_transcriber_pid(self) -> Optional[int]:
        """Find transcriber PID by name if not provided."""
        try:
            result = subprocess.run(
                ["pgrep", "-f", self.name],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                pids = result.stdout.strip().split('\n')
                if pids:
                    return int(pids[0])
        except Exception as e:
            logger.error(f"Failed to find transcriber PID: {e}")
        return None

    def pause(self) -> bool:
        """Pause transcriber with SIGSTOP."""
        # Find PID if not set
        if not self.pid:
            self.pid = self.find_transcriber_pid()
            if not self.pid:
                logger.error("Cannot find transcriber process")
                return False

        try:
            # Verify process exists
            os.kill(self.pid, 0)  # Check if process exists

            # Send SIGSTOP to pause
            os.kill(self.pid, signal.SIGSTOP)
            self.was_paused = True

            logger.info(f"✓ Transcriber PAUSED (PID: {self.pid}, SIGSTOP sent)")
            time.sleep(2)  # Wait for process to fully pause

            # Verify it's paused
            result = subprocess.run(
                ["ps", "-p", str(self.pid), "-o", "stat="],
                capture_output=True, text=True
            )
            if "T" in result.stdout:  # T = stopped/traced
                logger.info(f"✓ Confirmed: Transcriber is PAUSED")
                return True
            else:
                logger.warning(f"⚠ Pause sent but process state: {result.stdout.strip()}")
                return True

        except ProcessLookupError:
            logger.error(f"Process {self.pid} not found")
            return False
        except PermissionError:
            logger.error(f"Permission denied to pause PID {self.pid}")
            return False
        except Exception as e:
            logger.error(f"Failed to pause transcriber: {e}")
            return False

    def resume(self) -> bool:
        """Resume transcriber with SIGCONT."""
        if not self.was_paused:
            logger.info("Transcriber wasn't paused, skipping resume")
            return True

        try:
            # Send SIGCONT to resume
            os.kill(self.pid, signal.SIGCONT)

            logger.info(f"✓ Transcriber RESUMED (PID: {self.pid}, SIGCONT sent)")
            time.sleep(2)  # Wait for process to resume

            # Verify it's running
            result = subprocess.run(
                ["ps", "-p", str(self.pid), "-o", "stat="],
                capture_output=True, text=True
            )
            stat = result.stdout.strip()
            if "T" not in stat:  # Not in stopped state
                logger.info(f"✓ Confirmed: Transcriber is RUNNING (state: {stat})")
                self.was_paused = False
                return True
            else:
                logger.warning(f"⚠ Resume sent but still stopped: {stat}")
                return False

        except ProcessLookupError:
            logger.error(f"Process {self.pid} not found during resume")
            return False
        except Exception as e:
            logger.error(f"Failed to resume transcriber: {e}")
            return False


class WhatsAppNotifier:
    """Send WhatsApp alerts via wacli."""

    @staticmethod
    def send_message(message: str, number: str = None) -> bool:
        """Send WhatsApp text message."""
        number = number or CONFIG["whatsapp_number"]
        try:
            result = subprocess.run(
                [CONFIG["wacli_path"], "send", "text",
                 "--to", number, "--message", message],
                timeout=15, capture_output=True, text=True
            )

            if result.returncode == 0:
                logger.info(f"✓ WhatsApp message sent to {number}")
                return True
            else:
                logger.error(f"WhatsApp send failed: {result.stderr}")
                return False
        except subprocess.TimeoutExpired:
            logger.error("WhatsApp send timeout")
            return False
        except Exception as e:
            logger.error(f"WhatsApp send error: {e}")
            return False


class VisionBatchProcessor:
    """Process pending images with vision analysis."""

    def __init__(self):
        self.processed_files = self._load_processed_state()
        self.analysis_results = []

    def _load_processed_state(self) -> Dict:
        """Load already processed files."""
        if os.path.exists(CONFIG["state_file"]):
            try:
                with open(CONFIG["state_file"], 'r') as f:
                    return json.load(f)
            except:
                pass
        return {"processed": {}}

    def _save_processed_state(self):
        """Save processed files state."""
        try:
            os.makedirs(os.path.dirname(CONFIG["state_file"]), exist_ok=True)
            with open(CONFIG["state_file"], 'w') as f:
                json.dump(self.processed_files, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def find_pending_images(self) -> List[str]:
        """Find all images not yet processed."""
        pending = []
        snapshot_dir = Path(CONFIG["snapshot_dir"])

        for ext in ['*.jpg', '*.jpeg', '*.png']:
            for img_path in snapshot_dir.rglob(ext):
                img_str = str(img_path)
                if img_str not in self.processed_files["processed"]:
                    pending.append(img_str)

        logger.info(f"Found {len(pending)} pending images to process")
        return pending

    def analyze_image(self, image_path: str) -> Optional[Dict]:
        """Analyze single image with Ollama."""
        try:
            logger.info(f"Processing: {os.path.basename(image_path)}")

            # Read and encode image
            with open(image_path, 'rb') as f:
                image_data = base64.b64encode(f.read()).decode('utf-8')

            # Create detailed prompt
            prompt = f"""Analyze this CCTV security camera snapshot and provide detailed JSON.

Return ONLY valid JSON with this exact structure:
{{
  "timestamp": "{datetime.now().isoformat()}",
  "time_of_day": "morning/afternoon/evening/night",
  "lighting": "good/moderate/poor",
  "people_detected": {{
    "count": 0,
    "persons": []
  }},
  "objects_detected": [],
  "scene_description": "",
  "security_alerts": [],
  "confidence_score": 0.0
}}

For each person in "persons" array:
- "person_id": "Person 1", "Person 2", etc.
- "position": "left/center/right/far"
- "activity": "walking/standing/sitting/running/unknown"
- "appearance": brief description of clothing, gender, etc.
- "facing_camera": true/false

For each object in "objects_detected":
- "object": name of object
- "type": "vehicle/person/animal/object"
- "position": position description
- "confidence": 0.0-1.0

Be thorough and accurate for AI training purposes."""

            # Run Ollama
            result = subprocess.run(
                ["ollama", "run", CONFIG["vision_model"]],
                input=json.dumps({"prompt": prompt, "images": [image_data]}),
                capture_output=True, text=True, timeout=30
            )

            if result.returncode != 0:
                logger.error(f"Ollama failed: {result.stderr}")
                return None

            # Parse JSON from response
            response_text = result.stdout.strip()

            # Extract JSON if wrapped in markdown
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0].strip()

            analysis = json.loads(response_text)

            # Add metadata
            analysis["source_file"] = image_path
            analysis["analyzed_at"] = datetime.now().isoformat()
            analysis["model_used"] = CONFIG["vision_model"]
            analysis["file_size_bytes"] = os.path.getsize(image_path)

            return analysis

        except subprocess.TimeoutExpired:
            logger.error(f"Vision analysis timeout for {image_path}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error for {image_path}: {e}")
            return None
        except Exception as e:
            logger.error(f"Analysis failed for {image_path}: {e}")
            return None

    def save_analysis(self, analysis: Dict, image_path: str) -> str:
        """Save analysis to JSON file."""
        original_name = Path(image_path).stem
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{original_name}_analysis_{timestamp}.json"
        filepath = os.path.join(CONFIG["analysis_dir"], filename)

        with open(filepath, 'w') as f:
            json.dump(analysis, f, indent=2, default=str)

        logger.info(f"✓ Saved: {filename}")
        return filepath

    def log_processed_image(self, image_path: str, analysis_file: str):
        """Log processed image to daily log."""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "image": image_path,
            "analysis": analysis_file,
            "people_count": 0,
            "objects_count": 0
        }

        # Load existing analysis for counts
        try:
            with open(analysis_file, 'r') as f:
                data = json.load(f)
                log_entry["people_count"] = data.get("people_detected", {}).get("count", 0)
                log_entry["objects_count"] = len(data.get("objects_detected", []))
        except:
            pass

        # Append to log
        os.makedirs(os.path.dirname(CONFIG["processed_log"]), exist_ok=True)
        with open(CONFIG["processed_log"], 'a') as f:
            f.write(json.dumps(log_entry) + '\n')

    def process_batch(self) -> Dict:
        """Process all pending images."""
        start_time = time.time()
        stats = {
            "start_time": datetime.now().isoformat(),
            "total_found": 0,
            "processed": 0,
            "failed": 0,
            "total_people": 0,
            "total_objects": 0,
            "duration_seconds": 0,
            "images": []
        }

        # Find pending images
        pending = self.find_pending_images()
        stats["total_found"] = len(pending)

        if not pending:
            logger.info("No pending images to process")
            return stats

        logger.info(f"=== Starting batch processing of {len(pending)} images ===")

        # Process each image
        for i, image_path in enumerate(pending, 1):
            # Check time limit
            elapsed = time.time() - start_time
            if elapsed > CONFIG["max_processing_time"]:
                logger.warning(f"⚠ Time limit reached ({elapsed:.0f}s), stopping batch")
                break

            logger.info(f"[{i}/{len(pending)}] Processing: {os.path.basename(image_path)}")

            # Analyze
            analysis = self.analyze_image(image_path)

            if analysis:
                # Save analysis
                analysis_file = self.save_analysis(analysis, image_path)

                # Mark as processed
                self.processed_files["processed"][image_path] = {
                    "analyzed_at": datetime.now().isoformat(),
                    "analysis_file": analysis_file
                }
                self._save_processed_state()

                # Log it
                self.log_processed_image(image_path, analysis_file)

                # Update stats
                people = analysis.get("people_detected", {}).get("count", 0)
                objects = len(analysis.get("objects_detected", []))

                stats["processed"] += 1
                stats["total_people"] += people
                stats["total_objects"] += objects
                stats["images"].append({
                    "image": os.path.basename(image_path),
                    "people": people,
                    "objects": objects
                })

                logger.info(f"  ✓ Found: {people} people, {objects} objects")
            else:
                stats["failed"] += 1
                logger.error(f"  ✗ Failed to process")

        stats["duration_seconds"] = round(time.time() - start_time, 2)
        stats["end_time"] = datetime.now().isoformat()

        return stats


def main():
    """Main batch processing workflow."""
    logger.info("=" * 60)
    logger.info("CCTV VISION BATCH PROCESSOR")
    logger.info(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    # Initialize components
    transcriber = TranscriberManager(
        CONFIG["transcriber_pid"],
        CONFIG["transcriber_name"]
    )

    wa = WhatsAppNotifier()
    processor = VisionBatchProcessor()

    # Step 1: Pause transcriber
    logger.info("")
    logger.info("Step 1: Pausing transcriber...")
    if not transcriber.pause():
        logger.error("✗ Failed to pause transcriber - aborting")
        WhatsAppNotifier.send_message(
            "❌ Vision batch failed: Could not pause transcriber",
            CONFIG["whatsapp_number"]
        )
        sys.exit(1)

    # Notify start
    WhatsAppNotifier.send_message(
        f"🔍Vision batch started\n"
        f"Time: 11:00 AM\n"
        f"Transcriber: PAUSED\n"
        f"Model: {CONFIG['vision_model']}",
        CONFIG["whatsapp_number"]
    )

    try:
        # Step 2: Process images
        logger.info("")
        logger.info("Step 2: Processing batch...")
        stats = processor.process_batch()

        # Step 3: Generate summary
        logger.info("")
        logger.info("Step 3: Summary...")
        logger.info(f"  Total found: {stats['total_found']}")
        logger.info(f"  Processed: {stats['processed']}")
        logger.info(f"  Failed: {stats['failed']}")
        logger.info(f"  Total people detected: {stats['total_people']}")
        logger.info(f"  Total objects detected: {stats['total_objects']}")
        logger.info(f"  Duration: {stats['duration_seconds']}s")

        # Send completion WhatsApp
        message = (
            f"✅ Vision batch complete!\n"
            f"Images processed: {stats['processed']}\n"
            f"People detected: {stats['total_people']}\n"
            f"Objects found: {stats['total_objects']}\n"
            f"Duration: {stats['duration_seconds']:.0f}s\n"
            f"Time: {datetime.now().strftime('%H:%M')}"
        )
        WhatsAppNotifier.send_message(message, CONFIG["whatsapp_number"])

    except Exception as e:
        logger.error(f"Batch processing error: {e}")
        WhatsAppNotifier.send_message(
            f"❌ Vision batch error: {str(e)}",
            CONFIG["whatsapp_number"]
        )

    finally:
        # Step 4: Resume transcriber (always!)
        logger.info("")
        logger.info("Step 4: Resuming transcriber...")
        if transcriber.resume():
            WhatsAppNotifier.send_message(
                "▶️ Transcriber RESUMED",
                CONFIG["whatsapp_number"]
            )
        else:
            logger.error("✗ Failed to resume transcriber!")
            WhatsAppNotifier.send_message(
                "⚠️ WARNING: Transcriber resume failed!",
                CONFIG["whatsapp_number"]
            )

    logger.info("")
    logger.info("=" * 60)
    logger.info("BATCH PROCESSING COMPLETE")
    logger.info(f"Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
