"""Step: rally_segment – combine audio events + activity to identify rally periods."""

import json
import logging
from pathlib import Path

import numpy as np

from ..job import artifacts_dir, debug_dir

logger = logging.getLogger(__name__)


def run(job: dict, config: dict, job_path: str) -> None:
    """Execute the rally_segment step."""
    art = artifacts_dir(job_path)
    dbg = debug_dir(job_path)
    dbg.mkdir(parents=True, exist_ok=True)

    # Load inputs
    with open(art / "audio_events.json", "r", encoding="utf-8") as f:
        audio_events = json.load(f)
    with open(art / "activity.json", "r", encoding="utf-8") as f:
        activity_data = json.load(f)

    scfg = config["segmentation"]
    impact_gap_max = scfg["impact_gap_max_sec"]
    min_impacts = scfg["min_impacts"]
    min_duration = scfg["min_rally_duration_sec"]
    end_grace = scfg["end_grace_sec"]
    activity_min_mean = scfg["activity_min_mean"]

    impacts = audio_events["impact_events"]
    cheers = audio_events["cheer_segments"]
    activity_samples = activity_data["samples"]

    # Build activity lookup
    act_times = np.array([s["t"] for s in activity_samples])
    act_values = np.array([s["activity"] for s in activity_samples])

    # --- [NEW] Step 2: Adaptive threshold – remove low-confidence impacts ---
    raw_impact_count = len(impacts)
    if len(impacts) > 5:
        scores = np.array([imp["score"] for imp in impacts])
        adaptive_threshold = max(
            scfg.get("impact_score_floor", 0.04),
            float(np.percentile(scores, 30)),
        )
        impacts = [imp for imp in impacts if imp["score"] >= adaptive_threshold]
        logger.info(
            f"Adaptive threshold: {adaptive_threshold:.3f}, "
            f"{raw_impact_count} → {len(impacts)} impacts"
        )

    # --- Cross-modal validation: filter impacts without video confirmation ---
    if scfg.get("require_video_confirmation", False):
        confirm_window = scfg.get("video_confirm_window_sec", 0.5)
        confirm_threshold = scfg.get("video_confirm_threshold", 0.03)
        confirmed = []
        for imp in impacts:
            t = imp["t"]
            mask = (act_times >= t - confirm_window) & (act_times <= t + confirm_window)
            if mask.any() and float(act_values[mask].max()) >= confirm_threshold:
                confirmed.append(imp)
        pre_cross = len(impacts)
        impacts = confirmed
        logger.info(
            f"Cross-modal filter: {pre_cross} → {len(impacts)} impacts "
            f"(window={confirm_window}s, threshold={confirm_threshold})"
        )

    # --- [MODIFIED] Step 3: Variable gap impact grouping ---
    gap_multiplier = scfg.get("gap_multiplier", 2.5)
    gap_min_sec = scfg.get("gap_min_sec", 2.0)

    rallies = []
    if impacts:
        current_start = impacts[0]["t"]
        current_end = impacts[0]["t"]
        current_impacts = [impacts[0]]
        current_gap_max = impact_gap_max

        for imp in impacts[1:]:
            gap = imp["t"] - current_end
            if gap <= current_gap_max:
                # Continue current rally
                current_end = imp["t"]
                current_impacts.append(imp)
                # Dynamic gap update based on rally's average interval
                if len(current_impacts) >= 3:
                    intervals = [
                        current_impacts[j + 1]["t"] - current_impacts[j]["t"]
                        for j in range(len(current_impacts) - 1)
                    ]
                    avg_interval = sum(intervals) / len(intervals)
                    current_gap_max = max(
                        gap_min_sec,
                        min(impact_gap_max, avg_interval * gap_multiplier),
                    )
            else:
                # End current rally and start new one
                rallies.append(
                    _make_rally(current_start, current_end, current_impacts, end_grace)
                )
                current_start = imp["t"]
                current_end = imp["t"]
                current_impacts = [imp]
                current_gap_max = impact_gap_max  # Reset

        # Don't forget the last rally
        rallies.append(
            _make_rally(current_start, current_end, current_impacts, end_grace)
        )

    # Split long rallies at activity dips
    split_min_dur = scfg.get("split_min_duration_sec", 8.0)
    split_act_max = scfg.get("split_activity_max", 0.025)
    split_min_win = scfg.get("split_min_window_sec", 1.0)
    pre_split_count = len(rallies)
    rallies = _split_long_rallies(
        rallies, act_times, act_values, end_grace,
        split_min_dur, split_act_max, split_min_win,
    )
    if len(rallies) > pre_split_count:
        logger.info(f"Activity-dip split: {pre_split_count} -> {len(rallies)} candidates")

    # Filter by duration and impact count
    filtered = []
    for r in rallies:
        duration = r["end"] - r["start"]
        if duration < min_duration:
            continue
        if r["impact_count"] < min_impacts:
            continue
        filtered.append(r)

    # Video activity filtering
    activity_filtered = []
    for r in filtered:
        mean_act = _get_activity_mean(act_times, act_values, r["start"], r["end"])
        if mean_act < activity_min_mean:
            logger.debug(f"Rally {r['start']:.1f}-{r['end']:.1f} removed: "
                         f"low activity ({mean_act:.3f})")
            continue
        r["conf_video"] = round(mean_act, 4)
        activity_filtered.append(r)

    # --- [NEW] Step 7: Rhythm pattern score ---
    for r in activity_filtered:
        r["rhythm_score"] = _compute_rhythm_score(r["_impacts"])

    # --- [NEW] Step 8: Merge nearby rallies ---
    merge_gap = scfg.get("merge_gap_sec", 2.0)
    pre_merge_count = len(activity_filtered)
    activity_filtered = _merge_nearby(activity_filtered, merge_gap)
    if len(activity_filtered) < pre_merge_count:
        logger.info(f"Merge nearby: {pre_merge_count} -> {len(activity_filtered)} rallies")

    # --- [NEW] Step 9: Activity gradient boundary refinement ---
    look_back = scfg.get("boundary_look_back_sec", 2.0)
    look_fwd = scfg.get("boundary_look_fwd_sec", 1.0)
    grad_thresh = scfg.get("boundary_grad_threshold", 0.02)
    for r in activity_filtered:
        _refine_boundaries(r, act_times, act_values, look_back, look_fwd, grad_thresh)

    # Cheer-based end refinement
    for r in activity_filtered:
        r["end_refined"] = r["end"]
        r["reason_end_refined"] = "gap_timeout"

        for cheer in cheers:
            # Check if cheer starts near the end of rally (within ±2 sec)
            if abs(cheer["start"] - r["end"]) <= 2.0:
                r["end_refined"] = round(cheer["start"], 3)
                r["reason_end_refined"] = "cheer"
                break

    # Assign IDs
    for i, r in enumerate(activity_filtered):
        r["id"] = i + 1

    # --- [MODIFIED] Step 11: Confidence (count + quality + rhythm weighted) ---
    for r in activity_filtered:
        count_factor = min(1.0, r["impact_count"] / 8.0)
        quality_factor = min(1.0, r["impact_score_mean"] / 0.3)
        rhythm_factor = r["rhythm_score"]
        r["conf_audio"] = round(
            count_factor * 0.35 + quality_factor * 0.35 + rhythm_factor * 0.30, 4
        )

    # Remove transient fields before output
    for r in activity_filtered:
        r.pop("_impacts", None)

    # Write output
    output = {"rallies": activity_filtered}
    with open(art / "rallies.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    logger.info(f"Rally segmentation: {len(activity_filtered)} rallies "
                f"(from {len(rallies)} candidates)")

    # Debug plot
    _plot_timeline(activity_filtered, act_times, act_values,
                   impacts, cheers, dbg / "rallies_timeline.png")


def _make_rally(start: float, end: float, impacts: list, end_grace: float) -> dict:
    """Create a rally dict from impact cluster."""
    scores = [imp["score"] for imp in impacts]
    return {
        "start": round(start, 3),
        "end": round(end + end_grace, 3),
        "impact_count": len(impacts),
        "impact_score_mean": round(sum(scores) / len(scores), 4),
        "impact_score_max": round(max(scores), 4),
        "_impacts": impacts,
    }


def _compute_rhythm_score(impacts: list) -> float:
    """Impact interval regularity score (0~1). Higher = more regular."""
    if len(impacts) < 3:
        return 0.5  # Not enough data → neutral

    intervals = []
    for i in range(len(impacts) - 1):
        intervals.append(impacts[i + 1]["t"] - impacts[i]["t"])

    intervals = np.array(intervals)
    # Typical table tennis rally interval: 0.3~2.0s
    valid = intervals[(intervals >= 0.2) & (intervals <= 3.0)]
    if len(valid) < 2:
        return 0.3  # Not enough valid intervals

    mean_val = float(np.mean(valid))
    if mean_val <= 0:
        return 0.0
    # CV (coefficient of variation) = std / mean → lower = more regular
    cv = float(np.std(valid) / mean_val)
    # Convert CV to 0~1 score (CV=0 → 1.0, CV=1 → 0.0)
    return round(max(0.0, 1.0 - cv), 4)


def _merge_nearby(rallies: list, merge_gap_sec: float = 2.0) -> list:
    """Merge adjacent rallies whose gap is within merge_gap_sec."""
    if len(rallies) < 2:
        return rallies

    merged = [rallies[0]]
    for r in rallies[1:]:
        prev = merged[-1]
        gap = r["start"] - prev["end"]
        if gap <= merge_gap_sec:
            # Merge
            prev["end"] = r["end"]
            prev["impact_count"] += r["impact_count"]
            prev["_impacts"] = prev.get("_impacts", []) + r.get("_impacts", [])
            all_scores = [imp["score"] for imp in prev["_impacts"]]
            prev["impact_score_mean"] = round(sum(all_scores) / len(all_scores), 4)
            prev["impact_score_max"] = round(max(all_scores), 4)
            # Recompute rhythm for merged rally
            prev["rhythm_score"] = _compute_rhythm_score(prev["_impacts"])
            # Recompute conf_video as max of the two
            if "conf_video" in r:
                prev["conf_video"] = round(
                    max(prev.get("conf_video", 0), r["conf_video"]), 4
                )
        else:
            merged.append(r)

    return merged


def _refine_boundaries(
    rally: dict,
    act_times: np.ndarray,
    act_values: np.ndarray,
    look_back: float = 2.0,
    look_fwd: float = 1.0,
    grad_threshold: float = 0.02,
) -> None:
    """Refine start/end using activity gradient."""
    # Start refinement: find activity rise point before first impact
    start = rally["start"]
    search_start = max(0.0, start - look_back)
    mask = (act_times >= search_start) & (act_times <= start)
    if mask.sum() > 3:
        window = act_values[mask]
        grad = np.diff(window)
        if len(grad) > 0:
            peak_idx = int(np.argmax(grad))
            if grad[peak_idx] > grad_threshold:
                times_in_window = act_times[mask]
                rally["start"] = round(float(times_in_window[peak_idx]), 3)

    # End refinement: find activity drop after last impact
    end = rally["end"]
    search_end = end + look_fwd
    mask = (act_times >= end - 1.0) & (act_times <= search_end)
    if mask.sum() > 3:
        window = act_values[mask]
        grad = np.diff(window)
        if len(grad) > 0:
            dip_idx = int(np.argmin(grad))
            if grad[dip_idx] < -grad_threshold:
                times_in_window = act_times[mask]
                rally["end"] = round(float(times_in_window[dip_idx + 1]), 3)


def _split_long_rallies(
    rallies: list,
    act_times: np.ndarray,
    act_values: np.ndarray,
    end_grace: float,
    min_dur: float,
    act_max: float,
    min_window: float,
) -> list:
    """Recursively split long rallies at activity dips."""
    result = []
    for r in rallies:
        result.extend(
            _try_split(r, act_times, act_values, end_grace,
                       min_dur, act_max, min_window)
        )
    return result


def _try_split(
    rally: dict,
    act_times: np.ndarray,
    act_values: np.ndarray,
    end_grace: float,
    min_dur: float,
    act_max: float,
    min_window: float,
) -> list:
    """Try to split a single rally at the deepest activity dip.

    Returns a list of one or more rallies.
    """
    duration = rally["end"] - rally["start"]
    impacts = rally.get("_impacts", [])

    if duration < min_dur or len(impacts) < 4:
        return [rally]

    # Evaluate each consecutive impact gap as a split candidate
    best_idx = -1
    best_act = float("inf")

    for i in range(len(impacts) - 1):
        gap_start = impacts[i]["t"]
        gap_end = impacts[i + 1]["t"]
        gap_len = gap_end - gap_start

        if gap_len < min_window:
            continue

        # Mean activity in the gap window
        mask = (act_times >= gap_start) & (act_times <= gap_end)
        if not mask.any():
            mean_act = 0.0
        else:
            mean_act = float(act_values[mask].mean())

        if mean_act < best_act:
            best_act = mean_act
            best_idx = i

    # Check if best candidate qualifies
    if best_idx < 0 or best_act >= act_max:
        return [rally]

    # Both halves need at least 2 impacts
    left_impacts = impacts[: best_idx + 1]
    right_impacts = impacts[best_idx + 1 :]
    if len(left_impacts) < 2 or len(right_impacts) < 2:
        return [rally]

    left = _make_rally(left_impacts[0]["t"], left_impacts[-1]["t"],
                       left_impacts, end_grace)
    right = _make_rally(right_impacts[0]["t"], right_impacts[-1]["t"],
                        right_impacts, end_grace)

    logger.debug(
        f"Split rally {rally['start']:.1f}-{rally['end']:.1f} "
        f"at gap {impacts[best_idx]['t']:.1f}-{impacts[best_idx+1]['t']:.1f} "
        f"(activity={best_act:.4f})"
    )

    # Recurse on both halves
    return (
        _try_split(left, act_times, act_values, end_grace,
                   min_dur, act_max, min_window)
        + _try_split(right, act_times, act_values, end_grace,
                     min_dur, act_max, min_window)
    )


def _get_activity_mean(times: np.ndarray, values: np.ndarray,
                       start: float, end: float) -> float:
    """Get mean activity in a time range."""
    if len(times) == 0:
        return 0.0
    mask = (times >= start) & (times <= end)
    if not mask.any():
        return 0.0
    return float(values[mask].mean())


def _plot_timeline(rallies: list, act_times: np.ndarray, act_values: np.ndarray,
                   impacts: list, cheers: list, out_path: Path) -> None:
    """Generate rallies timeline debug plot."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 6), sharex=True)

    # Activity curve
    if len(act_times) > 0:
        ax1.plot(act_times, act_values, color="blue", linewidth=0.8, alpha=0.7)
        ax1.fill_between(act_times, act_values, alpha=0.2)
    ax1.set_ylabel("Activity")
    ax1.set_title("Activity + Rally Segments")

    # Rally spans
    for r in rallies:
        ax1.axvspan(r["start"], r.get("end_refined", r["end"]),
                     alpha=0.3, color="green")
        ax1.text(r["start"], 0.95, f"R{r['id']}", fontsize=8,
                 transform=ax1.get_xaxis_transform())

    # Impact events
    for imp in impacts:
        ax2.axvline(imp["t"], color="red", alpha=0.4, linewidth=0.5)
    ax2.set_ylabel("Impacts")
    ax2.set_title("Impact Events + Cheer Segments")

    # Cheer segments
    for ch in cheers:
        ax2.axvspan(ch["start"], ch["end"], alpha=0.3,
                     color="orange" if ch["type"] == "cheer" else "purple")

    ax2.set_xlabel("Time (sec)")
    plt.tight_layout()
    plt.savefig(str(out_path), dpi=100)
    plt.close(fig)
