"""Job creation and loading utilities."""

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import load_default_config

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
