"""Step: audio_events – detect impact and cheer/clap events via PANNs SED."""

import json
import logging
from pathlib import Path

import numpy as np

from ..job import artifacts_dir, debug_dir

logger = logging.getLogger(__name__)


def run(job: dict, config: dict, job_path: str) -> None:
    """Execute the audio_events step."""
    art = artifacts_dir(job_path)
    dbg = debug_dir(job_path)
    dbg.mkdir(parents=True, exist_ok=True)

    audio_path = art / "audio.wav"

    # Check if audio exists
    if not audio_path.exists():
        logger.warning("No audio.wav found. Writing empty audio_events.")
        _write_empty_events(art, config)
        return

    acfg = config["audio"]
    model_sr = acfg["model_sr"]
    hop_sec = acfg["hop_sec"]

    # Load audio
    import soundfile as sf
    audio_data, sr = sf.read(str(audio_path))
    if sr != model_sr:
        import librosa
        audio_data = librosa.resample(audio_data, orig_sr=sr, target_sr=model_sr)
        sr = model_sr

    # Ensure 1D (mono)
    if audio_data.ndim > 1:
        audio_data = audio_data.mean(axis=1)

    # Run PANNs SED model
    logger.info("Running PANNs SED model...")
    framewise_probs, labels = _run_panns_sed(audio_data, sr)

    # Find label indices for impact, cheer, clap
    impact_indices = _find_label_indices(labels, acfg["impact_label_contains"])
    cheer_indices = _find_label_indices(labels, acfg["cheer_label_contains"])
    clap_indices = _find_label_indices(labels, acfg["clap_label_contains"])

    logger.info(f"Label indices: impact={len(impact_indices)}, "
                f"cheer={len(cheer_indices)}, clap={len(clap_indices)}")

    # Compute per-frame probabilities
    n_frames = framewise_probs.shape[0]
    audio_duration = len(audio_data) / sr
    frame_times = np.linspace(0, audio_duration, n_frames)

    # Impact probability: max across impact labels per frame
    if impact_indices:
        impact_prob = framewise_probs[:, impact_indices].max(axis=1)
    else:
        impact_prob = np.zeros(n_frames)

    # Cheer probability: max across cheer labels
    if cheer_indices:
        cheer_prob = framewise_probs[:, cheer_indices].max(axis=1)
    else:
        cheer_prob = np.zeros(n_frames)

    # Clap probability
    if clap_indices:
        clap_prob = framewise_probs[:, clap_indices].max(axis=1)
    else:
        clap_prob = np.zeros(n_frames)

    # Detect impact events (local maxima above threshold)
    impact_events = _detect_impacts(
        frame_times, impact_prob,
        threshold=acfg["impact_threshold"],
        min_distance_sec=acfg["impact_min_distance_sec"],
    )

    # Detect cheer segments
    cheer_segments = _detect_segments(
        frame_times, cheer_prob,
        threshold=acfg["cheer_threshold"],
        merge_gap_sec=acfg["cheer_merge_gap_sec"],
        min_len_sec=acfg["cheer_min_len_sec"],
        seg_type="cheer",
    )

    # Detect clap segments
    clap_segments = _detect_segments(
        frame_times, clap_prob,
        threshold=acfg["cheer_threshold"],
        merge_gap_sec=acfg["cheer_merge_gap_sec"],
        min_len_sec=acfg["cheer_min_len_sec"],
        seg_type="clap",
    )

    all_cheer_segments = cheer_segments + clap_segments
    all_cheer_segments.sort(key=lambda s: s["start"])

    # Write output
    output = {
        "sr": model_sr,
        "hop_sec": hop_sec,
        "impact_events": impact_events,
        "cheer_segments": all_cheer_segments,
        "label_indices_used": {
            "impact": impact_indices,
            "cheer": cheer_indices,
            "clap": clap_indices,
        },
    }
    out_path = art / "audio_events.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    logger.info(f"Audio events: {len(impact_events)} impacts, "
                f"{len(all_cheer_segments)} cheer/clap segments")

    # Generate debug plot
    _plot_events(frame_times, impact_prob, cheer_prob + clap_prob,
                 impact_events, all_cheer_segments, dbg / "audio_events_plot.png")


def _run_panns_sed(audio: np.ndarray, sr: int) -> tuple[np.ndarray, list[str]]:
    """Run PANNs SED and return (framewise_probs, labels)."""
    from panns_inference import SoundEventDetection, labels as panns_labels

    sed = SoundEventDetection(checkpoint_path=None, device="cpu")
    # panns expects (batch, samples) at 32000 sr
    audio_input = audio[np.newaxis, :].astype(np.float32)
    framewise_output = sed.inference(audio_input)
    # framewise_output shape: (batch, frames, classes)
    probs = framewise_output[0]  # (frames, classes)
    return probs, panns_labels


def _find_label_indices(labels: list[str], patterns: list[str]) -> list[int]:
    """Find indices where label contains any of the pattern substrings."""
    indices = []
    for i, label in enumerate(labels):
        for pat in patterns:
            if pat.lower() in label.lower():
                indices.append(i)
                break
    return indices


def _detect_impacts(times: np.ndarray, prob: np.ndarray,
                    threshold: float, min_distance_sec: float) -> list[dict]:
    """Detect impact events as local maxima above threshold."""
    from scipy.signal import find_peaks

    dt = times[1] - times[0] if len(times) > 1 else 0.1
    min_distance_frames = max(1, int(min_distance_sec / dt))

    peaks, properties = find_peaks(prob, height=threshold,
                                   distance=min_distance_frames)

    events = []
    for idx in peaks:
        events.append({
            "t": round(float(times[idx]), 3),
            "score": round(float(prob[idx]), 4),
        })
    return events


def _detect_segments(times: np.ndarray, prob: np.ndarray,
                     threshold: float, merge_gap_sec: float,
                     min_len_sec: float, seg_type: str) -> list[dict]:
    """Detect segments where probability exceeds threshold."""
    above = prob >= threshold
    segments = []
    in_seg = False
    start_idx = 0

    for i in range(len(above)):
        if above[i] and not in_seg:
            in_seg = True
            start_idx = i
        elif not above[i] and in_seg:
            in_seg = False
            segments.append((start_idx, i - 1))
    if in_seg:
        segments.append((start_idx, len(above) - 1))

    # Merge segments with small gaps
    merged = []
    for seg in segments:
        if merged and (times[seg[0]] - times[merged[-1][1]]) < merge_gap_sec:
            merged[-1] = (merged[-1][0], seg[1])
        else:
            merged.append(seg)

    # Filter short segments and build output
    result = []
    for start_i, end_i in merged:
        duration = times[end_i] - times[start_i]
        if duration < min_len_sec:
            continue
        score = float(prob[start_i:end_i + 1].mean())
        result.append({
            "start": round(float(times[start_i]), 3),
            "end": round(float(times[end_i]), 3),
            "score": round(score, 4),
            "type": seg_type,
        })
    return result


def _write_empty_events(art: Path, config: dict) -> None:
    """Write empty audio_events.json when no audio is available."""
    output = {
        "sr": config["audio"]["model_sr"],
        "hop_sec": config["audio"]["hop_sec"],
        "impact_events": [],
        "cheer_segments": [],
        "label_indices_used": {"impact": [], "cheer": [], "clap": []},
    }
    with open(art / "audio_events.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)


def _plot_events(times: np.ndarray, impact_prob: np.ndarray,
                 cheer_prob: np.ndarray, impact_events: list,
                 cheer_segments: list, out_path: Path) -> None:
    """Generate debug plot of audio events."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 6), sharex=True)

    # Impact plot
    ax1.plot(times, impact_prob, color="blue", alpha=0.7, linewidth=0.5)
    ax1.set_ylabel("Impact Probability")
    ax1.set_title("Impact Events")
    for ev in impact_events:
        ax1.axvline(ev["t"], color="red", alpha=0.5, linewidth=0.8)

    # Cheer/clap plot
    ax2.plot(times, cheer_prob, color="green", alpha=0.7, linewidth=0.5)
    ax2.set_ylabel("Cheer/Clap Probability")
    ax2.set_xlabel("Time (sec)")
    ax2.set_title("Cheer/Clap Segments")
    for seg in cheer_segments:
        ax2.axvspan(seg["start"], seg["end"], alpha=0.3,
                     color="orange" if seg["type"] == "cheer" else "purple")

    plt.tight_layout()
    plt.savefig(str(out_path), dpi=100)
    plt.close(fig)
