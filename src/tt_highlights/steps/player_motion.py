"""Step: player_motion – compute per-player motion intensity using zone crops.

Requires:
  - proxy.mp4 (from preprocess)
  - player_zones.json (from setup, opt-in)

Optional:
  - rallies.json (from rally_segment) — if present, also produces per-rally summaries

Produces:
  - player_motion.json with full-video time-series samples + optional per-rally data

Motion is computed in original frame coordinates (NOT warped) by cropping
each player's zone and computing frame-differencing within that crop.
"""

import json
import logging
from pathlib import Path

import cv2
import numpy as np

from ..job import artifacts_dir, debug_dir

logger = logging.getLogger(__name__)


def run(job: dict, config: dict, job_path: str) -> None:
    """Execute the player_motion step."""
    pm_cfg = config.get("player_motion", {})
    if not pm_cfg.get("enabled", False):
        logger.info("player_motion disabled — skipping.")
        return

    art = artifacts_dir(job_path)
    dbg = debug_dir(job_path)
    dbg.mkdir(parents=True, exist_ok=True)

    proxy_path = art / "proxy.mp4"
    zones_path = art / "player_zones.json"

    if not proxy_path.exists():
        logger.warning("proxy.mp4 not found — skipping player_motion.")
        return
    if not zones_path.exists():
        logger.warning("player_zones.json not found — skipping player_motion.")
        _write_empty(art)
        return

    with open(zones_path, "r", encoding="utf-8") as f:
        zones_data = json.load(f)

    from ..job import proxy_scale, scale_zones
    sx, sy = proxy_scale(job_path)
    zones = scale_zones(zones_data.get("zones", []), sx, sy)
    if not zones:
        logger.warning("No player zones defined — skipping player_motion.")
        _write_empty(art)
        return

    # rallies.json is optional — used for backward-compat per-rally summaries
    rallies_path = art / "rallies.json"
    rallies = []
    if rallies_path.exists():
        with open(rallies_path, "r", encoding="utf-8") as f:
            rallies_data = json.load(f)
        rallies = rallies_data.get("rallies", [])

    sample_fps = pm_cfg.get("sample_fps", 10)
    smoothing_window = pm_cfg.get("smoothing_window", 5)

    # Open video
    cap = cv2.VideoCapture(str(proxy_path))
    video_fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if video_fps <= 0:
        video_fps = 30.0

    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_interval = max(1, int(round(video_fps / sample_fps)))
    actual_fps = video_fps / frame_interval
    duration = total_frames / video_fps

    zone_labels = [z.get("label", f"zone_{i}") for i, z in enumerate(zones)]

    logger.info(
        f"player_motion: {len(zones)} zones, full-video scan, "
        f"~{duration:.1f}s, sample_interval={frame_interval}"
    )

    # Precompute zone rects
    zone_rects = []
    for zone in zones:
        r = zone["rect"]
        x1 = max(0, r["x"])
        y1 = max(0, r["y"])
        x2 = min(frame_w, r["x"] + r["w"])
        y2 = min(frame_h, r["y"] + r["h"])
        zone_rects.append((x1, y1, x2, y2))

    # ── Full-video scan ───────────────────────────────────────────────────
    prev_grays = [None] * len(zones)
    samples = []  # list of {t, <zone_label>: activity, ...}
    frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % frame_interval == 0:
            t = frame_idx / video_fps
            gray_full = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            sample = {"t": round(t, 3)}
            for zi, (x1, y1, x2, y2) in enumerate(zone_rects):
                crop = gray_full[y1:y2, x1:x2]
                label = zone_labels[zi]
                if prev_grays[zi] is not None:
                    diff = cv2.absdiff(crop, prev_grays[zi])
                    activity = float(diff.mean())
                    sample[label] = round(activity, 4)
                else:
                    sample[label] = 0.0
                prev_grays[zi] = crop.copy()

            samples.append(sample)

        frame_idx += 1

    cap.release()

    # ── Per-rally summaries (backward compat, only if rallies.json exists) ─
    rally_results = []
    if rallies:
        rally_ranges = [
            (r["id"], r["start"], r.get("end_refined", r["end"]))
            for r in rallies
        ]
        rally_results = _compute_per_rally(
            samples, rally_ranges, zones, zone_labels, smoothing_window,
        )

    output = {
        "enabled": True,
        "fps": round(actual_fps, 2),
        "zone_labels": zone_labels,
        "samples": samples,
        "rallies": rally_results,
    }
    with open(art / "player_motion.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    logger.info(
        f"Player motion computed: {len(samples)} samples, "
        f"{len(rally_results)} rally summaries."
    )

    # Debug plot
    if rally_results:
        _plot_motion_summary(rally_results, zones, dbg / "player_motion_summary.png")


def _compute_per_rally(
    samples: list[dict],
    rally_ranges: list[tuple],
    zones: list[dict],
    zone_labels: list[str],
    smoothing_window: int,
) -> list[dict]:
    """Compute per-rally motion summaries from full-video samples."""
    rally_results = []

    for rid, rstart, rend in rally_ranges:
        # Filter samples in rally time window
        rally_samples = [s for s in samples if rstart <= s["t"] <= rend]

        zone_results = {}
        for zi, label in enumerate(zone_labels):
            raw_vals = [s.get(label, 0.0) for s in rally_samples]
            if not raw_vals:
                zone_results[label] = {
                    "samples": [],
                    "raw_mean": 0.0,
                    "raw_peak": 0.0,
                    "raw_total": 0.0,
                    "raw_end_burst": 0.0,
                    "mean": 0.0,
                    "peak": 0.0,
                    "total": 0.0,
                }
                continue

            smoothed_raw = _moving_average(raw_vals, smoothing_window)

            raw_mean = float(np.mean(smoothed_raw))
            raw_peak = float(np.max(smoothed_raw))
            raw_total = float(np.sum(smoothed_raw))

            n = len(smoothed_raw)
            tail_start = max(0, n - max(1, n // 5))
            raw_end_burst = float(np.mean(smoothed_raw[tail_start:]))

            max_val = max(smoothed_raw) if smoothed_raw else 1.0
            if max_val > 0:
                norm_vals = [round(v / max_val, 4) for v in smoothed_raw]
            else:
                norm_vals = [0.0] * len(smoothed_raw)

            norm_mean = float(np.mean(norm_vals))

            zone_results[label] = {
                "samples": [
                    {"t": s["t"], "activity": nv}
                    for s, nv in zip(rally_samples, norm_vals)
                ],
                "raw_mean": round(raw_mean, 4),
                "raw_peak": round(raw_peak, 4),
                "raw_total": round(raw_total, 4),
                "raw_end_burst": round(raw_end_burst, 4),
                "mean": round(norm_mean, 4),
                "peak": round(float(max(norm_vals)), 4),
                "total": round(float(sum(norm_vals)), 4),
            }

        # Inter-player features
        motion_asymmetry = 0.0
        end_burst_asymmetry = 0.0

        if len(zone_labels) == 2:
            m1 = zone_results[zone_labels[0]]["raw_mean"]
            m2 = zone_results[zone_labels[1]]["raw_mean"]
            denom = max(m1 + m2, 1e-6)
            motion_asymmetry = round(abs(m1 - m2) / denom, 4)

            eb1 = zone_results[zone_labels[0]]["raw_end_burst"]
            eb2 = zone_results[zone_labels[1]]["raw_end_burst"]
            eb_denom = max(eb1 + eb2, 1e-6)
            end_burst_asymmetry = round(abs(eb1 - eb2) / eb_denom, 4)

        rally_results.append({
            "rally_id": rid,
            "zones": zone_results,
            "motion_asymmetry": motion_asymmetry,
            "end_burst_asymmetry": end_burst_asymmetry,
        })

    return rally_results


def _moving_average(data: list[float], window: int = 5) -> list[float]:
    """Simple moving average smoothing."""
    if len(data) <= window:
        return list(data)
    result = []
    for i in range(len(data)):
        start = max(0, i - window // 2)
        end = min(len(data), i + window // 2 + 1)
        result.append(sum(data[start:end]) / (end - start))
    return result


def _write_empty(art: Path) -> None:
    """Write empty player_motion.json."""
    output = {
        "enabled": False,
        "fps": 0,
        "zone_labels": [],
        "samples": [],
        "rallies": [],
    }
    with open(art / "player_motion.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)


def _plot_motion_summary(
    rally_results: list[dict], zones: list[dict], out_path: Path,
) -> None:
    """Generate a summary plot of per-zone motion across rallies."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    zone_labels = [z.get("label", f"zone_{i}") for i, z in enumerate(zones)]
    if not zone_labels:
        return

    fig, axes = plt.subplots(
        min(len(rally_results), 6), 1,
        figsize=(14, 3 * min(len(rally_results), 6)),
        squeeze=False,
    )

    colors = {"near": "blue", "far": "red"}
    for idx, rr in enumerate(rally_results[:6]):
        ax = axes[idx, 0]
        for zl in zone_labels:
            samples = rr["zones"].get(zl, {}).get("samples", [])
            if samples:
                ts = [s["t"] for s in samples]
                vs = [s["activity"] for s in samples]
                ax.plot(ts, vs, label=zl, color=colors.get(zl, "green"), alpha=0.8)
        ax.set_title(f"Rally {rr['rally_id']} (asymm={rr['motion_asymmetry']:.2f})")
        ax.set_ylim(0, 1.05)
        ax.legend(loc="upper right", fontsize=8)

    plt.tight_layout()
    plt.savefig(str(out_path), dpi=100)
    plt.close(fig)
