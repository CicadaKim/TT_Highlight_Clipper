"""Recent jobs management — persists across sessions."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_RECENT_FILE = Path.home() / ".tt_highlights" / "recent_jobs.json"
_MAX_RECENT = 20


def _load_raw() -> list[dict]:
    if not _RECENT_FILE.exists():
        return []
    try:
        with open(_RECENT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_raw(entries: list[dict]) -> None:
    _RECENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_RECENT_FILE, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


def add_recent_job(job_path: str, job_data: dict) -> None:
    """Add or update a job in the recent list."""
    job_path = str(Path(job_path).resolve())
    entries = _load_raw()

    # Remove existing entry for same path
    entries = [e for e in entries if e.get("job_path") != job_path]

    video_name = Path(job_data.get("input_video", "")).name or "unknown"
    now = datetime.now(timezone.utc).isoformat()

    entries.insert(0, {
        "job_path": job_path,
        "video_name": video_name,
        "created_at": job_data.get("created_at", now),
        "last_opened": now,
    })

    # Trim to max
    entries = entries[:_MAX_RECENT]
    _save_raw(entries)


def get_recent_jobs() -> list[dict]:
    """Return recent jobs, pruning stale entries (missing files)."""
    entries = _load_raw()
    valid = []
    for e in entries:
        if Path(e.get("job_path", "")).exists():
            valid.append(e)
    # Save pruned list if anything was removed
    if len(valid) != len(entries):
        _save_raw(valid)
    return valid


def remove_recent_job(job_path: str) -> None:
    """Remove a specific job from the recent list."""
    job_path = str(Path(job_path).resolve())
    entries = _load_raw()
    entries = [e for e in entries if e.get("job_path") != job_path]
    _save_raw(entries)
