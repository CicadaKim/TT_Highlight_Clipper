"""Step: features – consolidate all signals into per-rally feature vectors.

Output structure per rally:
  {
    rally_id: int,
    raw: { duration, impact_count, ... },
    norm: { duration, impact_count, ... },
    # Flat compat fields (transition period)
    duration: ...,
    impact_count: ...,
    ...
  }

Normalization: percentile clipping + 0-1 scaling within the job.
If fewer than 5 rallies, normalization is skipped (identity fallback).
Binary features (e.g., ocr_score_change) are copied as-is to norm.
"""

import json
import logging
import math
from pathlib import Path

import numpy as np

from ..job import artifacts_dir

logger = logging.getLogger(__name__)

# Features that are binary (0/1) and should not be normalized
_BINARY_FEATURES = {"ocr_score_change"}

# Features where lower raw value = higher quality (inverted normalization)
_INVERSE_FEATURES = {"stance_variability_near", "stance_variability_far"}

# Features from ball tracking that depend on quality gate
_BALL_FEATURES = {
    "ball_speed_peak", "ball_accel_spikes", "ball_coverage_entropy",
}

# Features from player motion (opt-in)
_MOTION_FEATURES = {
    "motion_asymmetry", "end_burst_asymmetry",
    "near_motion_mean", "far_motion_mean",
    "near_end_burst", "far_end_burst",
}

# Features from pose estimation (opt-in)
_POSE_FEATURES = {
    "pose_asymmetry", "swing_count_diff",
    "wrist_speed_peak_near", "wrist_speed_peak_far",
    "wrist_speed_mean_near", "wrist_speed_mean_far",
    "arm_extension_peak_near", "arm_extension_peak_far",
    "pose_confidence_near", "pose_confidence_far",
    "pose_energy_near", "pose_energy_far",
    "stance_variability_near", "stance_variability_far",
    # Aggregate keys (used by scoring config)
    "wrist_speed_peak", "wrist_speed_mean", "arm_extension_peak",
}


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
    player_motion = _load_optional(art / "player_motion.json")
    pose_data = _load_optional(art / "pose_estimation.json")

    rallies = rallies_data["rallies"]
    impacts = audio_events["impact_events"]
    cheers = audio_events["cheer_segments"]
    act_samples = activity_data["samples"]

    act_times = np.array([s["t"] for s in act_samples]) if act_samples else np.array([])
    act_values = np.array([s["activity"] for s in act_samples]) if act_samples else np.array([])

    quality_min = config["ball"]["quality_min_ratio"]

    # Check if ball features are available
    ball_features_enabled = bool(
        ball_tracks and ball_tracks.get("enabled")
    )

    # Check if player motion features are available
    motion_enabled = bool(
        player_motion and player_motion.get("enabled")
    )
    motion_by_rally = {}
    if motion_enabled:
        for mr in player_motion.get("rallies", []):
            motion_by_rally[mr["rally_id"]] = mr

    # Full-video samples fallback for ball/motion
    ball_samples = ball_tracks.get("samples", []) if ball_tracks else []
    motion_samples = player_motion.get("samples", []) if player_motion else []
    motion_zone_labels = player_motion.get("zone_labels", []) if player_motion else []

    # Check if pose estimation features are available
    pose_enabled = bool(pose_data and pose_data.get("enabled"))
    pose_by_rally = {}
    if pose_enabled:
        for pr in pose_data.get("rallies", []):
            pose_by_rally[pr["rally_id"]] = pr

    # ── Extract raw features per rally ────────────────────────────────────
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
        impact_peak_t = max(rally_impacts, key=lambda i: i["score"])["t"] if rally_impacts else (start + end) / 2

        # Collect impact timestamps for selection step
        impact_times = [imp["t"] for imp in rally_impacts]

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
        ball_speed_peak_t = None
        ball_accel_spikes = 0.0
        ball_coverage_entropy = 0.0

        if ball_features_enabled:
            # Try per-rally tracks first (backward compat)
            track_found = False
            for track in ball_tracks.get("tracks", []):
                if track["rally_id"] == rid:
                    ball_quality = track.get("quality", 0.0)
                    if ball_quality >= quality_min:
                        pts = track.get("best_track", [])
                        ball_speed_peak = _ball_speed_peak(pts)
                        ball_speed_peak_t = _ball_speed_peak_time(pts)
                        ball_accel_spikes = _ball_accel_spikes(pts)
                        ball_coverage_entropy = _ball_coverage_entropy(
                            pts, config["video"]["warp_width"],
                            config["video"]["warp_height"]
                        )
                    track_found = True
                    break

            # Fallback: slice from full-video samples
            if not track_found and ball_samples:
                rally_ball = [
                    s for s in ball_samples
                    if start <= s["t"] <= end and s.get("detected")
                ]
                total_in_window = sum(
                    1 for s in ball_samples if start <= s["t"] <= end
                )
                ball_quality = len(rally_ball) / max(total_in_window, 1)
                if ball_quality >= quality_min:
                    ball_speed_peak = _ball_speed_peak(rally_ball)
                    ball_speed_peak_t = _ball_speed_peak_time(rally_ball)
                    ball_accel_spikes = _ball_accel_spikes(rally_ball)
                    ball_coverage_entropy = _ball_coverage_entropy(
                        rally_ball, config["video"]["warp_width"],
                        config["video"]["warp_height"]
                    )

        # Player motion features
        motion_asymmetry = 0.0
        end_burst_asymmetry = 0.0
        near_motion_mean = 0.0
        far_motion_mean = 0.0
        near_end_burst = 0.0
        far_end_burst = 0.0

        if motion_enabled and rid in motion_by_rally:
            # Use per-rally summaries (backward compat)
            mr = motion_by_rally[rid]
            motion_asymmetry = mr.get("motion_asymmetry", 0.0)
            end_burst_asymmetry = mr.get("end_burst_asymmetry", 0.0)
            zones = mr.get("zones", {})
            if "near" in zones:
                near_motion_mean = zones["near"].get("raw_mean", 0.0)
                near_end_burst = zones["near"].get("raw_end_burst", 0.0)
            if "far" in zones:
                far_motion_mean = zones["far"].get("raw_mean", 0.0)
                far_end_burst = zones["far"].get("raw_end_burst", 0.0)
        elif motion_enabled and motion_samples and motion_zone_labels:
            # Fallback: slice from full-video samples
            rally_ms = [
                s for s in motion_samples if start <= s["t"] <= end
            ]
            if rally_ms:
                zone_means = {}
                for zl in motion_zone_labels:
                    vals = [s.get(zl, 0.0) for s in rally_ms]
                    zone_means[zl] = float(np.mean(vals)) if vals else 0.0
                if "near" in zone_means:
                    near_motion_mean = zone_means["near"]
                if "far" in zone_means:
                    far_motion_mean = zone_means["far"]
                # End burst: last 20% of samples
                n = len(rally_ms)
                tail_start = max(0, n - max(1, n // 5))
                tail = rally_ms[tail_start:]
                if "near" in zone_means and tail:
                    near_end_burst = float(np.mean([s.get("near", 0.0) for s in tail]))
                if "far" in zone_means and tail:
                    far_end_burst = float(np.mean([s.get("far", 0.0) for s in tail]))
                # Asymmetry
                if len(motion_zone_labels) == 2:
                    m1 = zone_means.get(motion_zone_labels[0], 0.0)
                    m2 = zone_means.get(motion_zone_labels[1], 0.0)
                    denom = max(m1 + m2, 1e-6)
                    motion_asymmetry = abs(m1 - m2) / denom
                    eb1 = near_end_burst if motion_zone_labels[0] == "near" else far_end_burst
                    eb2 = far_end_burst if motion_zone_labels[0] == "near" else near_end_burst
                    eb_denom = max(eb1 + eb2, 1e-6)
                    end_burst_asymmetry = abs(eb1 - eb2) / eb_denom

        # Pose estimation features (per-zone raw values)
        pose_asymmetry = 0.0
        swing_count_diff = 0.0
        wrist_speed_peak_near = 0.0
        wrist_speed_peak_far = 0.0
        wrist_speed_mean_near = 0.0
        wrist_speed_mean_far = 0.0
        arm_extension_peak_near = 0.0
        arm_extension_peak_far = 0.0
        pose_confidence_near = 0.0
        pose_confidence_far = 0.0
        pose_energy_near = 0.0
        pose_energy_far = 0.0
        stance_variability_near = 0.0
        stance_variability_far = 0.0

        if pose_enabled and rid in pose_by_rally:
            pr = pose_by_rally[rid]
            pose_asymmetry = pr.get("pose_asymmetry", 0.0)
            swing_count_diff = pr.get("swing_count_diff", 0.0)
            pzones = pr.get("zones", {})
            if "near" in pzones:
                wrist_speed_peak_near = pzones["near"].get("wrist_speed_peak", 0.0)
                wrist_speed_mean_near = pzones["near"].get("wrist_speed_mean", 0.0)
                arm_extension_peak_near = pzones["near"].get("arm_extension_peak", 0.0)
                pose_confidence_near = pzones["near"].get("pose_confidence", 0.0)
                pose_energy_near = pzones["near"].get("pose_energy", 0.0)
                stance_variability_near = pzones["near"].get("stance_variability", 0.0)
            if "far" in pzones:
                wrist_speed_peak_far = pzones["far"].get("wrist_speed_peak", 0.0)
                wrist_speed_mean_far = pzones["far"].get("wrist_speed_mean", 0.0)
                arm_extension_peak_far = pzones["far"].get("arm_extension_peak", 0.0)
                pose_confidence_far = pzones["far"].get("pose_confidence", 0.0)
                pose_energy_far = pzones["far"].get("pose_energy", 0.0)
                stance_variability_far = pzones["far"].get("stance_variability", 0.0)

        raw = {
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
            "motion_asymmetry": round(motion_asymmetry, 4),
            "end_burst_asymmetry": round(end_burst_asymmetry, 4),
            "near_motion_mean": round(near_motion_mean, 4),
            "far_motion_mean": round(far_motion_mean, 4),
            "near_end_burst": round(near_end_burst, 4),
            "far_end_burst": round(far_end_burst, 4),
            "wrist_speed_peak_near": round(wrist_speed_peak_near, 4),
            "wrist_speed_peak_far": round(wrist_speed_peak_far, 4),
            "wrist_speed_mean_near": round(wrist_speed_mean_near, 4),
            "wrist_speed_mean_far": round(wrist_speed_mean_far, 4),
            "arm_extension_peak_near": round(arm_extension_peak_near, 4),
            "arm_extension_peak_far": round(arm_extension_peak_far, 4),
            "pose_confidence_near": round(pose_confidence_near, 4),
            "pose_confidence_far": round(pose_confidence_far, 4),
            "pose_energy_near": round(pose_energy_near, 4),
            "pose_energy_far": round(pose_energy_far, 4),
            "stance_variability_near": round(stance_variability_near, 4),
            "stance_variability_far": round(stance_variability_far, 4),
            "pose_asymmetry": round(pose_asymmetry, 4),
            "swing_count_diff": round(swing_count_diff, 4),
            # Scoring-aggregate features (max/mean across zones)
            "wrist_speed_peak": round(max(wrist_speed_peak_near, wrist_speed_peak_far), 4),
            "wrist_speed_mean": round((wrist_speed_mean_near + wrist_speed_mean_far) / 2, 4),
            "arm_extension_peak": round(max(arm_extension_peak_near, arm_extension_peak_far), 4),
        }

        feat = {
            "rally_id": rid,
            "raw": raw,
            "impact_times": impact_times,
            "ball_speed_peak_t": ball_speed_peak_t,
            "ball_features_enabled": ball_features_enabled,
        }

        # Flat compat fields (transition period)
        feat.update(raw)

        rally_features.append(feat)

    # ── Normalize features ────────────────────────────────────────────────
    norm_cfg = config.get("scoring", {}).get("normalization", {})
    clip_low = norm_cfg.get("clip_percentile_low", 5)
    clip_high = norm_cfg.get("clip_percentile_high", 95)

    _normalize_features(rally_features, clip_low, clip_high)

    output = {"rally_features": rally_features}
    with open(art / "features.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    logger.info(f"Features computed for {len(rally_features)} rallies.")


def _normalize_features(
    rally_features: list[dict],
    clip_low: float = 5,
    clip_high: float = 95,
) -> None:
    """Add 'norm' dict with 0-1 normalized features to each rally.

    - Skips normalization (identity) if fewer than 5 rallies
    - Binary features are copied as-is
    - Percentile clipping prevents outlier dominance
    """
    if not rally_features:
        return

    # Collect all numeric feature keys from raw
    feature_keys = list(rally_features[0]["raw"].keys())

    if len(rally_features) < 5:
        # Identity fallback: norm = raw
        for feat in rally_features:
            feat["norm"] = dict(feat["raw"])
        return

    # Collect values per feature for percentile computation
    feature_values: dict[str, list[float]] = {
        k: [] for k in feature_keys
    }
    for feat in rally_features:
        for k in feature_keys:
            feature_values[k].append(float(feat["raw"].get(k, 0) or 0))

    # Compute percentile bounds per feature
    bounds: dict[str, tuple[float, float]] = {}
    for k in feature_keys:
        if k in _BINARY_FEATURES:
            continue
        vals = np.array(feature_values[k])
        low = float(np.percentile(vals, clip_low))
        high = float(np.percentile(vals, clip_high))
        bounds[k] = (low, high)

    # Normalize
    for feat in rally_features:
        norm = {}
        for k in feature_keys:
            raw_val = float(feat["raw"].get(k, 0) or 0)
            if k in _BINARY_FEATURES:
                norm[k] = raw_val
            elif k in bounds:
                low, high = bounds[k]
                if high - low < 1e-9:
                    if k in _INVERSE_FEATURES:
                        norm[k] = 0.5 if raw_val > 0 else 1.0
                    else:
                        norm[k] = 0.5 if raw_val > 0 else 0.0
                else:
                    clipped = max(low, min(high, raw_val))
                    if k in _INVERSE_FEATURES:
                        norm[k] = round(1.0 - (clipped - low) / (high - low), 4)
                    else:
                        norm[k] = round((clipped - low) / (high - low), 4)
            else:
                norm[k] = raw_val
        feat["norm"] = norm


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


def _ball_speed_peak_time(pts: list[dict]) -> float | None:
    """Return the time at which peak ball speed occurs."""
    if len(pts) < 2:
        return None
    best_speed = 0.0
    best_t = None
    for i in range(1, len(pts)):
        dt = pts[i]["t"] - pts[i - 1]["t"]
        if dt <= 0:
            continue
        dx = pts[i]["x"] - pts[i - 1]["x"]
        dy = pts[i]["y"] - pts[i - 1]["y"]
        speed = math.sqrt(dx ** 2 + dy ** 2) / dt
        if speed > best_speed:
            best_speed = speed
            best_t = pts[i]["t"]
    return best_t


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
