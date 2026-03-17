"""Step: features – consolidate all signals into per-rally feature vectors."""

import json
import logging
import math
from pathlib import Path

import numpy as np

from ..job import artifacts_dir

logger = logging.getLogger(__name__)


def run(job: dict, config: dict, job_path: str) -> None:
    """Execute the features step."""
    art = artifacts_dir(job_path)

    # Load required inputs
    with open(art / "rallies.json", "r", encoding="utf-8") as f:
        rallies_data = json.load(f)
    with open(art / "audio_events.json", "r", encoding="utf-8") as f:
        audio_events = json.load(f)
    with open(art / "activity.json", "r", encoding="utf-8") as f:
        activity_data = json.load(f)

    # Load optional inputs
    ocr_events = _load_optional(art / "ocr_events.json")
    ball_tracks = _load_optional(art / "ball_tracks.json")

    rallies = rallies_data["rallies"]
    impacts = audio_events["impact_events"]
    cheers = audio_events["cheer_segments"]
    act_samples = activity_data["samples"]

    act_times = np.array([s["t"] for s in act_samples]) if act_samples else np.array([])
    act_values = np.array([s["activity"] for s in act_samples]) if act_samples else np.array([])

    quality_min = config["ball"]["quality_min_ratio"]

    rally_features = []
    for rally in rallies:
        rid = rally["id"]
        start = rally["start"]
        end = rally.get("end_refined", rally["end"])
        duration = end - start

        # Impact features
        rally_impacts = [imp for imp in impacts if start <= imp["t"] <= end]
        impact_count = len(rally_impacts)
        impact_rate = impact_count / max(duration, 0.1)
        impact_peak = max((imp["score"] for imp in rally_impacts), default=0.0)
        # Timestamp of the highest-score impact within the rally
        impact_peak_t = max(rally_impacts, key=lambda i: i["score"])["t"] if rally_impacts else (start + end) / 2

        # Activity features
        activity_mean, activity_peak = _activity_stats(act_times, act_values, start, end)

        # Cheer near end: within 2 sec before end_refined
        cheer_near_end = _cheer_near_end(cheers, end, window=2.0)

        # OCR score change
        ocr_score_change = 0
        if ocr_events and ocr_events.get("enabled"):
            for ev in ocr_events.get("events", []):
                if ev["rally_id"] == rid:
                    ocr_score_change = 1
                    break

        # Post-pause: activity drop after end
        post_pause = _post_pause(act_times, act_values, end, window=3.0)

        # Ball features (quality-gated)
        ball_quality = 0.0
        ball_speed_peak = 0.0
        ball_accel_spikes = 0.0
        ball_coverage_entropy = 0.0

        if ball_tracks and ball_tracks.get("enabled"):
            for track in ball_tracks.get("tracks", []):
                if track["rally_id"] == rid:
                    ball_quality = track.get("quality", 0.0)
                    if ball_quality >= quality_min:
                        pts = track.get("best_track", [])
                        ball_speed_peak = _ball_speed_peak(pts)
                        ball_accel_spikes = _ball_accel_spikes(pts)
                        ball_coverage_entropy = _ball_coverage_entropy(
                            pts, config["video"]["warp_width"],
                            config["video"]["warp_height"]
                        )
                    break

        feat = {
            "rally_id": rid,
            "duration": round(duration, 3),
            "impact_count": impact_count,
            "impact_rate": round(impact_rate, 4),
            "impact_peak": round(impact_peak, 4),
            "impact_peak_t": round(impact_peak_t, 3),
            "activity_mean": round(activity_mean, 4),
            "activity_peak": round(activity_peak, 4),
            "cheer_near_end": round(cheer_near_end, 4),
            "ocr_score_change": ocr_score_change,
            "post_pause": round(post_pause, 4),
            "ball_track_quality": round(ball_quality, 4),
            "ball_speed_peak": round(ball_speed_peak, 4),
            "ball_accel_spikes": round(ball_accel_spikes, 4),
            "ball_coverage_entropy": round(ball_coverage_entropy, 4),
        }
        rally_features.append(feat)

    output = {"rally_features": rally_features}
    with open(art / "features.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    logger.info(f"Features computed for {len(rally_features)} rallies.")


def _load_optional(path: Path) -> dict | None:
    """Load a JSON file if it exists, else return None."""
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _activity_stats(times: np.ndarray, values: np.ndarray,
                    start: float, end: float) -> tuple[float, float]:
    """Get mean and peak activity in time range."""
    if len(times) == 0:
        return 0.0, 0.0
    mask = (times >= start) & (times <= end)
    if not mask.any():
        return 0.0, 0.0
    segment = values[mask]
    return float(segment.mean()), float(segment.max())


def _cheer_near_end(cheers: list, end: float, window: float) -> float:
    """Compute cheer presence near rally end (0-1 scale)."""
    best_score = 0.0
    for ch in cheers:
        # Check if cheer segment overlaps with [end - window, end]
        if ch["end"] >= (end - window) and ch["start"] <= end:
            best_score = max(best_score, ch["score"])
    return best_score


def _post_pause(times: np.ndarray, values: np.ndarray,
                end: float, window: float) -> float:
    """Compute activity drop after rally end."""
    if len(times) == 0:
        return 0.0
    # Mean activity in [end, end + window]
    mask_post = (times >= end) & (times <= end + window)
    # Mean activity in [end - window, end]
    mask_pre = (times >= end - window) & (times <= end)

    pre_mean = float(values[mask_pre].mean()) if mask_pre.any() else 0.0
    post_mean = float(values[mask_post].mean()) if mask_post.any() else 0.0

    return max(0.0, pre_mean - post_mean)


def _ball_speed_peak(pts: list[dict]) -> float:
    """Compute peak ball speed from track points."""
    if len(pts) < 2:
        return 0.0
    speeds = []
    for i in range(1, len(pts)):
        dt = pts[i]["t"] - pts[i - 1]["t"]
        if dt <= 0:
            continue
        dx = pts[i]["x"] - pts[i - 1]["x"]
        dy = pts[i]["y"] - pts[i - 1]["y"]
        speed = math.sqrt(dx ** 2 + dy ** 2) / dt
        speeds.append(speed)
    return max(speeds) if speeds else 0.0


def _ball_accel_spikes(pts: list[dict]) -> float:
    """Count acceleration spikes (sharp direction/speed changes)."""
    if len(pts) < 3:
        return 0.0

    speeds = []
    for i in range(1, len(pts)):
        dt = pts[i]["t"] - pts[i - 1]["t"]
        if dt <= 0:
            continue
        dx = pts[i]["x"] - pts[i - 1]["x"]
        dy = pts[i]["y"] - pts[i - 1]["y"]
        speeds.append(math.sqrt(dx ** 2 + dy ** 2) / dt)

    if len(speeds) < 2:
        return 0.0

    # Count large acceleration changes
    spikes = 0
    for i in range(1, len(speeds)):
        accel = abs(speeds[i] - speeds[i - 1])
        if accel > 50:  # px/s^2 threshold
            spikes += 1

    return float(spikes)


def _ball_coverage_entropy(pts: list[dict], warp_w: int, warp_h: int) -> float:
    """Compute spatial coverage entropy of ball trajectory."""
    if len(pts) < 2:
        return 0.0

    # Divide warp area into grid cells
    grid_cols, grid_rows = 8, 4
    cell_w = warp_w / grid_cols
    cell_h = warp_h / grid_rows

    counts = np.zeros(grid_cols * grid_rows)
    for pt in pts:
        col = min(int(pt["x"] / cell_w), grid_cols - 1)
        row = min(int(pt["y"] / cell_h), grid_rows - 1)
        col = max(0, col)
        row = max(0, row)
        counts[row * grid_cols + col] += 1

    # Compute entropy
    total = counts.sum()
    if total == 0:
        return 0.0
    probs = counts / total
    probs = probs[probs > 0]
    entropy = -np.sum(probs * np.log2(probs))

    # Normalize by max possible entropy
    max_entropy = np.log2(grid_cols * grid_rows)
    return float(entropy / max_entropy) if max_entropy > 0 else 0.0
