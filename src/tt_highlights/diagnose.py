"""Clip diagnosis engine — missed rally analysis & detection explanation."""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class FilterResult:
    name: str
    passed: bool
    actual: float | int
    threshold: float | int
    suggestion: float | int | None = None
    param_key: str | None = None


@dataclass
class DiagnosisResult:
    """Result of diagnosing why a manual clip was NOT auto-detected."""
    filters: list[FilterResult]
    blocked_by: list[str]
    suggestions: dict[str, float | int]

    @property
    def all_passed(self) -> bool:
        return len(self.blocked_by) == 0


@dataclass
class ExplanationResult:
    """Result of explaining why an auto clip WAS detected."""
    detection_reasons: list[str]
    highlight_explanation: str
    score_breakdown: dict[str, float]
    combined_score: float
    threshold: float
    is_highlight: bool
    suggestions: list[str] = field(default_factory=list)


def diagnose_missed_rally(
    clip_start: float,
    clip_end: float,
    audio_events_path: str | Path,
    activity_path: str | Path,
    config: dict,
) -> DiagnosisResult:
    """Simulate the rally detection pipeline to find why a clip was missed."""
    audio_events_path = Path(audio_events_path)
    activity_path = Path(activity_path)

    with open(audio_events_path, "r", encoding="utf-8") as f:
        audio_events = json.load(f)
    with open(activity_path, "r", encoding="utf-8") as f:
        activity_data = json.load(f)

    scfg = config["segmentation"]
    impacts_all = audio_events["impact_events"]
    activity_samples = activity_data["samples"]

    act_times = np.array([s["t"] for s in activity_samples])
    act_values = np.array([s["activity"] for s in activity_samples])

    filters: list[FilterResult] = []
    suggestions: dict[str, float | int] = {}

    # ── Filter 1: Impact existence in range ──────────────────────────────
    impacts_in_range = [
        imp for imp in impacts_all
        if clip_start <= imp["t"] <= clip_end
    ]
    has_impacts = len(impacts_in_range) > 0
    filters.append(FilterResult(
        name="impact_exists",
        passed=has_impacts,
        actual=len(impacts_in_range),
        threshold=1,
        suggestion=max(0.01, scfg.get("impact_threshold", 0.05) - 0.02)
            if not has_impacts else None,
        param_key="impact_threshold" if not has_impacts else None,
    ))

    if not has_impacts:
        # Check with lower threshold — are there any raw impacts we can find?
        raw_impacts = audio_events.get("impact_events_raw", impacts_all)
        raw_in_range = [
            imp for imp in raw_impacts if clip_start <= imp["t"] <= clip_end
        ]
        if raw_in_range:
            min_score = min(imp["score"] for imp in raw_in_range)
            filters[-1].suggestion = round(max(0.01, min_score - 0.01), 3)

    # ── Filter 2: Adaptive threshold ─────────────────────────────────────
    floor = scfg.get("impact_score_floor", 0.04)
    if len(impacts_all) > 5:
        scores = np.array([imp["score"] for imp in impacts_all])
        adaptive_thresh = max(floor, float(np.percentile(scores, 30)))
    else:
        adaptive_thresh = floor

    impacts_after_adaptive = [
        imp for imp in impacts_in_range if imp["score"] >= adaptive_thresh
    ]
    passed_adaptive = len(impacts_after_adaptive) > 0 or not has_impacts
    if has_impacts and not passed_adaptive:
        max_score_in_range = max(imp["score"] for imp in impacts_in_range)
        suggest_floor = round(max(0.01, max_score_in_range - 0.01), 3)
    else:
        suggest_floor = None
    filters.append(FilterResult(
        name="adaptive_threshold",
        passed=passed_adaptive,
        actual=round(adaptive_thresh, 4),
        threshold=floor,
        suggestion=suggest_floor,
        param_key="impact_score_floor" if not passed_adaptive else None,
    ))

    # Use whichever impacts survived
    working_impacts = impacts_after_adaptive if passed_adaptive else impacts_in_range

    # ── Filter 3: Minimum impact count ───────────────────────────────────
    min_impacts = scfg["min_impacts"]
    count = len(working_impacts)
    passed_count = count >= min_impacts
    filters.append(FilterResult(
        name="min_impacts",
        passed=passed_count,
        actual=count,
        threshold=min_impacts,
        suggestion=max(1, count) if not passed_count and count > 0 else None,
        param_key="min_impacts" if not passed_count else None,
    ))

    # ── Filter 4: Minimum duration ───────────────────────────────────────
    min_dur = scfg["min_rally_duration_sec"]
    dur = clip_end - clip_start
    passed_dur = dur >= min_dur
    filters.append(FilterResult(
        name="min_rally_duration_sec",
        passed=passed_dur,
        actual=round(dur, 2),
        threshold=min_dur,
        suggestion=round(max(0.5, dur - 0.5), 1) if not passed_dur else None,
        param_key="min_rally_duration_sec" if not passed_dur else None,
    ))

    # ── Filter 5: Activity mean ──────────────────────────────────────────
    activity_min = scfg["activity_min_mean"]
    mask = (act_times >= clip_start) & (act_times <= clip_end)
    mean_act = float(act_values[mask].mean()) if mask.any() else 0.0
    passed_activity = mean_act >= activity_min
    filters.append(FilterResult(
        name="activity_min_mean",
        passed=passed_activity,
        actual=round(mean_act, 4),
        threshold=activity_min,
        suggestion=round(max(0.0, mean_act - 0.005), 3)
            if not passed_activity else None,
        param_key="activity_min_mean" if not passed_activity else None,
    ))

    # ── Filter 6: Impact gap ─────────────────────────────────────────────
    gap_max = scfg["impact_gap_max_sec"]
    if len(working_impacts) >= 2:
        max_gap = max(
            working_impacts[j + 1]["t"] - working_impacts[j]["t"]
            for j in range(len(working_impacts) - 1)
        )
    else:
        max_gap = 0.0
    passed_gap = max_gap <= gap_max or len(working_impacts) < 2
    filters.append(FilterResult(
        name="impact_gap_max_sec",
        passed=passed_gap,
        actual=round(max_gap, 2),
        threshold=gap_max,
        suggestion=round(max_gap + 0.5, 1) if not passed_gap else None,
        param_key="impact_gap_max_sec" if not passed_gap else None,
    ))

    blocked_by = [f.name for f in filters if not f.passed]
    for f in filters:
        if not f.passed and f.suggestion is not None and f.param_key:
            suggestions[f.param_key] = f.suggestion

    return DiagnosisResult(
        filters=filters,
        blocked_by=blocked_by,
        suggestions=suggestions,
    )


def explain_detected_rally(
    clip: dict,
    audio_events_path: str | Path,
    activity_path: str | Path,
    config: dict,
) -> ExplanationResult:
    """Explain why an auto-detected clip was detected and its highlight status."""
    audio_events_path = Path(audio_events_path)
    activity_path = Path(activity_path)

    with open(activity_path, "r", encoding="utf-8") as f:
        activity_data = json.load(f)
    activity_samples = activity_data["samples"]
    act_times = np.array([s["t"] for s in activity_samples])
    act_values = np.array([s["activity"] for s in activity_samples])

    scfg = config["segmentation"]
    hl_cfg = config.get("highlights", {})

    ca = clip.get("conf_audio", 0)
    cv_raw = clip.get("conf_video", 0)
    cv_norm = clip.get("conf_video_norm", 0)
    rhythm = clip.get("rhythm_score", 0)
    impact_count = clip.get("impact_count", 0)
    reason_end = clip.get("reason_end", "")

    # Recompute mean activity
    mask = (act_times >= clip["clip_start"]) & (act_times <= clip["clip_end"])
    mean_act = float(act_values[mask].mean()) if mask.any() else 0.0

    # Detection reasons
    reasons = []
    reasons.append(f"{impact_count} impacts detected")
    reasons.append(f"mean activity {mean_act:.3f} (threshold {scfg['activity_min_mean']})")
    reasons.append(f"rhythm score {rhythm:.2f}")
    if reason_end:
        reasons.append(f"end reason: {reason_end}")

    # Highlight score
    vid_floor = hl_cfg.get("video_floor", 0.03)
    threshold = hl_cfg.get("auto_threshold", 0.4)

    if cv_raw < vid_floor:
        combined = 0.0
    else:
        combined = ca * 0.3 + cv_norm * 0.5 + rhythm * 0.2
    combined = round(combined, 4)

    is_hl = clip.get("is_highlight", False)

    if is_hl:
        hl_explanation = (
            f"Highlighted: score {combined:.2f} >= threshold {threshold}"
        )
    else:
        hl_explanation = (
            f"Not highlighted: score {combined:.2f} < threshold {threshold}"
        )

    breakdown = {
        "conf_audio": round(ca, 4),
        "conf_audio_weighted": round(ca * 0.3, 4),
        "conf_video_norm": round(cv_norm, 4),
        "conf_video_weighted": round(cv_norm * 0.5, 4),
        "rhythm_score": round(rhythm, 4),
        "rhythm_weighted": round(rhythm * 0.2, 4),
    }

    # Suggestions for false positives (detected but user un-highlighted)
    suggestions = []
    if not is_hl and combined >= threshold:
        # User manually un-highlighted — might be false positive
        if mean_act < 0.08:
            suggestions.append(
                f"raise activity_min_mean to {mean_act + 0.01:.3f}"
            )
        if impact_count <= 3:
            suggestions.append(
                f"raise min_impacts to {impact_count + 1}"
            )
    elif is_hl and combined < threshold:
        # User manually highlighted — lower threshold?
        suggestions.append(
            f"lower auto_threshold to {combined - 0.05:.2f}"
        )

    return ExplanationResult(
        detection_reasons=reasons,
        highlight_explanation=hl_explanation,
        score_breakdown=breakdown,
        combined_score=combined,
        threshold=threshold,
        is_highlight=is_hl,
        suggestions=suggestions,
    )
