"""Step: ball_tracking – detect and track ball in rally segments (optional, quality-gated)."""

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
    rallies_path = art / "rallies.json"
    proxy_path = art / "proxy.mp4"

    if not roi_path.exists() or not rallies_path.exists() or not proxy_path.exists():
        logger.warning("Missing required artifacts. Skipping ball tracking.")
        _write_disabled(art)
        return

    with open(roi_path, "r", encoding="utf-8") as f:
        roi_data = json.load(f)
    with open(rallies_path, "r", encoding="utf-8") as f:
        rallies_data = json.load(f)

    rallies = rallies_data["rallies"]
    if not rallies:
        logger.info("No rallies to track. Writing empty ball tracks.")
        _write_empty(art)
        return

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
    if video_fps <= 0:
        video_fps = 30.0

    frame_interval = max(1, int(round(video_fps / detection_fps)))

    tracks = []

    for rally in rallies:
        rid = rally["id"]
        r_start = rally["start"]
        r_end = rally.get("end_refined", rally["end"])

        logger.info(f"Tracking ball in rally {rid}: [{r_start:.1f}-{r_end:.1f}]")

        start_frame = int(r_start * video_fps)
        end_frame = int(r_end * video_fps)

        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

        prev_gray = None
        detections = []
        total_frames_checked = 0
        frame_idx = start_frame

        while frame_idx <= end_frame:
            ret, frame = cap.read()
            if not ret:
                break

            if (frame_idx - start_frame) % frame_interval == 0:
                total_frames_checked += 1
                t = frame_idx / video_fps

                warped = cv2.warpPerspective(frame, H, (warp_w, warp_h))
                gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)

                if prev_gray is not None:
                    diff = cv2.absdiff(gray, prev_gray)
                    _, binary = cv2.threshold(diff, diff_threshold, 255, cv2.THRESH_BINARY)

                    # Find blob candidates
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

                        detections.append({
                            "t": round(t, 3),
                            "x": round(cx, 1),
                            "y": round(cy, 1),
                            "conf": round(conf, 3),
                        })

                prev_gray = gray

            frame_idx += 1

        # Simple nearest-neighbor tracking
        best_track = _simple_track(detections, max_jump_px, max_misses)

        # Quality: detection ratio
        quality = len(best_track) / max(total_frames_checked, 1)

        tracks.append({
            "rally_id": rid,
            "quality": round(quality, 4),
            "best_track": best_track,
        })

        logger.info(f"  Rally {rid}: {len(best_track)} detections, "
                     f"quality={quality:.3f}")

    cap.release()

    output = {
        "enabled": True,
        "tracks": tracks,
    }
    with open(art / "ball_tracks.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    logger.info(f"Ball tracking done: {len(tracks)} rally tracks.")


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
    output = {"enabled": False, "tracks": []}
    with open(art / "ball_tracks.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)


def _write_empty(art: Path) -> None:
    """Write enabled but empty ball_tracks.json."""
    output = {"enabled": True, "tracks": []}
    with open(art / "ball_tracks.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
