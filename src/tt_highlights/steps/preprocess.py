"""Step: preprocess – create proxy video, audio wav, frame0, and video metadata."""

import json
import logging
import subprocess
from pathlib import Path

from ..job import artifacts_dir, debug_dir

logger = logging.getLogger(__name__)


def run(job: dict, config: dict, job_path: str) -> None:
    """Execute the preprocess step."""
    input_video = job["input_video"]
    art = artifacts_dir(job_path)
    art.mkdir(parents=True, exist_ok=True)

    proxy_height = config["video"]["proxy_height"]
    proxy_fps = config["video"]["proxy_fps"]
    model_sr = config["audio"]["model_sr"]

    proxy_path = art / "proxy.mp4"
    audio_path = art / "audio.wav"
    frame0_path = art / "frame0.jpg"
    meta_path = art / "video_meta.json"

    # 1) Probe input video for metadata
    meta = _probe_video(input_video)

    # 2) Generate proxy video (scaled, fps adjusted, no audio)
    logger.info("Generating proxy video...")
    _run_ffmpeg([
        "ffmpeg", "-y", "-i", input_video,
        "-vf", f"scale=-2:{proxy_height},fps={proxy_fps}",
        "-an",  # no audio
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", "fast", "-crf", "23",
        "-movflags", "+faststart",
        str(proxy_path),
    ])

    # 3) Extract audio (mono, resampled)
    if meta["has_audio"]:
        logger.info("Extracting audio...")
        _run_ffmpeg([
            "ffmpeg", "-y", "-i", input_video,
            "-vn",  # no video
            "-ac", "1",  # mono
            "-ar", str(model_sr),
            "-c:a", "pcm_s16le",
            str(audio_path),
        ])
    else:
        logger.warning("No audio track found. Skipping audio extraction.")

    # 4) Extract frame0
    logger.info("Extracting frame0...")
    _run_ffmpeg([
        "ffmpeg", "-y", "-i", input_video,
        "-frames:v", "1",
        "-q:v", "2",
        str(frame0_path),
    ])

    # 5) Write video_meta.json
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    logger.info(f"Preprocess done. duration={meta['duration_sec']:.1f}s, "
                f"has_audio={meta['has_audio']}")


def _probe_video(path: str) -> dict:
    """Use ffprobe to extract video metadata."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    info = json.loads(result.stdout)

    video_stream = None
    has_audio = False
    for s in info.get("streams", []):
        if s["codec_type"] == "video" and video_stream is None:
            video_stream = s
        if s["codec_type"] == "audio":
            has_audio = True

    if video_stream is None:
        raise RuntimeError("No video stream found in input file.")

    duration = float(info["format"].get("duration", 0))
    fps_str = video_stream.get("r_frame_rate", "30/1")
    num, den = fps_str.split("/")
    fps = round(int(num) / max(int(den), 1), 2)

    return {
        "duration_sec": round(duration, 3),
        "fps": fps,
        "width": int(video_stream["width"]),
        "height": int(video_stream["height"]),
        "has_audio": has_audio,
    }


def _run_ffmpeg(cmd: list[str]) -> None:
    """Run an ffmpeg command, raising on failure."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"ffmpeg stderr:\n{result.stderr}")
        raise RuntimeError(f"ffmpeg failed: {' '.join(cmd[:4])}...")
