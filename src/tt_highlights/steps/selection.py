"""Step: selection – choose final highlights with hybrid clip windows.

Hybrid selection:
  - Short rallies (duration < 70% of clip_length): dynamic window
    [rally_start - pre_roll, rally_end + post_roll]
  - Long rallies (duration >= 70% of clip_length): anchored fixed-length
    clip centered on an anchor point, with length = clip_length

Anchor rules by category:
  - reaction: end_refined + post_roll (end anchor)
  - impact: impact_peak_t > ball_speed_peak_t > rally midpoint
  - long_rally: densest impact window center > dynamic fallback
"""

import json
import logging
from pathlib import Path

import numpy as np

from ..job import artifacts_dir

logger = logging.getLogger(__name__)


def run(job: dict, config: dict, job_path: str) -> None:
    """Execute the selection step."""
    art = artifacts_dir(job_path)

    with open(art / "rallies.json", "r", encoding="utf-8") as f:
        rallies_data = json.load(f)
    with open(art / "scores.json", "r", encoding="utf-8") as f:
        scores_data = json.load(f)
    with open(art / "features.json", "r", encoding="utf-8") as f:
        features_data = json.load(f)

    # Load video metadata for duration clamping
    meta_path = art / "video_meta.json"
    video_duration = 9999.0
    if meta_path.exists():
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        video_duration = meta.get("duration_sec", 9999.0)

    # Load feedback if available
    feedback_path = art / "feedback.json"
    excluded_ids = set()
    pinned_ids = set()
    if feedback_path.exists():
        with open(feedback_path, "r", encoding="utf-8") as f:
            feedback = json.load(f)
        excluded_ids = set(feedback.get("excluded_rally_ids", []))
        pinned_ids = set(feedback.get("pinned_rally_ids", []))

    ccfg = config["clips"]
    total = ccfg["total"]
    quotas = ccfg["quotas"]
    clip_length = ccfg["length_sec"]
    pre_roll = ccfg["pre_roll_sec"]
    post_roll = ccfg["post_roll_sec"]
    overlap_sec = ccfg["overlap_sec"]

    rallies_by_id = {r["id"]: r for r in rallies_data["rallies"]}
    features_by_id = {f["rally_id"]: f for f in features_data["rally_features"]}
    candidates = scores_data["candidates"]

    # Apply scoring.minimums filters
    minimums = config.get("scoring", {}).get("minimums", {})
    min_segment_score = minimums.get("segment_score", 0)
    min_category_score = minimums.get("category_score", 0)

    if min_segment_score > 0:
        for cat in list(candidates.keys()):
            candidates[cat] = [
                c for c in candidates[cat]
                if rallies_by_id.get(c["rally_id"], {}).get("segment_score", 1) >= min_segment_score
            ]
    if min_category_score > 0:
        for cat in list(candidates.keys()):
            candidates[cat] = [
                c for c in candidates[cat]
                if c["score"] >= min_category_score
            ]

    # Step 1: Select candidates per category by quota
    selected = []
    used_rally_ids = set()

    # First, force-include pinned rallies
    for rid in pinned_ids:
        if rid in excluded_ids:
            continue
        if rid not in rallies_by_id:
            continue
        rally = rallies_by_id[rid]
        # Find best category for this rally
        best_cat = None
        best_score = -1
        for cat, cands in candidates.items():
            for c in cands:
                if c["rally_id"] == rid and c["score"] > best_score:
                    best_score = c["score"]
                    best_cat = cat
        if best_cat:
            window = _compute_window(
                rally, best_cat, features_by_id.get(rid, {}),
                clip_length, pre_roll, post_roll, video_duration
            )
            selected.append({
                "category": best_cat,
                "rally_id": rid,
                "clip_start": window["clip_start"],
                "clip_end": window["clip_end"],
                "clip_mode": window["clip_mode"],
                "clip_length_sec": window["clip_length_sec"],
                "anchor_t": window["anchor_t"],
                "anchor_reason": window["anchor_reason"],
                "score": best_score,
                "reasons": ["pinned"],
            })
            used_rally_ids.add(rid)

    # Then fill quotas per category
    for cat, quota in quotas.items():
        cat_candidates = candidates.get(cat, [])
        count = sum(1 for s in selected if s["category"] == cat)

        for cand in cat_candidates:
            if count >= quota:
                break
            rid = cand["rally_id"]
            if rid in used_rally_ids or rid in excluded_ids:
                continue
            if rid not in rallies_by_id:
                continue

            rally = rallies_by_id[rid]
            window = _compute_window(
                rally, cat, features_by_id.get(rid, {}),
                clip_length, pre_roll, post_roll, video_duration
            )

            # Check overlap with existing selections
            if _has_overlap(window["clip_start"], window["clip_end"],
                            selected, overlap_sec):
                continue

            selected.append({
                "category": cat,
                "rally_id": rid,
                "clip_start": window["clip_start"],
                "clip_end": window["clip_end"],
                "clip_mode": window["clip_mode"],
                "clip_length_sec": window["clip_length_sec"],
                "anchor_t": window["anchor_t"],
                "anchor_reason": window["anchor_reason"],
                "score": cand["score"],
                "reasons": cand.get("reasons", []),
            })
            used_rally_ids.add(rid)
            count += 1

    # Step 2: Fill remaining slots from all candidates by score
    if len(selected) < total:
        all_candidates = []
        for cat, cands in candidates.items():
            for c in cands:
                all_candidates.append((cat, c))
        all_candidates.sort(key=lambda x: x[1]["score"], reverse=True)

        for cat, cand in all_candidates:
            if len(selected) >= total:
                break
            rid = cand["rally_id"]
            if rid in used_rally_ids or rid in excluded_ids:
                continue
            if rid not in rallies_by_id:
                continue

            rally = rallies_by_id[rid]
            window = _compute_window(
                rally, cat, features_by_id.get(rid, {}),
                clip_length, pre_roll, post_roll, video_duration
            )

            if _has_overlap(window["clip_start"], window["clip_end"],
                            selected, overlap_sec):
                continue

            selected.append({
                "category": cat,
                "rally_id": rid,
                "clip_start": window["clip_start"],
                "clip_end": window["clip_end"],
                "clip_mode": window["clip_mode"],
                "clip_length_sec": window["clip_length_sec"],
                "anchor_t": window["anchor_t"],
                "anchor_reason": window["anchor_reason"],
                "score": cand["score"],
                "reasons": cand.get("reasons", []),
            })
            used_rally_ids.add(rid)

    # Sort by clip_start time and assign ranks
    selected.sort(key=lambda x: x["clip_start"])
    for i, s in enumerate(selected):
        s["rank"] = i + 1
        s["clip_start"] = round(s["clip_start"], 3)
        s["clip_end"] = round(s["clip_end"], 3)
        s["clip_length_sec"] = round(s["clip_length_sec"], 3)
        s["score"] = round(s["score"], 4)

    # Determine overall clip mode
    modes = set(s["clip_mode"] for s in selected)
    if len(modes) == 1:
        overall_mode = modes.pop()
    elif modes:
        overall_mode = "hybrid"
    else:
        overall_mode = "dynamic"

    output = {
        "clip_mode": overall_mode,
        "highlights": selected,
    }
    with open(art / "highlights.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    logger.info(f"Selected {len(selected)} highlights (mode={overall_mode}).")
    for s in selected:
        logger.info(
            f"  #{s['rank']} {s['category']} R{s['rally_id']} "
            f"[{s['clip_start']:.1f}-{s['clip_end']:.1f}] "
            f"mode={s['clip_mode']} score={s['score']:.2f}"
        )


def _compute_window(
    rally: dict, category: str, features: dict,
    clip_length: float, pre_roll: float, post_roll: float,
    video_duration: float,
) -> dict:
    """Compute clip window using hybrid strategy.

    Returns dict with: clip_start, clip_end, clip_mode, clip_length_sec,
    anchor_t, anchor_reason.
    """
    start = rally["start"]
    end = rally.get("end_refined", rally["end"])
    rally_duration = end - start

    # Decision: dynamic vs anchored fixed-length
    if rally_duration >= clip_length * 0.7:
        # Long rally → anchored fixed-length
        anchor_t, anchor_reason = _find_anchor(
            rally, category, features, start, end,
        )
        half = clip_length / 2
        clip_start = anchor_t - half
        clip_end = anchor_t + half
        clip_mode = "anchored_fixed_length"
    else:
        # Short rally → dynamic
        clip_start = start - pre_roll
        clip_end = end + post_roll
        anchor_t = None
        anchor_reason = "dynamic"
        clip_mode = "dynamic"

    # Clamp to video boundaries
    clip_start = max(0.0, clip_start)
    clip_end = min(video_duration, clip_end)

    return {
        "clip_start": clip_start,
        "clip_end": clip_end,
        "clip_mode": clip_mode,
        "clip_length_sec": clip_end - clip_start,
        "anchor_t": round(anchor_t, 3) if anchor_t is not None else None,
        "anchor_reason": anchor_reason,
    }


def _find_anchor(
    rally: dict, category: str, features: dict,
    start: float, end: float,
) -> tuple[float, str]:
    """Determine anchor point based on category.

    Returns (anchor_time, reason_string).
    """
    midpoint = (start + end) / 2

    if category == "reaction":
        # Anchor at end: end_refined + post_roll area
        end_refined = rally.get("end_refined", end)
        return end_refined, "end_refined"

    elif category == "impact":
        # Priority: impact_peak_t → ball_speed_peak_t → midpoint
        impact_peak_t = features.get("impact_peak_t")
        if impact_peak_t is not None and start <= impact_peak_t <= end:
            return impact_peak_t, "impact_peak_t"

        ball_speed_peak_t = features.get("ball_speed_peak_t")
        if ball_speed_peak_t is not None and start <= ball_speed_peak_t <= end:
            return ball_speed_peak_t, "ball_speed_peak_t"

        return midpoint, "rally_midpoint"

    elif category == "long_rally":
        # Densest impact window center → dynamic fallback
        impact_times = features.get("impact_times", [])
        if len(impact_times) >= 3:
            center = _densest_window_center(impact_times, start, end)
            if center is not None:
                return center, "densest_impact_window"

        return midpoint, "rally_midpoint"

    # Default fallback
    return midpoint, "rally_midpoint"


def _densest_window_center(
    impact_times: list[float],
    start: float,
    end: float,
    window_sec: float = 5.0,
) -> float | None:
    """Find the center of the window with the most impact events."""
    if not impact_times:
        return None

    times = sorted(impact_times)
    best_count = 0
    best_center = None

    for i, t in enumerate(times):
        # Count impacts in [t, t + window_sec]
        count = sum(1 for t2 in times if t <= t2 <= t + window_sec)
        if count > best_count:
            best_count = count
            # Center of the window
            window_end = min(t + window_sec, end)
            best_center = (t + window_end) / 2

    return best_center


def _has_overlap(clip_start: float, clip_end: float,
                 selected: list, overlap_sec: float) -> bool:
    """Check if a clip overlaps with any already selected clip."""
    for s in selected:
        overlap = min(clip_end, s["clip_end"]) - max(clip_start, s["clip_start"])
        if overlap >= overlap_sec:
            return True
    return False
