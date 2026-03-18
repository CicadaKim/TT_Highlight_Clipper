"""Step: setup – detect table polygon and optional scoreboard ROI.

This step runs immediately after preprocess. It produces:
  - table_roi.json   (4-point polygon + confidence + frame_id)
  - scoreboard_roi.json  (rect + source + confidence + frame_id)
  - setup_state.json (completion gate for downstream vision steps)

In CLI mode with --auto-accept-setup, results are accepted automatically.
In interactive (UI) mode the user can review/edit proposals before saving.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from ..job import artifacts_dir, debug_dir
from .table_roi import _detect_table_polygon, _draw_overlay

logger = logging.getLogger(__name__)


def run(job: dict, config: dict, job_path: str, *, auto_accept: bool = False) -> None:
    """Execute the setup step — auto-propose ROIs and write artifacts.

    Parameters
    ----------
    auto_accept : bool
        When True, mark setup as completed even if confidence is low.
        When False (default), low-confidence results leave setup incomplete
        so the user can review in the UI.
    """
    art = artifacts_dir(job_path)
    dbg = debug_dir(job_path)
    dbg.mkdir(parents=True, exist_ok=True)

    frame0_path = art / "frame0.jpg"
    if not frame0_path.exists():
        raise FileNotFoundError("frame0.jpg not found. Run preprocess first.")

    frame = cv2.imread(str(frame0_path))
    if frame is None:
        raise RuntimeError(f"Could not read {frame0_path}")

    h, w = frame.shape[:2]

    # ── Table polygon proposal (skip if manual ROI exists) ────────────────
    roi_path = art / "table_roi.json"
    if _is_manual_roi(roi_path):
        logger.info("Existing manual table ROI found — skipping auto-detect.")
        with open(roi_path, "r", encoding="utf-8") as f:
            roi_data = json.load(f)
        polygon = roi_data["table_polygon"]
        confidence = roi_data.get("confidence", 1.0)
        method = roi_data.get("source", "manual")
    else:
        polygon, confidence, method = _auto_propose_table(frame)
        roi_data = {
            "table_polygon": polygon,
            "polygon_order": "clockwise",
            "source": method,
            "confidence": round(confidence, 4),
            "frame_id": 0,
            "frame_size": {"w": w, "h": h},
        }
        with open(roi_path, "w", encoding="utf-8") as f:
            json.dump(roi_data, f, indent=2)

    # ── Scoreboard ROI proposal (skip if manual or enabled) ───────────────
    sb_path = art / "scoreboard_roi.json"
    if _should_preserve_scoreboard(sb_path):
        logger.info("Existing manual/enabled scoreboard ROI found — keeping.")
    else:
        sb_data = {
            "enabled": False,
            "rect": {"x": 0, "y": 0, "w": 0, "h": 0},
            "source": "none",
            "confidence": 0.0,
            "frame_id": 0,
        }
        with open(sb_path, "w", encoding="utf-8") as f:
            json.dump(sb_data, f, indent=2)

    # ── Player zones (opt-in) ──────────────────────────────────────────────
    pz_cfg = config.get("player_zones", {})
    pz_path = art / "player_zones.json"
    if pz_cfg.get("enabled", False):
        if _is_manual_zones(pz_path):
            logger.info("Existing manual player zones found — keeping.")
        elif pz_cfg.get("auto_derive", True):
            zones = _auto_derive_zones(polygon, h, w, pz_cfg)
            pz_data = {
                "source": "auto",
                "zones": zones,
                "player_a_score_side": "left",
                "frame_size": {"w": w, "h": h},
            }
            with open(pz_path, "w", encoding="utf-8") as f:
                json.dump(pz_data, f, indent=2)
            logger.info(f"Player zones auto-derived: {len(zones)} zones")

    # ── Debug overlay ─────────────────────────────────────────────────────
    _draw_overlay(frame, polygon, dbg / "frame0_overlay.png")

    # ── Setup state ───────────────────────────────────────────────────────
    warnings = []
    requires_review = confidence < 0.6
    if requires_review:
        warnings.append(
            f"Low table detection confidence ({confidence:.2f}). "
            "Manual review recommended."
        )

    # Gate: only mark completed if confidence is high enough OR auto_accept
    completed = (not requires_review) or auto_accept

    state = {
        "completed": completed,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "requires_review": requires_review,
        "warnings": warnings,
    }
    with open(art / "setup_state.json", "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

    logger.info(
        f"Setup done: table_roi via {method} (conf={confidence:.2f}), "
        f"requires_review={requires_review}, completed={completed}"
    )


def _is_manual_roi(roi_path: Path) -> bool:
    """Return True if roi_path exists and was manually set."""
    if not roi_path.exists():
        return False
    with open(roi_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("source") == "manual"


def _should_preserve_scoreboard(sb_path: Path) -> bool:
    """Return True if scoreboard ROI exists and should not be overwritten."""
    if not sb_path.exists():
        return False
    with open(sb_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("source") == "manual" or data.get("enabled") is True


def _auto_propose_table(
    frame: np.ndarray,
) -> tuple[list[list[int]], float, str]:
    """Run auto-detection and return (polygon, confidence, method).

    Reuses _detect_table_polygon from table_roi.py which tries
    edge → color → center-fallback strategies in order.
    """
    h, w = frame.shape[:2]

    # Strategy 1: Edge-based
    from .table_roi import _detect_via_edges, _detect_via_color

    polygon = _detect_via_edges(frame)
    if polygon is not None:
        confidence = _assess_confidence(polygon, h, w)
        return polygon, confidence, "auto_edge"

    # Strategy 2: Color-based
    polygon = _detect_via_color(frame)
    if polygon is not None:
        confidence = _assess_confidence(polygon, h, w) * 0.9  # slightly lower
        return polygon, confidence, "auto_color"

    # Strategy 3: Center-biased fallback
    polygon = _detect_table_polygon(frame)  # will return fallback
    return polygon, 0.3, "auto_fallback"


def _assess_confidence(
    polygon: list[list[int]], frame_h: int, frame_w: int,
) -> float:
    """Heuristic confidence score for a detected polygon (0~1)."""
    pts = np.array(polygon, dtype=np.float32)
    area = cv2.contourArea(pts)
    frame_area = frame_h * frame_w

    # Area ratio: expect table to be 10%-60% of frame
    area_ratio = area / frame_area
    if area_ratio < 0.05 or area_ratio > 0.8:
        return 0.2

    # Convexity check
    hull = cv2.convexHull(pts)
    hull_area = cv2.contourArea(hull)
    convexity = area / hull_area if hull_area > 0 else 0

    # Aspect ratio: table tennis table is roughly 2.7:1 → expect width > height
    rect = cv2.minAreaRect(pts)
    (_, _), (rw, rh), _ = rect
    if rw < rh:
        rw, rh = rh, rw
    aspect = rw / rh if rh > 0 else 0

    # Score components
    area_score = min(1.0, area_ratio / 0.3)  # peaks around 30% coverage
    convex_score = convexity
    # Table tennis aspect ~2.7, but perspective varies → accept 1.5-4.0
    aspect_score = max(0.0, 1.0 - abs(aspect - 2.7) / 3.0) if aspect > 0 else 0

    confidence = area_score * 0.4 + convex_score * 0.3 + aspect_score * 0.3
    return max(0.1, min(1.0, confidence))


def _is_manual_zones(pz_path: Path) -> bool:
    """Return True if player_zones.json exists and was manually set."""
    if not pz_path.exists():
        return False
    with open(pz_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("source") == "manual"


def _auto_derive_zones(
    polygon: list[list[int]], frame_h: int, frame_w: int, pz_cfg: dict,
) -> list[dict]:
    """Derive two player zones from table polygon short edges.

    Algorithm:
    1. Find the 4 edges of the polygon.
    2. Identify the two shortest edges (player-side ends of the table).
    3. For each short edge, expand outward by margin_px to create a zone rect.
    """
    pts = np.array(polygon, dtype=np.float32)
    n = len(pts)
    if n != 4:
        # Fallback: split frame vertically into two halves
        return _fallback_zones(frame_h, frame_w, pz_cfg)

    # Compute edge lengths
    edges = []
    for i in range(n):
        p1 = pts[i]
        p2 = pts[(i + 1) % n]
        length = float(np.linalg.norm(p2 - p1))
        mid = ((p1 + p2) / 2).tolist()
        edges.append({
            "i": i,
            "p1": p1.tolist(),
            "p2": p2.tolist(),
            "length": length,
            "mid": mid,
        })

    # Sort by length — two shortest edges are the player sides
    edges.sort(key=lambda e: e["length"])
    short_edges = edges[:2]

    margin_px = pz_cfg.get("margin_px", 150)
    # Cap margin to 20% of frame height
    margin_px = min(margin_px, int(frame_h * 0.2))

    zones = []
    for idx, edge in enumerate(short_edges):
        p1 = np.array(edge["p1"])
        p2 = np.array(edge["p2"])
        mid = np.array(edge["mid"])

        # Compute outward direction: perpendicular to edge, pointing away from polygon center
        center = pts.mean(axis=0)
        edge_vec = p2 - p1
        # Normal perpendicular to edge
        normal = np.array([-edge_vec[1], edge_vec[0]], dtype=np.float32)
        normal = normal / (np.linalg.norm(normal) + 1e-8)
        # Ensure normal points away from polygon center
        if np.dot(normal, mid - center) < 0:
            normal = -normal

        # Zone corners: expand from the edge outward
        outer_p1 = p1 + normal * margin_px
        outer_p2 = p2 + normal * margin_px

        # Clamp to frame bounds
        zone_poly = np.array([p1, p2, outer_p2, outer_p1])
        zone_poly[:, 0] = np.clip(zone_poly[:, 0], 0, frame_w)
        zone_poly[:, 1] = np.clip(zone_poly[:, 1], 0, frame_h)
        polygon_pts = [[int(p[0]), int(p[1])] for p in zone_poly]

        # Bounding rect for backward compat / cropping
        x_min = max(0, int(zone_poly[:, 0].min()))
        y_min = max(0, int(zone_poly[:, 1].min()))
        x_max = min(frame_w, int(zone_poly[:, 0].max()))
        y_max = min(frame_h, int(zone_poly[:, 1].max()))

        label = "near" if idx == 0 else "far"
        # Heuristic: the zone closer to the bottom of the frame is "near"
        if short_edges[0]["mid"][1] < short_edges[1]["mid"][1]:
            label = "far" if idx == 0 else "near"

        zones.append({
            "label": label,
            "polygon": polygon_pts,
            "rect": {"x": x_min, "y": y_min, "w": x_max - x_min, "h": y_max - y_min},
            "edge_pts": [edge["p1"], edge["p2"]],
        })

    return zones


def _fallback_zones(frame_h: int, frame_w: int, pz_cfg: dict) -> list[dict]:
    """Fallback: split frame into top/bottom halves."""
    half = frame_h // 2
    margin = pz_cfg.get("margin_px", 150)
    far_h = half + margin
    near_y = half - margin
    near_h = frame_h - near_y
    return [
        {
            "label": "far",
            "polygon": [[0, 0], [frame_w, 0], [frame_w, far_h], [0, far_h]],
            "rect": {"x": 0, "y": 0, "w": frame_w, "h": far_h},
            "edge_pts": [],
        },
        {
            "label": "near",
            "polygon": [[0, near_y], [frame_w, near_y], [frame_w, frame_h], [0, frame_h]],
            "rect": {"x": 0, "y": near_y, "w": frame_w, "h": near_h},
            "edge_pts": [],
        },
    ]


def is_setup_complete(job_path: str) -> bool:
    """Check whether setup has been completed for a job."""
    art = artifacts_dir(job_path)
    state_path = art / "setup_state.json"
    if not state_path.exists():
        return False
    with open(state_path, "r", encoding="utf-8") as f:
        state = json.load(f)
    return state.get("completed", False)
