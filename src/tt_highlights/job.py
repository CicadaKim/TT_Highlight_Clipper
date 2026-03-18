"""Job creation and loading utilities."""

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import load_default_config
from .recent import add_recent_job

import yaml


def create_job(input_video: str, out_base_dir: str) -> Path:
    """Create a new job directory with job.json and config.yaml.

    Returns the path to job.json.
    """
    input_video = Path(input_video).resolve()
    if not input_video.exists():
        raise FileNotFoundError(f"Input video not found: {input_video}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    job_id = f"{timestamp}_{uuid.uuid4().hex[:6]}"
    out_dir = Path(out_base_dir).resolve() / job_id

    # Create directory structure
    (out_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (out_dir / "exports" / "clips").mkdir(parents=True, exist_ok=True)
    (out_dir / "debug").mkdir(parents=True, exist_ok=True)

    # Write job.json
    job_data = {
        "input_video": str(input_video),
        "out_dir": str(out_dir),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    job_path = out_dir / "job.json"
    with open(job_path, "w", encoding="utf-8") as f:
        json.dump(job_data, f, indent=2, ensure_ascii=False)

    # Copy default config
    config = load_default_config()
    config_path = out_dir / "config.yaml"
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

    add_recent_job(str(job_path), job_data)

    return job_path


def load_job(job_path: str | Path) -> dict:
    """Load job.json and return its contents."""
    job_path = Path(job_path)
    if not job_path.exists():
        raise FileNotFoundError(f"job.json not found: {job_path}")
    with open(job_path, "r", encoding="utf-8") as f:
        return json.load(f)


def job_dir(job_path: str | Path) -> Path:
    """Return the job directory from a job.json path."""
    return Path(job_path).parent


def artifacts_dir(job_path: str | Path) -> Path:
    """Return the artifacts directory for a job."""
    return job_dir(job_path) / "artifacts"


def exports_dir(job_path: str | Path) -> Path:
    """Return the exports directory for a job."""
    return job_dir(job_path) / "exports"


def debug_dir(job_path: str | Path) -> Path:
    """Return the debug directory for a job."""
    return job_dir(job_path) / "debug"


def proxy_scale(job_path: str | Path) -> tuple[float, float]:
    """Return (sx, sy) scale factors from original to proxy resolution.

    Reads video_meta.json (original) and proxy.mp4 dimensions.
    Returns (1.0, 1.0) if sizes match or data is unavailable.
    """
    import cv2

    art = artifacts_dir(job_path)
    meta_path = art / "video_meta.json"
    proxy_path = art / "proxy.mp4"

    if not meta_path.exists() or not proxy_path.exists():
        return 1.0, 1.0

    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    orig_w = meta.get("width", 0)
    orig_h = meta.get("height", 0)
    if orig_w <= 0 or orig_h <= 0:
        return 1.0, 1.0

    cap = cv2.VideoCapture(str(proxy_path))
    proxy_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    proxy_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    if proxy_w <= 0 or proxy_h <= 0:
        return 1.0, 1.0

    return proxy_w / orig_w, proxy_h / orig_h


def scale_zones(zones: list[dict], sx: float, sy: float) -> list[dict]:
    """Scale zone rects and polygons from original to proxy coordinates."""
    if sx == 1.0 and sy == 1.0:
        return zones
    scaled = []
    for z in zones:
        r = z.get("rect", {})
        entry = {
            **z,
            "rect": {
                "x": int(r.get("x", 0) * sx),
                "y": int(r.get("y", 0) * sy),
                "w": int(r.get("w", 0) * sx),
                "h": int(r.get("h", 0) * sy),
            },
        }
        if "polygon" in z:
            entry["polygon"] = [
                [int(pt[0] * sx), int(pt[1] * sy)] for pt in z["polygon"]
            ]
        scaled.append(entry)
    return scaled
