"""Inspector — pure data loading, aggregation, frame extraction, and freshness checks.

No Streamlit imports. Used by app.py for Debug Panel, Rally Inspector, and Calibration Mode.
"""

import json
import logging
from enum import Enum
from pathlib import Path

from .job import artifacts_dir, debug_dir
from .config import load_config

logger = logging.getLogger(__name__)


# ── Status enums ──────────────────────────────────────────────────────────────


class PoseStatus(Enum):
    OFF = "off"                 # config에서 disabled
    UNAVAILABLE = "unavailable" # enabled이나 output 없음
    ACTIVE = "active"           # 해당 rally에 zone feature 존재
    FALLBACK = "fallback"       # step은 돌았으나 이 rally에 유효 데이터 없음


class OcrStatus(Enum):
    UNAVAILABLE = "unavailable"
    NO_CHANGE = "no_change"
    LEFT_SCORED = "left_scored"
    RIGHT_SCORED = "right_scored"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _load_optional(path: Path) -> dict | None:
    """Load a JSON file if it exists, else return None."""
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _mtime(path: Path) -> float | None:
    """Return file mtime or None if missing."""
    if path.exists():
        return path.stat().st_mtime
    return None


# ── Artifact Freshness ────────────────────────────────────────────────────────


def check_artifact_freshness(job_path: str | Path) -> dict:
    """Check modification timestamps of all inspector-relevant artifacts.

    Uses rallies.json mtime as the base timestamp. Downstream artifacts
    older than rallies.json are flagged as stale.
    """
    art = artifacts_dir(job_path)
    base_path = art / "rallies.json"
    base_ts = _mtime(base_path)

    artifact_names = {
        "features": art / "features.json",
        "scores": art / "scores.json",
        "player_motion": art / "player_motion.json",
        "pose_estimation": art / "pose_estimation.json",
        "ocr_events": art / "ocr_events.json",
    }

    artifacts = {}
    warnings = []
    for name, path in artifact_names.items():
        ts = _mtime(path)
        exists = path.exists()
        stale = False
        if exists and base_ts is not None and ts is not None:
            stale = ts < base_ts
        artifacts[name] = {"exists": exists, "ts": ts, "stale": stale}

        if not exists:
            warnings.append(f"{name}.json not found — run Full Analysis to generate.")
        elif stale:
            warnings.append(
                f"{name}.json is older than rallies.json — may be stale. "
                "Re-run Full Analysis."
            )

    return {
        "base_ts": base_ts,
        "artifacts": artifacts,
        "warnings": warnings,
    }


# ── Rally Inspector ───────────────────────────────────────────────────────────


def load_rally_inspector(job_path: str | Path, rally_id: int) -> dict:
    """Load and aggregate all data for one rally for the inspector panel.

    Returns a rich dict with summary, motion, pose, ocr, scores, features,
    events, freshness, and status_messages.
    """
    art = artifacts_dir(job_path)
    config_path = Path(job_path).parent / "config.yaml"
    config = load_config(str(config_path)) if config_path.exists() else {}
    pose_cfg = config.get("pose_estimation", {})

    # Load artifacts
    rallies_data = _load_optional(art / "rallies.json")
    features_data = _load_optional(art / "features.json")
    scores_data = _load_optional(art / "scores.json")
    motion_data = _load_optional(art / "player_motion.json")
    pose_data = _load_optional(art / "pose_estimation.json")
    ocr_data = _load_optional(art / "ocr_events.json")
    audio_data = _load_optional(art / "audio_events.json")
    activity_data = _load_optional(art / "activity.json")

    freshness = check_artifact_freshness(job_path)
    status_messages = list(freshness["warnings"])

    # Find rally info
    rally_info = None
    if rallies_data:
        for r in rallies_data.get("rallies", []):
            if r["id"] == rally_id:
                rally_info = r
                break

    if rally_info is None:
        return {
            "summary": None, "motion": None, "pose": None, "ocr": None,
            "scores": None, "features": None, "events": None,
            "freshness": freshness, "status_messages": ["Rally not found in rallies.json"],
        }

    start = rally_info["start"]
    end = rally_info.get("end_refined", rally_info["end"])
    duration = end - start

    # ── Summary ──
    # Audio events in rally window
    impacts = []
    if audio_data:
        for imp in audio_data.get("impact_events", []):
            if start <= imp["t"] <= end:
                impacts.append(imp)

    impact_count = len(impacts)
    impact_rate = impact_count / max(duration, 0.1)
    impact_peak = max((imp["score"] for imp in impacts), default=0.0)

    # Activity in rally window
    activity_mean = 0.0
    activity_peak = 0.0
    if activity_data:
        act_samples = activity_data.get("samples", [])
        rally_acts = [s["activity"] for s in act_samples if start <= s["t"] <= end]
        if rally_acts:
            activity_mean = sum(rally_acts) / len(rally_acts)
            activity_peak = max(rally_acts)

    segment_score = rally_info.get("segment_score")
    segment_flags = rally_info.get("segment_flags", [])
    conf_audio = rally_info.get("conf_audio")
    conf_video = rally_info.get("conf_video")

    summary = {
        "rally_id": rally_id,
        "start": start, "end": end, "duration": round(duration, 2),
        "impact_count": impact_count,
        "impact_rate": round(impact_rate, 2),
        "impact_peak": round(impact_peak, 4),
        "activity_mean": round(activity_mean, 4),
        "activity_peak": round(activity_peak, 4),
        "segment_score": segment_score,
        "segment_flags": segment_flags,
        "conf_audio": conf_audio,
        "conf_video": conf_video,
    }

    # ── Motion ──
    motion = None
    if motion_data and motion_data.get("enabled"):
        motion_rally = None
        for mr in motion_data.get("rallies", []):
            if mr["rally_id"] == rally_id:
                motion_rally = mr
                break
        if motion_rally:
            zones = motion_rally.get("zones", {})
            near_z = zones.get("near", {})
            far_z = zones.get("far", {})
            motion = {
                "near": {
                    "raw_mean": near_z.get("raw_mean", 0.0),
                    "raw_peak": near_z.get("raw_peak", 0.0),
                    "raw_end_burst": near_z.get("raw_end_burst", 0.0),
                },
                "far": {
                    "raw_mean": far_z.get("raw_mean", 0.0),
                    "raw_peak": far_z.get("raw_peak", 0.0),
                    "raw_end_burst": far_z.get("raw_end_burst", 0.0),
                },
                "asymmetry": motion_rally.get("motion_asymmetry", 0.0),
                "end_burst_asymmetry": motion_rally.get("end_burst_asymmetry", 0.0),
            }
        else:
            status_messages.append("Player motion: no data for this rally")
    else:
        if motion_data is None:
            status_messages.append("Player motion step not run — run Full Analysis")
        else:
            status_messages.append("Player motion disabled in config")

    # ── Pose ──
    pose = None
    pose_status = PoseStatus.OFF
    if not pose_cfg.get("enabled", False):
        pose_status = PoseStatus.OFF
        status_messages.append("Pose disabled in config")
    elif pose_data is None or not pose_data.get("enabled"):
        pose_status = PoseStatus.UNAVAILABLE
        status_messages.append("Pose step not run — run Full Analysis")
    else:
        pose_rally = None
        for pr in pose_data.get("rallies", []):
            if pr["rally_id"] == rally_id:
                pose_rally = pr
                break

        if pose_rally:
            zones = pose_rally.get("zones", {})
            near_z = zones.get("near", {})
            far_z = zones.get("far", {})

            # valid_frame_ratio: actual samples vs expected
            pose_fps = pose_data.get("fps", 5)

            def _valid_ratio(zone_data):
                expected = duration * pose_fps
                actual = len(zone_data.get("samples", []))
                return actual / max(expected, 1)

            near_ratio = _valid_ratio(near_z)
            far_ratio = _valid_ratio(far_z)

            # Check if any real data exists
            has_data = (near_z.get("pose_confidence", 0) > 0
                        or far_z.get("pose_confidence", 0) > 0)
            if has_data:
                pose_status = PoseStatus.ACTIVE
            else:
                pose_status = PoseStatus.FALLBACK
                status_messages.append("Pose: no valid detections for this rally")

            if near_ratio < 0.3:
                status_messages.append(
                    f"Pose: low valid frame ratio near ({near_ratio:.0%}) "
                    "— skeleton detection unreliable for this zone"
                )
            if far_ratio < 0.3:
                status_messages.append(
                    f"Pose: low valid frame ratio far ({far_ratio:.0%}) "
                    "— skeleton detection unreliable for this zone"
                )

            def _zone_pose(zd, ratio):
                return {
                    "wrist_speed_peak": zd.get("wrist_speed_peak", 0.0),
                    "wrist_speed_mean": zd.get("wrist_speed_mean", 0.0),
                    "arm_extension_peak": zd.get("arm_extension_peak", 0.0),
                    "pose_confidence": zd.get("pose_confidence", 0.0),
                    "pose_energy": zd.get("pose_energy", 0.0),
                    "stance_variability": zd.get("stance_variability", 0.0),
                    "valid_frame_ratio": round(ratio, 4),
                }

            pose = {
                "status": pose_status,
                "near": _zone_pose(near_z, near_ratio),
                "far": _zone_pose(far_z, far_ratio),
                "asymmetry": pose_rally.get("pose_asymmetry", 0.0),
                "swing_count_diff": pose_rally.get("swing_count_diff", 0.0),
            }
        else:
            pose_status = PoseStatus.FALLBACK
            status_messages.append("Pose: no data for this rally")

    # ── OCR ──
    ocr = {"status": OcrStatus.UNAVAILABLE, "event": None}
    if ocr_data:
        ocr_events = ocr_data.get("events", [])
        rally_ocr = [e for e in ocr_events if start <= e.get("t", 0) <= end]
        if rally_ocr:
            ev = rally_ocr[0]  # first event in rally window
            # delta is [left_delta, right_delta] list from scoreboard_ocr
            delta = ev.get("delta", [0, 0])
            if isinstance(delta, list) and len(delta) >= 2:
                left_d, right_d = delta[0], delta[1]
            else:
                left_d, right_d = 0, 0
            if left_d > 0:
                ocr["status"] = OcrStatus.LEFT_SCORED
            elif right_d > 0:
                ocr["status"] = OcrStatus.RIGHT_SCORED
            else:
                ocr["status"] = OcrStatus.NO_CHANGE
            ocr["event"] = ev
        else:
            ocr["status"] = OcrStatus.NO_CHANGE
    else:
        status_messages.append("OCR unavailable")

    # ── Scores ──
    scores = None
    if scores_data:
        candidates = scores_data.get("candidates", {})
        categories = {}
        top_score = -1
        top_category = ""
        all_reasons = []
        for cat, entries in candidates.items():
            for entry in entries:
                if entry["rally_id"] == rally_id:
                    categories[cat] = {
                        "score": entry["score"],
                        "reasons": entry.get("reasons", []),
                    }
                    if entry["score"] > top_score:
                        top_score = entry["score"]
                        top_category = cat
                        all_reasons = [
                            r.get("feature", "")
                            for r in entry.get("reasons", [])[:3]
                        ]
                    break
        if categories:
            scores = {
                "categories": categories,
                "top_category": top_category,
                "top_reasons": all_reasons,
            }
    else:
        status_messages.append("scores.json not found — run Full Analysis")

    # Check if scoring uses zero pose weights
    scoring_cfg = config.get("scoring", {}).get("weights", {})
    all_pose_zero = True
    for cat_weights in scoring_cfg.values():
        if isinstance(cat_weights, dict):
            for k, v in cat_weights.items():
                if "wrist" in k or "arm_extension" in k or "pose_asymmetry" in k:
                    if v > 0:
                        all_pose_zero = False
    if all_pose_zero and pose_status == PoseStatus.ACTIVE:
        status_messages.append("Scoring uses no pose weights yet (all 0.0)")

    # ── Features ──
    features = None
    if features_data:
        for feat in features_data.get("rally_features", []):
            if feat.get("rally_id") == rally_id:
                features = {
                    "raw": feat.get("raw", {}),
                    "norm": feat.get("norm", {}),
                }
                break
        if features is None:
            status_messages.append("features.json has no data for this rally")
    else:
        status_messages.append("features.json not found — run Full Analysis")

    # ── Events (for timeline) ──
    cheers = []
    if audio_data:
        for ch in audio_data.get("cheer_segments", []):
            # Include cheers that overlap with rally window
            if ch.get("end", 0) >= start and ch.get("start", 0) <= end:
                cheers.append({"start": ch["start"], "end": ch["end"],
                               "score": ch.get("score", 0)})

    ocr_events_timeline = []
    if ocr_data:
        for ev in ocr_data.get("events", []):
            if start <= ev.get("t", 0) <= end:
                ocr_events_timeline.append({"t": ev["t"], "delta": ev.get("delta", [0, 0])})

    events = {
        "impacts": [{"t": imp["t"], "score": imp["score"]} for imp in impacts],
        "cheers": cheers,
        "ocr": ocr_events_timeline,
    }

    return {
        "summary": summary,
        "motion": motion,
        "pose": pose,
        "ocr": ocr,
        "scores": scores,
        "features": features,
        "events": events,
        "freshness": freshness,
        "status_messages": status_messages,
    }


# ── Calibration Series ────────────────────────────────────────────────────────


def build_calibration_series(job_path: str | Path, rally_id: int) -> dict:
    """Build time-series data for the calibration plot.

    Returns activity, motion, pose curves, impacts, cheers, OCR event,
    rally window, pose valid ratios, and current parameter values.
    """
    art = artifacts_dir(job_path)
    config_path = Path(job_path).parent / "config.yaml"
    config = load_config(str(config_path)) if config_path.exists() else {}

    # Find rally window
    rallies_data = _load_optional(art / "rallies.json")
    rally_start = 0.0
    rally_end = 0.0
    if rallies_data:
        for r in rallies_data.get("rallies", []):
            if r["id"] == rally_id:
                rally_start = r["start"]
                rally_end = r.get("end_refined", r["end"])
                break

    # Activity samples in rally window
    activity_series = []
    activity_data = _load_optional(art / "activity.json")
    if activity_data:
        for s in activity_data.get("samples", []):
            if rally_start <= s["t"] <= rally_end:
                activity_series.append({"t": s["t"], "value": s["activity"]})

    # Motion samples
    near_motion = []
    far_motion = []
    motion_data = _load_optional(art / "player_motion.json")
    if motion_data and motion_data.get("enabled"):
        for mr in motion_data.get("rallies", []):
            if mr["rally_id"] == rally_id:
                zones = mr.get("zones", {})
                for s in zones.get("near", {}).get("samples", []):
                    near_motion.append({"t": s["t"], "value": s["activity"]})
                for s in zones.get("far", {}).get("samples", []):
                    far_motion.append({"t": s["t"], "value": s["activity"]})
                break

    # Parameter readback (needed before pose section)
    pz_cfg = config.get("player_zones", {})
    pm_cfg = config.get("player_motion", {})
    pe_cfg = config.get("pose_estimation", {})
    seg_cfg = config.get("segmentation", {})

    # Pose samples
    near_pose = []
    far_pose = []
    near_pose_valid_ratio = 0.0
    far_pose_valid_ratio = 0.0
    pose_sample_fps = pe_cfg.get("sample_fps", 5)
    pose_data = _load_optional(art / "pose_estimation.json")
    if pose_data and pose_data.get("enabled"):
        pose_fps = pose_data.get("fps", 5)
        pose_sample_fps = pose_fps  # actual fps from output (may differ from config)
        duration = rally_end - rally_start
        expected_frames = duration * pose_fps
        for pr in pose_data.get("rallies", []):
            if pr["rally_id"] == rally_id:
                zones = pr.get("zones", {})
                near_samples = zones.get("near", {}).get("samples", [])
                far_samples = zones.get("far", {}).get("samples", [])
                for s in near_samples:
                    near_pose.append({
                        "t": s["t"],
                        "wrist_speed": s.get("wrist_speed", 0),
                        "arm_extension": s.get("arm_extension", 0),
                        "pose_energy": s.get("pose_energy", 0),
                    })
                for s in far_samples:
                    far_pose.append({
                        "t": s["t"],
                        "wrist_speed": s.get("wrist_speed", 0),
                        "arm_extension": s.get("arm_extension", 0),
                        "pose_energy": s.get("pose_energy", 0),
                    })
                near_pose_valid_ratio = len(near_samples) / max(expected_frames, 1)
                far_pose_valid_ratio = len(far_samples) / max(expected_frames, 1)
                break

    # Impact events in rally window
    impacts = []
    audio_data = _load_optional(art / "audio_events.json")
    if audio_data:
        for imp in audio_data.get("impact_events", []):
            if rally_start <= imp["t"] <= rally_end:
                impacts.append({"t": imp["t"], "score": imp["score"]})

    # Cheer segments overlapping rally
    cheers = []
    if audio_data:
        for ch in audio_data.get("cheer_segments", []):
            if ch.get("end", 0) >= rally_start and ch.get("start", 0) <= rally_end:
                cheers.append({"start": ch["start"], "end": ch["end"],
                               "score": ch.get("score", 0)})

    # OCR event
    ocr_event = None
    ocr_data = _load_optional(art / "ocr_events.json")
    if ocr_data:
        for ev in ocr_data.get("events", []):
            if rally_start <= ev.get("t", 0) <= rally_end:
                ocr_event = {"t": ev["t"], "delta": ev.get("delta", [0, 0])}
                break

    # Parameter readback
    params = {
        "player_zones.margin_px": pz_cfg.get("margin_px", 150),
        "player_motion.sample_fps": pm_cfg.get("sample_fps", 10),
        "player_motion.smoothing_window": pm_cfg.get("smoothing_window", 5),
        "pose_estimation.sample_fps": pe_cfg.get("sample_fps", 5),
        "pose_estimation.conf_threshold": pe_cfg.get("conf_threshold", 0.5),
        "pose_estimation.swing_speed_threshold": pe_cfg.get("swing_speed_threshold", 0.8),
        "segmentation.require_video_confirmation": seg_cfg.get("require_video_confirmation", False),
    }

    return {
        "activity": activity_series,
        "near_motion": near_motion,
        "far_motion": far_motion,
        "near_pose": near_pose,
        "far_pose": far_pose,
        "near_pose_valid_ratio": round(near_pose_valid_ratio, 4),
        "far_pose_valid_ratio": round(far_pose_valid_ratio, 4),
        "pose_sample_fps": pose_sample_fps,
        "impacts": impacts,
        "cheers": cheers,
        "ocr_event": ocr_event,
        "rally_window": {"start": rally_start, "end": rally_end},
        "params": params,
    }


# ── Pose Debug Samples ────────────────────────────────────────────────────────


def list_pose_debug_samples(job_path: str | Path, rally_id: int) -> list[dict]:
    """List available pose skeleton debug images for a rally.

    Checks pose_estimation.json debug_samples first, falls back to file glob.
    Returns [{"zone_label": str, "t": float, "path": Path}].
    """
    art = artifacts_dir(job_path)
    dbg = debug_dir(job_path)

    # Try metadata from pose_estimation.json first
    pose_data = _load_optional(art / "pose_estimation.json")
    if pose_data:
        for pr in pose_data.get("rallies", []):
            if pr["rally_id"] == rally_id:
                ds = pr.get("debug_samples", [])
                if ds:
                    result = []
                    for d in ds:
                        p = Path(job_path).parent / d["path"]
                        if p.exists():
                            result.append({
                                "zone_label": d["zone_label"],
                                "t": d["t"],
                                "path": p,
                            })
                    if result:
                        return result
                break

    # Fallback: glob debug directory
    sample_dir = dbg / "pose_samples"
    if not sample_dir.exists():
        return []

    results = []
    pattern = f"rally_{rally_id}_zone_*.png"
    for p in sorted(sample_dir.glob(pattern)):
        # Parse filename: rally_{id}_zone_{label}_t{time}.png
        name = p.stem  # rally_1_zone_near_t12.3
        parts = name.split("_zone_")
        if len(parts) == 2:
            rest = parts[1]  # near_t12.3
            t_idx = rest.rfind("_t")
            if t_idx >= 0:
                zone_label = rest[:t_idx]
                try:
                    t = float(rest[t_idx + 2:])
                except ValueError:
                    t = 0.0
                results.append({"zone_label": zone_label, "t": t, "path": p})
    return results


# ── Frame Extraction ──────────────────────────────────────────────────────────


def _geometry_hash(zones, table_polygon, scoreboard_rect, has_skeletons=False) -> str:
    """Short hash of overlay geometry for cache invalidation."""
    import hashlib
    raw = json.dumps([zones, table_polygon, scoreboard_rect, has_skeletons],
                     sort_keys=True, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()[:8]


def _find_nearest_skeleton(
    skeleton_samples: list[dict], zone_label: str, t: float, max_dt: float = 2.0,
) -> Path | None:
    """Find the skeleton debug PNG closest in time for a zone label."""
    best_path = None
    best_dt = max_dt
    for s in skeleton_samples:
        if s["zone_label"] != zone_label:
            continue
        dt = abs(s["t"] - t)
        if dt < best_dt:
            best_dt = dt
            best_path = s["path"]
    return best_path


def extract_rally_frames(
    proxy_path: Path,
    rally_start: float,
    rally_end: float,
    rally_id: int,
    count: int,
    cache_dir: Path,
    zones: list[dict] | None = None,
    table_polygon: list | None = None,
    scoreboard_rect: dict | None = None,
    skeleton_samples: list[dict] | None = None,
) -> list[dict]:
    """Extract representative frames from the proxy video for a rally.

    Frames are cached as JPEG in cache_dir. The cache key includes a hash
    of the overlay geometry so that zone/ROI changes invalidate stale frames.

    When skeleton_samples is provided, nearest-time skeleton crops are
    composited as insets in the bottom-right corner of each zone rect,
    so zone and pose fitness can be verified in a single image.

    Returns [{"t": float, "path": Path}].
    """
    import cv2
    import numpy as np

    if not proxy_path.exists():
        return []

    duration = rally_end - rally_start
    if duration <= 0:
        return []

    cache_dir.mkdir(parents=True, exist_ok=True)

    # Include geometry hash in cache filename so ROI/zone edits invalidate
    geo_hash = _geometry_hash(zones, table_polygon, scoreboard_rect,
                              has_skeletons=bool(skeleton_samples))

    # Compute timestamps
    if count == 3:
        fractions = [0.15, 0.50, 0.85]
    elif count == 5:
        fractions = [0.15, 0.35, 0.55, 0.75, 0.90]
    else:
        fractions = [i / max(count - 1, 1) for i in range(count)]
        # Clamp to avoid exact start/end
        fractions = [max(0.1, min(0.9, f)) for f in fractions]

    timestamps = [rally_start + f * duration for f in fractions]

    results = []
    cap = cv2.VideoCapture(str(proxy_path))
    if not cap.isOpened():
        return []

    # Compute scale factor: ROI/zone coords are in original resolution,
    # but we draw on the proxy (which may be smaller).
    proxy_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    proxy_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # Read original resolution from video_meta.json
    meta_path = proxy_path.parent / "video_meta.json"
    orig_w, orig_h = proxy_w, proxy_h
    if meta_path.exists():
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        orig_w = meta.get("width", proxy_w)
        orig_h = meta.get("height", proxy_h)

    sx = proxy_w / max(orig_w, 1)
    sy = proxy_h / max(orig_h, 1)

    def _s(x, y):
        """Scale a point from source to proxy coords."""
        return int(x * sx), int(y * sy)

    try:
        for t in timestamps:
            cache_path = cache_dir / f"rally_{rally_id}_t{t:.1f}_{geo_hash}.jpg"

            # Use cache if exists
            if cache_path.exists():
                results.append({"t": round(t, 1), "path": cache_path})
                continue

            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ret, frame = cap.read()
            if not ret:
                continue

            # Draw overlays
            if zones:
                colors = {"near": (255, 0, 0), "far": (0, 0, 255)}
                for z in zones:
                    r = z.get("rect", {})
                    if r.get("w", 0) <= 0 or r.get("h", 0) <= 0:
                        continue
                    c = colors.get(z.get("label", ""), (0, 255, 0))
                    rx, ry = _s(r["x"], r["y"])
                    rw = int(r["w"] * sx)
                    rh = int(r["h"] * sy)

                    # Use polygon if available, else fall back to rect
                    zone_poly = z.get("polygon")
                    if zone_poly and len(zone_poly) >= 3:
                        scaled_zp = np.array(
                            [_s(pt[0], pt[1]) for pt in zone_poly],
                            dtype=np.int32,
                        )
                        overlay = frame.copy()
                        cv2.fillPoly(overlay, [scaled_zp], c)
                        frame = cv2.addWeighted(overlay, 0.15, frame, 0.85, 0)
                        cv2.polylines(frame, [scaled_zp], True, c, 2)
                    else:
                        overlay = frame.copy()
                        cv2.rectangle(overlay, (rx, ry), (rx + rw, ry + rh), c, -1)
                        frame = cv2.addWeighted(overlay, 0.15, frame, 0.85, 0)
                        cv2.rectangle(frame, (rx, ry), (rx + rw, ry + rh), c, 2)

                    # Composite nearest skeleton sample as inset
                    if skeleton_samples:
                        skel_path = _find_nearest_skeleton(
                            skeleton_samples, z.get("label", ""), t,
                        )
                        if skel_path and skel_path.exists():
                            skel_img = cv2.imread(str(skel_path))
                            if skel_img is not None:
                                # Scale skeleton crop to fit scaled zone height * 0.6
                                inset_h = max(int(rh * 0.6), 40)
                                s_h, s_w = skel_img.shape[:2]
                                if s_h > 0 and s_w > 0:
                                    scale = inset_h / s_h
                                    inset_w = int(s_w * scale)
                                    inset = cv2.resize(skel_img, (inset_w, inset_h))
                                    # Place at bottom-right of scaled zone rect
                                    iy = ry + rh - inset_h - 4
                                    ix = rx + rw - inset_w - 4
                                    iy = max(0, iy)
                                    ix = max(0, ix)
                                    # Bounds check
                                    fh, fw = frame.shape[:2]
                                    ey = min(iy + inset_h, fh)
                                    ex = min(ix + inset_w, fw)
                                    actual_h = ey - iy
                                    actual_w = ex - ix
                                    if actual_h > 0 and actual_w > 0:
                                        roi = frame[iy:ey, ix:ex]
                                        crop = inset[:actual_h, :actual_w]
                                        frame[iy:ey, ix:ex] = cv2.addWeighted(
                                            crop, 0.8, roi, 0.2, 0,
                                        )
                                        cv2.rectangle(frame, (ix, iy),
                                                      (ex - 1, ey - 1), c, 1)

            if table_polygon and len(table_polygon) >= 3:
                scaled_poly = [[_s(pt[0], pt[1])] for pt in table_polygon]
                pts = np.array(scaled_poly, dtype=np.int32)
                cv2.polylines(frame, [pts], True, (0, 200, 0), 2)

            if scoreboard_rect:
                sb_x, sb_y = _s(scoreboard_rect.get("x", 0),
                                scoreboard_rect.get("y", 0))
                sb_w = int(scoreboard_rect.get("w", 0) * sx)
                sb_h = int(scoreboard_rect.get("h", 0) * sy)
                if sb_w > 0 and sb_h > 0:
                    # Dashed line effect via short segments
                    for i in range(0, sb_w + sb_h, 8):
                        if i < sb_w:
                            pt1 = (sb_x + i, sb_y)
                            pt2 = (sb_x + min(i + 4, sb_w), sb_y)
                            cv2.line(frame, pt1, pt2, (200, 200, 0), 1)
                            pt1 = (sb_x + i, sb_y + sb_h)
                            pt2 = (sb_x + min(i + 4, sb_w), sb_y + sb_h)
                            cv2.line(frame, pt1, pt2, (200, 200, 0), 1)
                        if i < sb_h:
                            pt1 = (sb_x, sb_y + i)
                            pt2 = (sb_x, sb_y + min(i + 4, sb_h))
                            cv2.line(frame, pt1, pt2, (200, 200, 0), 1)
                            pt1 = (sb_x + sb_w, sb_y + i)
                            pt2 = (sb_x + sb_w, sb_y + min(i + 4, sb_h))
                            cv2.line(frame, pt1, pt2, (200, 200, 0), 1)

            cv2.imwrite(str(cache_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            results.append({"t": round(t, 1), "path": cache_path})
    finally:
        cap.release()

    return results
