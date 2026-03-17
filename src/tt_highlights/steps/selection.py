"""Step: selection – choose final highlights and compute dynamic clip windows."""

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
            clip_start, clip_end = _compute_window(
                rally, best_cat, features_by_id.get(rid, {}),
                clip_length, pre_roll, post_roll, video_duration
            )
            selected.append({
                "category": best_cat,
                "rally_id": rid,
                "clip_start": clip_start,
                "clip_end": clip_end,
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
            clip_start, clip_end = _compute_window(
                rally, cat, features_by_id.get(rid, {}),
                clip_length, pre_roll, post_roll, video_duration
            )

            # Check overlap with existing selections
            if _has_overlap(clip_start, clip_end, selected, overlap_sec):
                continue

            selected.append({
                "category": cat,
                "rally_id": rid,
                "clip_start": clip_start,
                "clip_end": clip_end,
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
            clip_start, clip_end = _compute_window(
                rally, cat, features_by_id.get(rid, {}),
                clip_length, pre_roll, post_roll, video_duration
            )

            if _has_overlap(clip_start, clip_end, selected, overlap_sec):
                continue

            selected.append({
                "category": cat,
                "rally_id": rid,
                "clip_start": clip_start,
                "clip_end": clip_end,
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
        s["score"] = round(s["score"], 4)

    output = {
        "clip_mode": "dynamic",
        "highlights": selected,
    }
    with open(art / "highlights.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    logger.info(f"Selected {len(selected)} highlights.")
    for s in selected:
        logger.info(f"  #{s['rank']} {s['category']} R{s['rally_id']} "
                     f"[{s['clip_start']:.1f}-{s['clip_end']:.1f}] score={s['score']:.2f}")


def _compute_window(rally: dict, category: str, features: dict,
                    clip_length: float, pre_roll: float, post_roll: float,
                    video_duration: float) -> tuple[float, float]:
    """Compute clip window: [rally_start - pre_roll, rally_end + post_roll]."""
    start = rally["start"]
    end = rally.get("end_refined", rally["end"])

    clip_start = start - pre_roll
    clip_end = end + post_roll

    # Clamp to video boundaries
    clip_start = max(0.0, clip_start)
    clip_end = min(video_duration, clip_end)

    return clip_start, clip_end


def _has_overlap(clip_start: float, clip_end: float,
                 selected: list, overlap_sec: float) -> bool:
    """Check if a clip overlaps with any already selected clip."""
    for s in selected:
        overlap = min(clip_end, s["clip_end"]) - max(clip_start, s["clip_start"])
        if overlap >= overlap_sec:
            return True
    return False
