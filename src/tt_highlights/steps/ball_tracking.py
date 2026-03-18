"""Step: ball_tracking – detect and track ball across entire video (optional, quality-gated).

Produces:
  - ball_tracks.json with full-video time-series samples + optional per-rally tracks
"""

import json
import logging
import math
from pathlib import Path

import cv2
import numpy as np

from ..job import artifacts_dir, debug_dir

logger = logging.getLogger(__name__)


def run(job: dict, config: dict, job_path: str) -> None:
    """Execute the ball_tracking step."""
    art = artifacts_dir(job_path)
    dbg = debug_dir(job_path)

    bcfg = config["ball"]

    if not bcfg.get("enabled", True):
        logger.info("Ball tracking disabled in config.")
        _write_disabled(art)
        return

    # Load required artifacts
    roi_path = art / "table_roi.json"
    proxy_path = art / "proxy.mp4"

    if not roi_path.exists() or not proxy_path.exists():
        logger.warning("Missing required artifacts (table_roi or proxy). Skipping ball tracking.")
        _write_disabled(art)
        return

    with open(roi_path, "r", encoding="utf-8") as f:
        roi_data = json.load(f)

    # rallies.json is optional — used for backward-compat per-rally tracks
    rallies_path = art / "rallies.json"
    rallies = []
    if rallies_path.exists():
        with open(rallies_path, "r", encoding="utf-8") as f:
            rallies_data = json.load(f)
        rallies = rallies_data.get("rallies", [])

    polygon = np.array(roi_data["table_polygon"], dtype=np.float32)
    warp_w = config["video"]["warp_width"]
    warp_h = config["video"]["warp_height"]

    dst_pts = np.array([
        [0, 0], [warp_w, 0], [warp_w, warp_h], [0, warp_h]
    ], dtype=np.float32)
    H, _ = cv2.findHomography(polygon, dst_pts)

    detection_fps = bcfg["detection_fps"]
    diff_threshold = bcfg["diff_threshold"]
    min_area = bcfg["min_area"]
    max_area = bcfg["max_area"]
    min_circularity = bcfg["min_circularity"]
    max_aspect_ratio = bcfg["max_aspect_ratio"]
    max_jump_px = bcfg["max_jump_px"]
    max_misses = bcfg["max_misses"]
    quality_min_ratio = bcfg["quality_min_ratio"]

    cap = cv2.VideoCapture(str(proxy_path))
    video_fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if video_fps <= 0:
        video_fps = 30.0

    frame_interval = max(1, int(round(video_fps / detection_fps)))
    actual_fps = video_fps / frame_interval
    duration = total_frames / video_fps

    logger.info(
        f"Ball tracking: full-video scan, {total_frames} frames, "
        f"interval={frame_interval}, ~{duration:.1f}s"
    )

    # ── Full-video scan ───────────────────────────────────────────────────
    prev_gray = None
    samples = []
    frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % frame_interval == 0:
            t = frame_idx / video_fps

            warped = cv2.warpPerspective(frame, H, (warp_w, warp_h))
            gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)

            detected = False
            best_cx, best_cy, best_conf = 0.0, 0.0, 0.0

            if prev_gray is not None:
                diff = cv2.absdiff(gray, prev_gray)
                _, binary = cv2.threshold(diff, diff_threshold, 255, cv2.THRESH_BINARY)

                contours, _ = cv2.findContours(
                    binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
                )

                for cnt in contours:
                    area = cv2.contourArea(cnt)
                    if area < min_area or area > max_area:
                        continue

                    perimeter = cv2.arcLength(cnt, True)
                    if perimeter == 0:
                        continue
                    circularity = 4 * math.pi * area / (perimeter ** 2)
                    if circularity < min_circularity:
                        continue

                    x, y, w, h = cv2.boundingRect(cnt)
                    aspect = max(w, h) / max(min(w, h), 1)
                    if aspect > max_aspect_ratio:
                        continue

                    cx = x + w / 2
                    cy = y + h / 2
                    conf = circularity * min(1.0, area / max_area)

                    if conf > best_conf:
                        detected = True
                        best_cx, best_cy, best_conf = cx, cy, conf

            sample = {"t": round(t, 3), "detected": detected}
            if detected:
                sample["x"] = round(best_cx, 1)
                sample["y"] = round(best_cy, 1)
                sample["conf"] = round(best_conf, 3)
            samples.append(sample)

            prev_gray = gray

        frame_idx += 1

    cap.release()

    # ── Per-rally tracks (backward compat, only if rallies.json exists) ───
    tracks = []
    if rallies:
        for rally in rallies:
            rid = rally["id"]
            r_start = rally["start"]
            r_end = rally.get("end_refined", rally["end"])

            # Filter samples within rally time window
            rally_detections = [
                s for s in samples
                if r_start <= s["t"] <= r_end and s["detected"]
            ]

            total_in_window = sum(
                1 for s in samples if r_start <= s["t"] <= r_end
            )

            # Simple nearest-neighbor tracking
            best_track = _simple_track(rally_detections, max_jump_px, max_misses)
            quality = len(best_track) / max(total_in_window, 1)

            tracks.append({
                "rally_id": rid,
                "quality": round(quality, 4),
                "best_track": best_track,
            })

    output = {
        "enabled": True,
        "fps": round(actual_fps, 2),
        "samples": samples,
        "tracks": tracks,
    }
    with open(art / "ball_tracks.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    detected_count = sum(1 for s in samples if s["detected"])
    logger.info(
        f"Ball tracking done: {len(samples)} samples, "
        f"{detected_count} detections ({detected_count / max(len(samples), 1):.1%}), "
        f"{len(tracks)} rally tracks."
    )


def _simple_track(detections: list[dict], max_jump: float,
                  max_misses: int) -> list[dict]:
    """Simple nearest-neighbor tracking to find the best track."""
    if not detections:
        return []

    # Group detections by time
    by_time = {}
    for d in detections:
        by_time.setdefault(d["t"], []).append(d)

    times = sorted(by_time.keys())
    if not times:
        return []

    # Try starting from each detection in the first frame
    best_track = []

    for start_det in by_time[times[0]]:
        track = [start_det]
        current = start_det
        misses = 0

        for t in times[1:]:
            candidates = by_time[t]
            nearest = None
            nearest_dist = float("inf")

            for c in candidates:
                dist = math.sqrt((c["x"] - current["x"]) ** 2 +
                                 (c["y"] - current["y"]) ** 2)
                if dist < nearest_dist:
                    nearest_dist = dist
                    nearest = c

            if nearest and nearest_dist <= max_jump:
                track.append(nearest)
                current = nearest
                misses = 0
            else:
                misses += 1
                if misses > max_misses:
                    break

        if len(track) > len(best_track):
            best_track = track

    return best_track


def _write_disabled(art: Path) -> None:
    """Write disabled ball_tracks.json."""
    output = {"enabled": False, "fps": 0, "samples": [], "tracks": []}
    with open(art / "ball_tracks.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
