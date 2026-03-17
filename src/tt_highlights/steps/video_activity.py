"""Step: video_activity – compute motion intensity curve in warped table coordinates."""

import json
import logging
from pathlib import Path

import cv2
import numpy as np

from ..job import artifacts_dir, debug_dir

logger = logging.getLogger(__name__)


def run(job: dict, config: dict, job_path: str) -> None:
    """Execute the video_activity step."""
    art = artifacts_dir(job_path)
    dbg = debug_dir(job_path)
    dbg.mkdir(parents=True, exist_ok=True)

    proxy_path = art / "proxy.mp4"
    roi_path = art / "table_roi.json"

    if not proxy_path.exists():
        raise FileNotFoundError("proxy.mp4 not found. Run preprocess first.")
    if not roi_path.exists():
        raise FileNotFoundError("table_roi.json not found. Run table_roi first.")

    with open(roi_path, "r", encoding="utf-8") as f:
        roi_data = json.load(f)

    polygon = np.array(roi_data["table_polygon"], dtype=np.float32)
    warp_w = config["video"]["warp_width"]
    warp_h = config["video"]["warp_height"]
    activity_fps = config["video"]["activity_fps"]

    # Compute homography from table polygon to rectangular warp target
    dst_pts = np.array([
        [0, 0], [warp_w, 0], [warp_w, warp_h], [0, warp_h]
    ], dtype=np.float32)
    H, _ = cv2.findHomography(polygon, dst_pts)

    # Open video
    cap = cv2.VideoCapture(str(proxy_path))
    video_fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    if video_fps <= 0:
        video_fps = 30.0

    # Compute frame sampling interval
    frame_interval = max(1, int(round(video_fps / activity_fps)))
    actual_fps = video_fps / frame_interval

    samples = []
    prev_gray = None
    frame_idx = 0
    raw_activities = []

    logger.info(f"Computing activity curve: video_fps={video_fps:.1f}, "
                f"sample_interval={frame_interval}, target_fps={activity_fps}")

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % frame_interval == 0:
            t = frame_idx / video_fps

            # Warp frame
            warped = cv2.warpPerspective(frame, H, (warp_w, warp_h))
            gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)

            if prev_gray is not None:
                diff = cv2.absdiff(gray, prev_gray)
                activity = float(diff.mean())
                raw_activities.append(activity)
                samples.append({"t": round(t, 3), "activity": activity})

            prev_gray = gray

        frame_idx += 1

    cap.release()

    if not raw_activities:
        logger.warning("No activity data computed.")
        _write_empty(art, activity_fps)
        return

    # Normalize to 0-1
    max_val = max(raw_activities) if raw_activities else 1.0
    if max_val > 0:
        for s in samples:
            s["activity"] = round(s["activity"] / max_val, 4)

    # Apply moving average smoothing (window=5)
    activities = [s["activity"] for s in samples]
    smoothed = _moving_average(activities, window=5)
    for i, s in enumerate(samples):
        s["activity"] = round(smoothed[i], 4)

    # Write output
    output = {
        "fps": round(actual_fps, 2),
        "samples": samples,
    }
    with open(art / "activity.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    logger.info(f"Activity computed: {len(samples)} samples")

    # Generate debug plot
    _plot_activity(samples, dbg / "activity_plot.png")


def _moving_average(data: list[float], window: int = 5) -> list[float]:
    """Simple moving average smoothing."""
    if len(data) <= window:
        return data
    result = []
    for i in range(len(data)):
        start = max(0, i - window // 2)
        end = min(len(data), i + window // 2 + 1)
        result.append(sum(data[start:end]) / (end - start))
    return result


def _write_empty(art: Path, fps: float) -> None:
    """Write empty activity.json."""
    output = {"fps": fps, "samples": []}
    with open(art / "activity.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)


def _plot_activity(samples: list[dict], out_path: Path) -> None:
    """Generate activity curve debug plot."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    times = [s["t"] for s in samples]
    values = [s["activity"] for s in samples]

    fig, ax = plt.subplots(figsize=(16, 4))
    ax.plot(times, values, color="blue", linewidth=0.8)
    ax.fill_between(times, values, alpha=0.3)
    ax.set_xlabel("Time (sec)")
    ax.set_ylabel("Activity (0-1)")
    ax.set_title("Video Activity Curve (warped table)")
    ax.set_ylim(0, 1.05)
    plt.tight_layout()
    plt.savefig(str(out_path), dpi=100)
    plt.close(fig)
