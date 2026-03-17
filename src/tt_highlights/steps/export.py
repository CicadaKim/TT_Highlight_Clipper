"""Step: export – extract MP4 clips and create highlights reel."""

import json
import logging
import shutil
import subprocess
from pathlib import Path

from ..job import artifacts_dir, exports_dir

logger = logging.getLogger(__name__)


def run(job: dict, config: dict, job_path: str) -> None:
    """Execute the export step."""
    input_video = job["input_video"]
    art = artifacts_dir(job_path)
    exp = exports_dir(job_path)
    clips_dir = exp / "clips"
    # Clean previous export to avoid stale files mixing in
    if clips_dir.exists():
        shutil.rmtree(clips_dir)
    clips_dir.mkdir(parents=True, exist_ok=True)
    reel_old = exp / "highlights_reel.mp4"
    reel_old.unlink(missing_ok=True)

    with open(art / "highlights.json", "r", encoding="utf-8") as f:
        highlights_data = json.load(f)

    highlights = highlights_data["highlights"]

    if not highlights:
        logger.warning("No highlights to export.")
        return

    # Extract individual clips
    exported_clips = []
    for hl in highlights:
        rank = hl["rank"]
        category = hl["category"]
        clip_start = hl["clip_start"]
        clip_end = hl["clip_end"]

        clip_name = f"clip_{rank:03d}_{category}.mp4"
        clip_path = clips_dir / clip_name

        logger.info(f"Exporting {clip_name}: [{clip_start:.1f}-{clip_end:.1f}]")

        try:
            clip_duration = clip_end - clip_start
            _run_ffmpeg([
                "ffmpeg", "-y",
                "-ss", str(clip_start),
                "-i", input_video,
                "-t", str(clip_duration),
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-pix_fmt", "yuv420p",
                "-profile:v", "high", "-level", "4.1",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                str(clip_path),
            ])
            exported_clips.append(clip_path)
        except RuntimeError as e:
            logger.error(f"Failed to export {clip_name}: {e}")
            continue

    if not exported_clips:
        logger.error("No clips were exported successfully.")
        return

    # Create highlights reel via concat
    reel_path = exp / "highlights_reel.mp4"
    logger.info(f"Creating highlights reel with {len(exported_clips)} clips...")

    # Write concat list
    concat_list = exp / "concat_list.txt"
    with open(concat_list, "w", encoding="utf-8") as f:
        for clip_path in exported_clips:
            # Use forward slashes and escape single quotes for ffmpeg
            safe_path = str(clip_path).replace("\\", "/").replace("'", "'\\''")
            f.write(f"file '{safe_path}'\n")

    try:
        _run_ffmpeg([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_list),
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-profile:v", "high", "-level", "4.1",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            str(reel_path),
        ])
        logger.info(f"Highlights reel created: {reel_path}")
    except RuntimeError as e:
        logger.error(f"Failed to create reel: {e}")

    # Clean up concat list
    concat_list.unlink(missing_ok=True)

    logger.info(f"Export done: {len(exported_clips)} clips + reel")


def _run_ffmpeg(cmd: list[str]) -> None:
    """Run an ffmpeg command, raising on failure."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"ffmpeg stderr:\n{result.stderr}")
        raise RuntimeError(f"ffmpeg failed: {' '.join(cmd[:4])}...")
