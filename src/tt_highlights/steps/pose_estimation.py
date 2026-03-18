"""Step: pose_estimation – extract skeleton/pose features using YOLOv8-Pose.

Requires:
  - proxy.mp4 (from preprocess)
  - rallies.json (from rally_segment)
  - player_zones.json (from setup, opt-in)

Produces:
  - pose_estimation.json with per-rally, per-zone keypoint-derived features

Uses COCO 17-keypoint layout. Velocities are normalized by shoulder width
to be resolution/crop-size invariant.
"""

import json
import logging
import math
from pathlib import Path

import cv2
import numpy as np

from ..job import artifacts_dir, debug_dir
from ..runtime import resolve_device

logger = logging.getLogger(__name__)

# COCO 17 keypoint indices
KP_NOSE = 0
KP_L_EYE, KP_R_EYE = 1, 2
KP_L_EAR, KP_R_EAR = 3, 4
KP_L_SHOULDER, KP_R_SHOULDER = 5, 6
KP_L_ELBOW, KP_R_ELBOW = 7, 8
KP_L_WRIST, KP_R_WRIST = 9, 10
KP_L_HIP, KP_R_HIP = 11, 12
KP_L_KNEE, KP_R_KNEE = 13, 14
KP_L_ANKLE, KP_R_ANKLE = 15, 16

NUM_KEYPOINTS = 17

# COCO skeleton edges for debug drawing
SKELETON_EDGES = [
    (KP_L_SHOULDER, KP_R_SHOULDER),
    (KP_L_SHOULDER, KP_L_ELBOW), (KP_L_ELBOW, KP_L_WRIST),
    (KP_R_SHOULDER, KP_R_ELBOW), (KP_R_ELBOW, KP_R_WRIST),
    (KP_L_SHOULDER, KP_L_HIP), (KP_R_SHOULDER, KP_R_HIP),
    (KP_L_HIP, KP_R_HIP),
    (KP_L_HIP, KP_L_KNEE), (KP_L_KNEE, KP_L_ANKLE),
    (KP_R_HIP, KP_R_KNEE), (KP_R_KNEE, KP_R_ANKLE),
]

# Minimum confidence for a keypoint to be considered valid
KP_CONF_THRESHOLD = 0.3

# Minimum crop dimension for YOLO to work reliably
MIN_CROP_DIM = 64


def run(job: dict, config: dict, job_path: str) -> None:
    """Execute the pose_estimation step."""
    pose_cfg = config.get("pose_estimation", {})
    if not pose_cfg.get("enabled", False):
        logger.info("pose_estimation disabled — skipping.")
        return

    art = artifacts_dir(job_path)
    dbg = debug_dir(job_path)
    dbg.mkdir(parents=True, exist_ok=True)

    proxy_path = art / "proxy.mp4"
    zones_path = art / "player_zones.json"
    rallies_path = art / "rallies.json"

    if not proxy_path.exists():
        logger.warning("proxy.mp4 not found — skipping pose_estimation.")
        return
    if not rallies_path.exists():
        logger.warning("rallies.json not found — skipping pose_estimation.")
        _write_empty(art)
        return
    if not zones_path.exists():
        logger.warning("player_zones.json not found — skipping pose_estimation.")
        _write_empty(art)
        return

    # Import ultralytics (GPU extras)
    try:
        from ultralytics import YOLO
    except ImportError:
        logger.warning(
            "ultralytics not installed — skipping pose_estimation. "
            "Install with: pip install -r requirements-gpu.txt"
        )
        _write_empty(art)
        return

    # Load artifacts
    with open(zones_path, "r", encoding="utf-8") as f:
        zones_data = json.load(f)
    with open(rallies_path, "r", encoding="utf-8") as f:
        rallies_data = json.load(f)

    from ..job import proxy_scale, scale_zones
    sx, sy = proxy_scale(job_path)
    zones = scale_zones(zones_data.get("zones", []), sx, sy)
    if not zones:
        logger.warning("No player zones defined — skipping pose_estimation.")
        _write_empty(art)
        return

    rallies = rallies_data.get("rallies", [])
    if not rallies:
        logger.warning("No rallies found — skipping pose_estimation.")
        _write_empty(art)
        return

    sample_fps = pose_cfg.get("sample_fps", 5)
    conf_threshold = pose_cfg.get("conf_threshold", 0.5)
    model_size = pose_cfg.get("model_size", "s")
    smoothing_window = pose_cfg.get("smoothing_window", 3)
    swing_speed_threshold = pose_cfg.get("swing_speed_threshold", 0.8)

    # Model initialization with GPU→CPU fallback
    device = resolve_device(config)
    model, device = _init_model(YOLO, model_size, device)
    if model is None:
        _write_empty(art)
        return

    # Open video
    cap = cv2.VideoCapture(str(proxy_path))
    video_fps = cap.get(cv2.CAP_PROP_FPS)
    if video_fps <= 0:
        video_fps = 30.0
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_interval = max(1, int(round(video_fps / sample_fps)))
    actual_fps = video_fps / frame_interval

    logger.info(
        f"pose_estimation: {len(zones)} zones, {len(rallies)} rallies, "
        f"model=yolov8{model_size}-pose, device={device}, "
        f"sample_interval={frame_interval}"
    )

    # Precompute zone rects and validate sizes
    zone_rects = []
    zone_labels = []
    for zone in zones:
        r = zone["rect"]
        x1 = max(0, r["x"])
        y1 = max(0, r["y"])
        x2 = min(frame_w, r["x"] + r["w"])
        y2 = min(frame_h, r["y"] + r["h"])
        if (x2 - x1) < MIN_CROP_DIM or (y2 - y1) < MIN_CROP_DIM:
            logger.warning(
                f"Zone '{zone.get('label', '?')}' crop too small "
                f"({x2-x1}x{y2-y1}) — skipping zone."
            )
            continue
        zone_rects.append((x1, y1, x2, y2))
        zone_labels.append(zone.get("label", f"zone_{len(zone_labels)}"))

    if not zone_rects:
        logger.warning("All zone crops too small — skipping pose_estimation.")
        _write_empty(art)
        return

    # Build rally time ranges
    rally_ranges = []
    for rally in rallies:
        start = rally["start"]
        end = rally.get("end_refined", rally["end"])
        rally_ranges.append((rally["id"], start, end))

    # Per-rally per-zone keypoint accumulator:
    # rally_kps[rid][label] = list of (t, best_person_kps, best_conf)
    rally_kps: dict[int, dict[str, list]] = {}
    for rid, _, _ in rally_ranges:
        rally_kps[rid] = {label: [] for label in zone_labels}

    # Debug sample collection
    debug_samples: list[dict] = []

    # Video scan loop
    frame_idx = 0
    import torch  # for OOM handling

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % frame_interval != 0:
            frame_idx += 1
            continue

        t = frame_idx / video_fps

        # Check if this timestamp falls within any rally
        active_rallies = [
            (rid, rstart, rend)
            for rid, rstart, rend in rally_ranges
            if rstart <= t <= rend
        ]
        if not active_rallies:
            frame_idx += 1
            continue

        # Crop zones
        crops = []
        for x1, y1, x2, y2 in zone_rects:
            crops.append(frame[y1:y2, x1:x2])

        # Batch inference (with OOM fallback)
        results = _run_inference(model, crops, conf_threshold, torch)

        if results is None:
            frame_idx += 1
            continue

        # Process results per zone
        for zi, res in enumerate(results):
            label = zone_labels[zi]
            best_kp, best_conf = _extract_best_person(res, conf_threshold)
            if best_kp is None:
                continue

            for rid, rstart, rend in active_rallies:
                rally_kps[rid][label].append((t, best_kp, best_conf))

            # Collect debug samples (first 3 rallies, up to 5 per rally-zone)
            if active_rallies:
                rid0 = active_rallies[0][0]
                if rid0 <= 3:
                    existing = sum(
                        1 for d in debug_samples
                        if d["rid"] == rid0 and d["label"] == label
                    )
                    if existing < 5:
                        debug_samples.append({
                            "rid": rid0,
                            "label": label,
                            "t": t,
                            "crop": crops[zi].copy(),
                            "kp": best_kp,
                        })

        frame_idx += 1

    cap.release()

    # Compute per-rally per-zone features
    save_samples = pose_cfg.get("save_samples", True)
    rally_results = []
    for rid, rstart, rend in rally_ranges:
        zone_features = {}
        for label in zone_labels:
            samples = rally_kps[rid][label]
            zone_features[label] = _compute_zone_features(
                samples, smoothing_window, swing_speed_threshold,
                save_samples=save_samples,
            )

        # Inter-player features
        pose_asymmetry = 0.0
        swing_count_diff = 0.0
        if len(zone_labels) == 2:
            e1 = zone_features[zone_labels[0]]["pose_energy"]
            e2 = zone_features[zone_labels[1]]["pose_energy"]
            denom = max(e1 + e2, 1e-6)
            pose_asymmetry = round(abs(e1 - e2) / denom, 4)

            s1 = zone_features[zone_labels[0]]["swing_count"]
            s2 = zone_features[zone_labels[1]]["swing_count"]
            swing_count_diff = abs(s1 - s2)

        rally_results.append({
            "rally_id": rid,
            "zones": zone_features,
            "pose_asymmetry": pose_asymmetry,
            "swing_count_diff": float(swing_count_diff),
            "debug_samples": [
                {"zone_label": d["label"], "t": d["t"],
                 "path": f"debug/pose_samples/rally_{d['rid']}_zone_{d['label']}_t{d['t']:.1f}.png"}
                for d in debug_samples if d["rid"] == rid
            ],
        })

    output = {
        "enabled": True,
        "model": f"yolov8{model_size}-pose",
        "fps": round(actual_fps, 2),
        "zone_labels": zone_labels,
        "rallies": rally_results,
    }
    with open(art / "pose_estimation.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    logger.info(f"Pose estimation computed for {len(rally_results)} rallies.")

    # Debug outputs
    if debug_samples:
        _save_debug_skeletons(debug_samples, dbg)
    if rally_results:
        _plot_pose_summary(
            rally_results, zone_labels, swing_speed_threshold, dbg
        )


# ── Model init ─────────────────────────────────────────────────────────


def _init_model(YOLO, model_size: str, device: str):
    """Initialize YOLOv8-Pose model with GPU→CPU fallback.

    Returns (model, device) or (None, device) on failure.
    """
    model_name = f"yolov8{model_size}-pose.pt"
    try:
        model = YOLO(model_name)
        model.to(device)
        return model, device
    except Exception as e:
        logger.error(f"Failed to load pose model on {device}: {e}")
        if device != "cpu":
            logger.info("Falling back to CPU for pose model.")
            try:
                model = YOLO(model_name)
                model.to("cpu")
                return model, "cpu"
            except Exception as e2:
                logger.error(f"CPU fallback also failed: {e2}")
                return None, "cpu"
        return None, device


# ── Inference ──────────────────────────────────────────────────────────


def _run_inference(model, crops, conf_threshold, torch):
    """Run YOLO inference on crop list, with OOM fallback to single-crop."""
    try:
        results = model(crops, verbose=False, conf=conf_threshold)
        return results
    except RuntimeError as e:
        if "out of memory" in str(e).lower():
            logger.warning("CUDA OOM — falling back to single-crop inference.")
            torch.cuda.empty_cache()
            results = []
            for c in crops:
                try:
                    r = model(c, verbose=False, conf=conf_threshold)
                    results.append(r[0])
                except RuntimeError:
                    torch.cuda.empty_cache()
                    return None
            return results
        raise


def _extract_best_person(result, conf_threshold):
    """Extract keypoints of the highest-confidence person from a YOLO result.

    Returns (keypoints_array, detection_confidence) or (None, 0.0).
    keypoints_array shape: (17, 3) — [x, y, conf] per keypoint.
    """
    if result.keypoints is None or len(result.keypoints) == 0:
        return None, 0.0

    # result.keypoints.data shape: (num_persons, 17, 3)
    kps_data = result.keypoints.data.cpu().numpy()
    if len(kps_data) == 0:
        return None, 0.0

    # result.boxes.conf shape: (num_persons,)
    confs = result.boxes.conf.cpu().numpy()
    if len(confs) == 0:
        return None, 0.0

    best_idx = int(confs.argmax())
    best_conf = float(confs[best_idx])
    if best_conf < conf_threshold:
        return None, 0.0

    return kps_data[best_idx], best_conf  # shape (17, 3)


# ── Feature computation ───────────────────────────────────────────────


def _shoulder_width(kp):
    """Compute shoulder width in pixels (normalizer for speed)."""
    ls = kp[KP_L_SHOULDER]
    rs = kp[KP_R_SHOULDER]
    if ls[2] > KP_CONF_THRESHOLD and rs[2] > KP_CONF_THRESHOLD:
        return math.sqrt((ls[0] - rs[0]) ** 2 + (ls[1] - rs[1]) ** 2)
    return None


def _arm_extension(kp):
    """Compute arm extension ratio: dist(elbow,wrist) / dist(shoulder,elbow).

    Returns max of left and right arm. None if keypoints not reliable.
    """
    extensions = []
    for s, e, w in [
        (KP_L_SHOULDER, KP_L_ELBOW, KP_L_WRIST),
        (KP_R_SHOULDER, KP_R_ELBOW, KP_R_WRIST),
    ]:
        if all(kp[j][2] > KP_CONF_THRESHOLD for j in (s, e, w)):
            d_se = math.sqrt(
                (kp[s][0] - kp[e][0]) ** 2 + (kp[s][1] - kp[e][1]) ** 2
            )
            d_ew = math.sqrt(
                (kp[e][0] - kp[w][0]) ** 2 + (kp[e][1] - kp[w][1]) ** 2
            )
            if d_se > 1e-3:
                extensions.append(d_ew / d_se)
    return max(extensions) if extensions else None


def _hip_midpoint(kp):
    """Compute hip midpoint if both hips are visible."""
    lh = kp[KP_L_HIP]
    rh = kp[KP_R_HIP]
    if lh[2] > KP_CONF_THRESHOLD and rh[2] > KP_CONF_THRESHOLD:
        return ((lh[0] + rh[0]) / 2, (lh[1] + rh[1]) / 2)
    return None


def _compute_zone_features(
    samples: list[tuple],  # [(t, kp_array, det_conf), ...]
    smoothing_window: int,
    swing_speed_threshold: float,
    *,
    save_samples: bool = True,
    max_samples: int = 200,
) -> dict:
    """Compute pose features for one zone within one rally."""
    empty = {
        "pose_confidence": 0.0,
        "wrist_speed_peak": 0.0,
        "wrist_speed_mean": 0.0,
        "pose_energy": 0.0,
        "arm_extension_peak": 0.0,
        "stance_variability": 0.0,
        "swing_count": 0,
        "samples": [],
    }

    if len(samples) < 1:
        return empty

    # Detection confidence
    confs = [s[2] for s in samples]
    pose_confidence = round(float(np.mean(confs)), 4)

    # Arm extension peak
    arm_extensions = []
    for _, kp, _ in samples:
        ext = _arm_extension(kp)
        if ext is not None:
            arm_extensions.append(ext)
    arm_extension_peak = round(max(arm_extensions), 4) if arm_extensions else 0.0

    # Hip midpoint variance (stance variability)
    hip_positions = []
    shoulder_widths_for_norm = []
    for _, kp, _ in samples:
        hm = _hip_midpoint(kp)
        sw = _shoulder_width(kp)
        if hm is not None and sw is not None and sw > 1e-3:
            hip_positions.append((hm[0] / sw, hm[1] / sw))
            shoulder_widths_for_norm.append(sw)

    stance_variability = 0.0
    if len(hip_positions) >= 2:
        hip_arr = np.array(hip_positions)
        stance_variability = round(
            float(np.var(hip_arr[:, 0]) + np.var(hip_arr[:, 1])), 4
        )

    # Velocity features (need >= 2 samples)
    if len(samples) < 2:
        return {
            "pose_confidence": pose_confidence,
            "wrist_speed_peak": 0.0,
            "wrist_speed_mean": 0.0,
            "pose_energy": 0.0,
            "arm_extension_peak": arm_extension_peak,
            "stance_variability": stance_variability,
            "swing_count": 0,
            "samples": [],
        }

    # Compute per-frame wrist velocities (normalized by shoulder width)
    wrist_speeds = []  # normalized
    all_kp_speeds = []  # per-frame total kp energy (normalized)
    frame_times = []  # per-frame timestamps (for time-series output)
    frame_arm_extensions = []  # per-frame arm extension

    for i in range(1, len(samples)):
        t_prev, kp_prev, _ = samples[i - 1]
        t_curr, kp_curr, _ = samples[i]
        dt = t_curr - t_prev
        if dt <= 0:
            continue

        sw = _shoulder_width(kp_curr)
        if sw is None or sw < 1e-3:
            continue

        # Wrist speed: max of left and right wrist
        max_wrist_speed = 0.0
        for wrist_idx in (KP_L_WRIST, KP_R_WRIST):
            if (kp_prev[wrist_idx][2] > KP_CONF_THRESHOLD
                    and kp_curr[wrist_idx][2] > KP_CONF_THRESHOLD):
                dx = kp_curr[wrist_idx][0] - kp_prev[wrist_idx][0]
                dy = kp_curr[wrist_idx][1] - kp_prev[wrist_idx][1]
                raw_speed = math.sqrt(dx ** 2 + dy ** 2) / dt
                normalized = raw_speed / sw
                max_wrist_speed = max(max_wrist_speed, normalized)

        wrist_speeds.append(max_wrist_speed)

        # Total keypoint energy: sum of all keypoint velocities (normalized)
        total_kp_speed = 0.0
        for ki in range(NUM_KEYPOINTS):
            if (kp_prev[ki][2] > KP_CONF_THRESHOLD
                    and kp_curr[ki][2] > KP_CONF_THRESHOLD):
                dx = kp_curr[ki][0] - kp_prev[ki][0]
                dy = kp_curr[ki][1] - kp_prev[ki][1]
                raw_speed = math.sqrt(dx ** 2 + dy ** 2) / dt
                total_kp_speed += raw_speed / sw

        all_kp_speeds.append(total_kp_speed)

        # Track per-frame data for time-series output
        frame_times.append(t_curr)
        ext = _arm_extension(kp_curr)
        frame_arm_extensions.append(ext if ext is not None else 0.0)

    if not wrist_speeds:
        return {
            "pose_confidence": pose_confidence,
            "wrist_speed_peak": 0.0,
            "wrist_speed_mean": 0.0,
            "pose_energy": 0.0,
            "arm_extension_peak": arm_extension_peak,
            "stance_variability": stance_variability,
            "swing_count": 0,
            "samples": [],
        }

    # Smooth wrist speeds
    smoothed_wrist = _moving_average(wrist_speeds, smoothing_window)
    wrist_speed_peak = round(float(max(smoothed_wrist)), 4)
    wrist_speed_mean = round(float(np.mean(smoothed_wrist)), 4)

    # Pose energy
    pose_energy = round(float(np.mean(all_kp_speeds)), 4) if all_kp_speeds else 0.0

    # Swing count: normalized wrist_speed > threshold
    swing_count = 0
    in_swing = False
    for v in smoothed_wrist:
        if v > swing_speed_threshold:
            if not in_swing:
                swing_count += 1
                in_swing = True
        else:
            in_swing = False

    # Build per-frame time-series samples (for inspector / calibration)
    time_series = []
    if save_samples:
        for i, t in enumerate(frame_times[:max_samples]):
            time_series.append({
                "t": round(t, 3),
                "wrist_speed": round(smoothed_wrist[i], 4),
                "arm_extension": round(frame_arm_extensions[i], 4),
                "pose_energy": round(all_kp_speeds[i], 4),
            })

    return {
        "pose_confidence": pose_confidence,
        "wrist_speed_peak": wrist_speed_peak,
        "wrist_speed_mean": wrist_speed_mean,
        "pose_energy": pose_energy,
        "arm_extension_peak": arm_extension_peak,
        "stance_variability": stance_variability,
        "swing_count": swing_count,
        "samples": time_series,
    }


def _moving_average(data: list[float], window: int = 3) -> list[float]:
    """Simple moving average smoothing."""
    if len(data) <= window:
        return list(data)
    result = []
    for i in range(len(data)):
        start = max(0, i - window // 2)
        end = min(len(data), i + window // 2 + 1)
        result.append(sum(data[start:end]) / (end - start))
    return result


# ── Empty output ───────────────────────────────────────────────────────


def _write_empty(art: Path) -> None:
    """Write empty pose_estimation.json."""
    output = {
        "enabled": False,
        "model": "",
        "fps": 0,
        "zone_labels": [],
        "rallies": [],
    }
    with open(art / "pose_estimation.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)


# ── Debug visualization ───────────────────────────────────────────────


def _save_debug_skeletons(debug_samples: list[dict], dbg: Path) -> None:
    """Save skeleton overlay images for debug inspection."""
    out_dir = dbg / "pose_samples"
    out_dir.mkdir(parents=True, exist_ok=True)

    for sample in debug_samples:
        rid = sample["rid"]
        label = sample["label"]
        t = sample["t"]
        crop = sample["crop"].copy()
        kp = sample["kp"]  # (17, 3)

        # Draw skeleton edges
        for i1, i2 in SKELETON_EDGES:
            if kp[i1][2] > KP_CONF_THRESHOLD and kp[i2][2] > KP_CONF_THRESHOLD:
                pt1 = (int(kp[i1][0]), int(kp[i1][1]))
                pt2 = (int(kp[i2][0]), int(kp[i2][1]))
                cv2.line(crop, pt1, pt2, (0, 255, 0), 2)

        # Draw keypoints
        for ki in range(NUM_KEYPOINTS):
            conf = kp[ki][2]
            if conf < 0.1:
                continue
            pt = (int(kp[ki][0]), int(kp[ki][1]))
            if conf > 0.5:
                color = (0, 255, 0)  # green
            elif conf > KP_CONF_THRESHOLD:
                color = (0, 255, 255)  # yellow
            else:
                color = (0, 0, 255)  # red
            cv2.circle(crop, pt, 4, color, -1)

        fname = f"rally_{rid}_zone_{label}_t{t:.1f}.png"
        cv2.imwrite(str(out_dir / fname), crop)


def _plot_pose_summary(
    rally_results: list[dict],
    zone_labels: list[str],
    swing_threshold: float,
    dbg: Path,
) -> None:
    """Generate a summary plot of wrist speed time series (placeholder).

    Since we don't store per-frame time series in output (file size),
    this plots a bar chart of key features per rally.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    n_rallies = min(len(rally_results), 8)
    if n_rallies == 0:
        return

    fig, axes = plt.subplots(
        n_rallies, 1,
        figsize=(14, 3 * n_rallies),
        squeeze=False,
    )

    colors = {"near": "blue", "far": "red"}
    feature_names = [
        "wrist_speed_peak", "wrist_speed_mean", "pose_energy",
        "arm_extension_peak", "stance_variability",
    ]
    x_pos = np.arange(len(feature_names))
    bar_width = 0.35

    for idx, rr in enumerate(rally_results[:n_rallies]):
        ax = axes[idx, 0]
        for zi, label in enumerate(zone_labels):
            zone_data = rr["zones"].get(label, {})
            vals = [zone_data.get(fn, 0.0) for fn in feature_names]
            offset = -bar_width / 2 + zi * bar_width
            ax.bar(
                x_pos + offset, vals, bar_width,
                label=label, color=colors.get(label, "green"), alpha=0.7,
            )

        ax.set_xticks(x_pos)
        ax.set_xticklabels(feature_names, rotation=30, ha="right", fontsize=8)
        asymm = rr.get("pose_asymmetry", 0.0)
        swings = rr.get("swing_count_diff", 0.0)
        ax.set_title(
            f"Rally {rr['rally_id']} "
            f"(asymm={asymm:.2f}, swing_diff={swings:.0f})",
            fontsize=10,
        )
        ax.legend(loc="upper right", fontsize=8)

    plt.tight_layout()
    plt.savefig(str(dbg / "pose_summary.png"), dpi=100)
    plt.close(fig)
