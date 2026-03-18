"""Runtime utilities — device resolution and GPU helpers."""

import logging
import subprocess

logger = logging.getLogger(__name__)

_cuda_ok: bool | None = None


def _cuda_available() -> bool:
    """Check if CUDA is available via PyTorch (if installed).

    Caches the result for the process lifetime (hardware doesn't change).
    """
    global _cuda_ok
    if _cuda_ok is not None:
        return _cuda_ok
    try:
        import torch
        _cuda_ok = torch.cuda.is_available()
    except ImportError:
        _cuda_ok = False
    return _cuda_ok


def resolve_device(config: dict) -> str:
    """Resolve the compute device from config['runtime']['device'].

    Returns "cuda" if available and requested, otherwise "cpu".
    Re-reads config on every call so Streamlit config changes take effect.
    """
    requested = config.get("runtime", {}).get("device", "auto")

    if requested == "cpu":
        device = "cpu"
    elif requested == "auto":
        device = "cuda" if _cuda_available() else "cpu"
    else:
        # Explicit device string like "cuda", "cuda:0"
        device = requested if _cuda_available() else "cpu"

    logger.info(f"Runtime device resolved: requested={requested!r} → {device!r}")
    return device


_nvenc_available: bool | None = None


def has_nvenc() -> bool:
    """Check if ffmpeg supports h264_nvenc encoder."""
    global _nvenc_available
    if _nvenc_available is not None:
        return _nvenc_available

    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=10,
        )
        _nvenc_available = "h264_nvenc" in result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        _nvenc_available = False

    logger.info(f"NVENC available: {_nvenc_available}")
    return _nvenc_available


def get_video_encoder(config: dict) -> list[str]:
    """Return ffmpeg video encoder args based on config.

    Returns a list like ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "18"]
    or ["-c:v", "libx264", "-preset", "fast", "-crf", "18"] for CPU fallback.
    """
    prefer_nvenc = config.get("runtime", {}).get("prefer_nvenc", True)

    if prefer_nvenc and has_nvenc():
        return [
            "-c:v", "h264_nvenc",
            "-preset", "p4",
            "-cq", "18",
            "-pix_fmt", "yuv420p",
            "-profile:v", "high", "-level", "4.1",
        ]

    return [
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-profile:v", "high", "-level", "4.1",
    ]
