import cv2
import numpy as np
import time
import threading
import logging
from datetime import datetime
from collections import deque

logger = logging.getLogger(__name__)


class MotionDetector:
    """
    Lightweight, efficient motion detector using background subtraction + contour analysis.

    Algorithm:
      - MOG2 background subtractor (adaptive, handles lighting changes)
      - Morphological cleanup to kill noise
      - Contour filtering by area to ignore tiny false positives
      - Cooldown + persistence logic to avoid alert spam
      - Per-frame scoring with rolling average for stable detection
    """

    def __init__(
        self,
        min_area=1500,           # Minimum contour area (px²) to count as motion
        threshold=25,            # Pixel diff threshold for fg/bg separation
        blur_size=21,            # Gaussian blur kernel (must be odd) — kills sensor noise
        dilate_iterations=2,     # Dilation passes to fill contour gaps
        cooldown_seconds=10,     # Minimum seconds between two motion events
        motion_frames_trigger=3, # Consecutive frames with motion before firing event
        history=500,             # MOG2 background history frames
        var_threshold=40,        # MOG2 variance threshold — lower = more sensitive
        avg_score_threshold=0.01, # Average motion score threshold (fraction of frame)
        avg_window=5,             # Number of recent scores to average for stability
        border_ignore_px=8,       # Ignore contours touching image border within px
    ):
        self.min_area = min_area
        self.threshold = threshold
        self.blur_size = blur_size
        self.dilate_iterations = dilate_iterations
        self.cooldown_seconds = cooldown_seconds
        self.motion_frames_trigger = motion_frames_trigger
        self._history = history
        self._var_threshold = var_threshold
        self.avg_score_threshold = avg_score_threshold
        self.avg_window = avg_window
        self.border_ignore_px = border_ignore_px

        # MOG2: best balance of speed vs accuracy for static cameras
        self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=history,
            varThreshold=var_threshold,
            detectShadows=False,   # shadows=False gives ~30% speedup, we don't need them
        )

        # Morphological kernel for noise cleanup
        self.kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

        # State
        self._lock = threading.Lock()
        self._motion_frame_count = 0
        self._last_event_time = 0.0
        self._last_motion_time = 0.0
        self._is_motion_active = False
        self._contours_cache = []

        # Callbacks
        self._on_motion_start = []
        self._on_motion_end = []

        # Stats
        self.total_events = 0
        self.frame_count = 0
        self._score_history = deque(maxlen=30)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_motion_start(self, fn):
        """Register callback: fn(timestamp, contours, frame)"""
        self._on_motion_start.append(fn)
        return fn

    def on_motion_end(self, fn):
        """Register callback: fn(timestamp, duration_seconds)"""
        self._on_motion_end.append(fn)
        return fn

    def process_frame(self, frame):
        """
        Feed a raw BGR frame. Returns (annotated_frame, motion_score, motion_active).
        Thread-safe — can be called from the capture thread directly.
        """
        self.frame_count += 1
        now = time.time()

        # --- Preprocessing ---
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (self.blur_size, self.blur_size), 0)

        # --- Background subtraction ---
        fg_mask = self.bg_subtractor.apply(blurred)

        # --- Threshold + morphological cleanup ---
        _, thresh = cv2.threshold(fg_mask, self.threshold, 255, cv2.THRESH_BINARY)
        cleaned = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, self.kernel)
        cleaned = cv2.dilate(cleaned, self.kernel, iterations=self.dilate_iterations)

        # --- Contour detection ---
        contours, _ = cv2.findContours(
            cleaned, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        # --- Filter by area ---
        # Filter out tiny contours and those touching borders (likely noise)
        significant = []
        for c in contours:
            area = cv2.contourArea(c)
            if area < self.min_area:
                continue
            x, y, w, h = cv2.boundingRect(c)
            H, W = frame.shape[:2]
            # ignore contours that touch frame border within tolerance
            if x <= self.border_ignore_px or y <= self.border_ignore_px or (x + w) >= (W - self.border_ignore_px) or (y + h) >= (H - self.border_ignore_px):
                continue
            significant.append(c)

        # --- Motion score: fraction of frame covered by significant motion ---
        h, w = frame.shape[:2]
        frame_area = h * w
        motion_area = sum(cv2.contourArea(c) for c in significant)
        score = min(motion_area / frame_area, 1.0)
        self._score_history.append(score)

        # Require both a significant contour and a recent averaged score above threshold
        recent_scores = list(self._score_history)[-self.avg_window:]
        avg_recent = (sum(recent_scores) / len(recent_scores)) if recent_scores else 0.0
        has_motion = (len(significant) > 0) and (avg_recent >= self.avg_score_threshold)

        # --- Annotate frame ---
        annotated = frame.copy()
        if significant:
            for c in significant:
                x, y, cw, ch = cv2.boundingRect(c)
                cv2.rectangle(annotated, (x, y), (x + cw, y + ch), (0, 255, 0), 2)
            # Overlay text
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cv2.putText(
                annotated, f"MOTION DETECTED  {ts}",
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2
            )
        else:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cv2.putText(
                annotated, ts,
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1
            )

        # --- State machine ---
        with self._lock:
            self._contours_cache = significant

            if has_motion:
                self._last_motion_time = now
                self._motion_frame_count += 1

                # Fire motion START event after N consecutive motion frames
                if (
                    self._motion_frame_count >= self.motion_frames_trigger
                    and not self._is_motion_active
                    and (now - self._last_event_time) >= self.cooldown_seconds
                ):
                    self._is_motion_active = True
                    self._last_event_time = now
                    self.total_events += 1
                    logger.info(f"Motion START (event #{self.total_events})")
                    # pass avg_recent in case callers want the score
                    self._fire(self._on_motion_start, now, significant, annotated, avg_recent)

            else:
                self._motion_frame_count = 0

                # Fire motion END event after 2s of no motion
                if self._is_motion_active and (now - self._last_motion_time) > 2.0:
                    duration = now - self._last_event_time
                    self._is_motion_active = False
                    logger.info(f"Motion END (lasted {duration:.1f}s)")
                    self._fire(self._on_motion_end, now, duration)

        return annotated, score, self._is_motion_active

    def get_stats(self):
        with self._lock:
            avg_score = (
                sum(self._score_history) / len(self._score_history)
                if self._score_history else 0.0
            )
            return {
                "total_events": self.total_events,
                "frames_processed": self.frame_count,
                "motion_active": self._is_motion_active,
                "avg_motion_score": round(avg_score * 100, 2),
                "contours_last_frame": len(self._contours_cache),
            }

    def reset_background(self):
        """Force the background model to reset — useful after scene changes."""
        self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=self._history, varThreshold=self._var_threshold, detectShadows=False
        )
        logger.info("Background model reset")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _fire(self, callbacks, *args):
        for fn in callbacks:
            try:
                t = threading.Thread(target=fn, args=args, daemon=True)
                t.start()
            except Exception as e:
                logger.error(f"Motion callback error: {e}")