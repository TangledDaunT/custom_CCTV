#!/usr/bin/env python3
"""
CCTV Snapshot Vision Analyzer
Analyzes motion detection snapshots using local vision models (Ollama)
Generates detailed JSON analysis for AI training.
"""

import os
import sys
import json
import time
import logging
import base64
import hashlib
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import threading
import queue

# Configuration
CONFIG = {
    "snapshot_dir": os.environ.get("CCTV_SNAPSHOT_DIR", "/media/shreyansh/LINUX_SHARE/cctv_snapshots"),
    "analysis_dir": os.environ.get("CCTV_ANALYSIS_DIR", "/media/shreyansh/LINUX_SHARE/cctv_analysis"),
    "log_file": os.environ.get("CCTV_VISION_LOG", "/var/log/cctv/vision_analyzer.log"),
    "state_file": os.environ.get("CCTV_VISION_STATE", "/var/lib/cctv/vision_state.json"),
    "ollama_model": os.environ.get("CCTV_VISION_MODEL", "llava:7b"),
    "watch_interval": float(os.environ.get("CCTV_VISION_INTERVAL", "5.0")),
    "max_concurrent": int(os.environ.get("CCTV_VISION_MAX_CONCURRENT", "1")),  # Process one at a time
    "enable_face_recognition": os.environ.get("CCTV_ENABLE_FACE_RECOGNITION", "false").lower() == "true",
}

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler(CONFIG["log_file"]) if os.path.exists(os.path.dirname(CONFIG["log_file"])) else logging.StreamHandler(),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("vision_analyzer")

# Ensure directories exist
os.makedirs(CONFIG["analysis_dir"], exist_ok=True)
os.makedirs(os.path.dirname(CONFIG["log_file"]), exist_ok=True)
os.makedirs(os.path.dirname(CONFIG["state_file"]), exist_ok=True)


class ProcessingState:
    """Track processed files to avoid duplicates."""

    def __init__(self, state_file: str):
        self.state_file = state_file
        self.state = self._load()
        self.lock = threading.Lock()

    def _load(self) -> Dict:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load state: {e}")
        return {"processed_files": {}}

    def save(self):
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def is_processed(self, filepath: str) -> bool:
        """Check if file has been processed (by hash)."""
        file_hash = self._compute_hash(filepath)
        with self.lock:
            return file_hash in self.state["processed_files"]

    def mark_processed(self, filepath: str, analysis_file: str):
        """Mark file as processed."""
        file_hash = self._compute_hash(filepath)
        with self.lock:
            self.state["processed_files"][file_hash] = {
                "original_file": filepath,
                "analysis_file": analysis_file,
                "timestamp": datetime.now().isoformat(),
                "file_size": os.path.getsize(filepath)
            }
            self.save()

    def _compute_hash(self, filepath: str) -> str:
        """Compute MD5 hash of file for deduplication."""
        try:
            hasher = hashlib.md5()
            with open(filepath, 'rb') as f:
                # Hash first 1MB for speed
                buf = f.read(1024 * 1024)
                hasher.update(buf)
            return hasher.hexdigest()
        except Exception as e:
            logger.error(f"Hash computation failed: {e}")
            return filepath  # Fallback to path


class VisionAnalyzer:
    """Analyze images using local Ollama vision models."""

    def __init__(self):
        self.state = ProcessingState(CONFIG["state_file"])
        self.processing_queue = queue.Queue()
        self.workers = []
        self.running = False

    def analyze_image(self, image_path: str) -> Optional[Dict]:
        """Analyze a single image using Ollama vision model."""
        try:
            logger.info(f"Analyzing: {image_path}")

            # Encode image to base64
            with open(image_path, 'rb') as f:
                image_data = base64.b64encode(f.read()).decode('utf-8')

            # Craft detailed prompt for JSON analysis
            prompt = f"""Analyze this CCTV security camera image and provide a detailed JSON analysis.

Return ONLY valid JSON (no markdown, no explanation) with this exact structure:
{{
  "timestamp": "{datetime.now().isoformat()}",
  "time_of_day": "day/evening/night",
  "lighting": "good/moderate/poor",
  "people_detected": {{
    "count": 0,
    "persons": []
  }},
  "objects_detected": [],
  "scene_description": "",
  "security_alerts": [],
  "motion_likely": true/false,
  "confidence_score": 0.0
}}

For each person detected, include:
- "person_id": "Person 1", "Person 2", etc.
- "position": "left/center/right/far"
- "activity": "walking/standing/sitting/unknown"
- "appearance": brief description
- "facing_camera": true/false

For each object, include:
- "object": object name
- "type": "vehicle/person/animal/object"
- "position": position description
- "confidence": 0.0-1.0

Provide realistic analysis suitable for AI training."""

            # Call Ollama with vision model
            result = subprocess.run(
                ["ollama", "run", CONFIG["ollama_model"]],
                input=json.dumps({
                    "prompt": prompt,
                    "images": [image_data]
                }),
                capture_output=True,
                text=True,
                timeout=60
            )

            if result.returncode != 0:
                logger.error(f"Ollama failed: {result.stderr}")
                return None

            # Parse JSON response
            response_text = result.stdout.strip()

            # Extract JSON from response (remove any markdown code blocks)
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0].strip()

            analysis = json.loads(response_text)

            # Add metadata
            analysis["source_file"] = image_path
            analysis["analyzed_at"] = datetime.now().isoformat()
            analysis["model_used"] = CONFIG["ollama_model"]
            analysis["file_size_bytes"] = os.path.getsize(image_path)

            return analysis

        except subprocess.TimeoutExpired:
            logger.error(f"Vision analysis timeout for {image_path}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error: {e}\nResponse: {response_text[:200]}")
            return None
        except Exception as e:
            logger.error(f"Analysis failed: {e}")
            return None

    def save_analysis(self, analysis: Dict, original_path: str) -> str:
        """Save analysis to JSON file."""
        # Generate analysis filename
        original_name = Path(original_path).stem
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        analysis_file = os.path.join(
            CONFIG["analysis_dir"],
            f"{original_name}_analysis_{timestamp}.json"
        )

        # Save JSON with formatting
        with open(analysis_file, 'w') as f:
            json.dump(analysis, f, indent=2, default=str)

        logger.info(f"Saved analysis: {analysis_file}")
        return analysis_file

    def process_file(self, image_path: str):
        """Process a single image file."""
        if not os.path.exists(image_path):
            return

        # Check if already processed
        if self.state.is_processed(image_path):
            logger.debug(f"Skipping (already processed): {image_path}")
            return

        # Analyze
        analysis = self.analyze_image(image_path)
        if not analysis:
            return

        # Save analysis
        analysis_file = self.save_analysis(analysis, image_path)

        # Mark as processed
        self.state.mark_processed(image_path, analysis_file)

        # Log summary
        people_count = analysis.get("people_detected", {}).get("count", 0)
        objects_count = len(analysis.get("objects_detected", []))
        logger.info(
            f"✅ Analysis complete: {people_count} people, {objects_count} objects - "
            f"{analysis.get('scene_description', 'No description')[:100]}"
        )

    def worker_loop(self):
        """Worker thread for processing images."""
        while self.running:
            try:
                # Get next image from queue (with timeout)
                image_path = self.processing_queue.get(timeout=1.0)
                self.process_file(image_path)
                self.processing_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Worker error: {e}")

    def watch_directory(self):
        """Watch for new snapshots and process them."""
        logger.info(f"Watching directory: {CONFIG['snapshot_dir']}")

        # Supported image extensions
        extensions = {'.jpg', '.jpeg', '.png'}

        while self.running:
            try:
                # Find all image files
                snapshot_dir = Path(CONFIG["snapshot_dir"])
                if not snapshot_dir.exists():
                    time.sleep(CONFIG["watch_interval"])
                    continue

                for ext in extensions:
                    for image_file in snapshot_dir.rglob(f'*{ext}'):
                        image_path = str(image_file)

                        # Skip if already processed
                        if self.state.is_processed(image_path):
                            continue

                        # Add to processing queue
                        if not self.processing_queue.full():
                            self.processing_queue.put(image_path)
                            logger.info(f"Queued: {image_path}")

                # Sleep before next check
                time.sleep(CONFIG["watch_interval"])

            except Exception as e:
                logger.error(f"Watch error: {e}")
                time.sleep(CONFIG["watch_interval"])

    def start(self):
        """Start the analyzer."""
        logger.info("=== Starting CCTV Vision Analyzer ===")
        logger.info(f"Model: {CONFIG['ollama_model']}")
        logger.info(f"Snapshot dir: {CONFIG['snapshot_dir']}")
        logger.info(f"Analysis dir: {CONFIG['analysis_dir']}")

        self.running = True

        # Start worker threads
        for i in range(CONFIG["max_concurrent"]):
            worker = threading.Thread(target=self.worker_loop, daemon=True, name=f"worker-{i}")
            worker.start()
            self.workers.append(worker)

        # Start watching
        self.watch_directory()

    def stop(self):
        """Stop the analyzer."""
        logger.info("Stopping analyzer...")
        self.running = False

        # Wait for workers to finish
        for worker in self.workers:
            worker.join(timeout=5)


def check_resources():
    """Check system resources before starting."""
    try:
        # Check memory
        with open('/proc/meminfo', 'r') as f:
            meminfo = f.read()

        total_mb = int([l for l in meminfo.split('\n') if 'MemTotal:' in l][0].split()[1]) // 1024
        available_mb = int([l for l in meminfo.split('\n') if 'MemAvailable:' in l][0].split()[1]) // 1024

        logger.info(f"Memory: {available_mb}MB / {total_mb}MB available")

        if available_mb < 1500:
            logger.warning("⚠️ Low memory (< 1.5GB available)")
            logger.warning("Vision analysis may be slow or unstable")

        # Check Ollama
        result = subprocess.run(["ollama", "list"], capture_output=True, text=True)
        if result.returncode == 0:
            models = [line.split()[0] for line in result.stdout.strip().split('\n')[1:] if line]
            logger.info(f"Available Ollama models: {models}")

            if CONFIG["ollama_model"] not in models:
                logger.warning(f"⚠️ Model {CONFIG['ollama_model']} not found")
                logger.info("Available models: " + ", ".join(models))
                logger.info(f"Pull model with: ollama pull {CONFIG['ollama_model']}")
        else:
            logger.error("Ollama not responding")
            return False

        return True

    except Exception as e:
        logger.error(f"Resource check failed: {e}")
        return False


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="CCTV Vision Analyzer")
    parser.add_argument("--check-resources", action="store_true", help="Check system resources")
    parser.add_argument("--once", type=str, help="Analyze single image and exit")
    parser.add_argument("--model", type=str, default="llava:7b", help="Ollama vision model")
    args = parser.parse_args()

    if args.model:
        CONFIG["ollama_model"] = args.model

    if args.check_resources:
        check_resources()
        return

    if args.once:
        analyzer = VisionAnalyzer()
        analysis = analyzer.analyze_image(args.once)
        if analysis:
            print(json.dumps(analysis, indent=2))
        return

    # Check resources before starting
    if not check_resources():
        logger.error("Resource check failed. Exiting.")
        sys.exit(1)

    # Start analyzer
    analyzer = VisionAnalyzer()
    try:
        analyzer.start()
    except KeyboardInterrupt:
        logger.info("\nShutting down...")
        analyzer.stop()


if __name__ == "__main__":
    main()
