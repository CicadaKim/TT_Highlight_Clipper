"""Step: table_roi – auto-detect table polygon + generate overlay."""

import json
import logging
from pathlib import Path

import cv2
import numpy as np

from ..job import artifacts_dir, debug_dir

logger = logging.getLogger(__name__)


def run(job: dict, config: dict, job_path: str) -> None:
    """Execute the table_roi step."""
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
    warp_w = config["video"]["warp_width"]
    warp_h = config["video"]["warp_height"]

    # Try auto-detection
    polygon = _detect_table_polygon(frame)

    # Write table_roi.json
    roi_data = {
        "table_polygon": polygon,
        "polygon_order": "clockwise",
        "source": "auto",
        "frame_size": {"w": w, "h": h},
    }
    roi_path = art / "table_roi.json"
    with open(roi_path, "w", encoding="utf-8") as f:
        json.dump(roi_data, f, indent=2)

    # Write default scoreboard_roi.json (disabled)
    sb_path = art / "scoreboard_roi.json"
    if not sb_path.exists():
        sb_data = {
            "enabled": False,
            "rect": {"x": 0, "y": 0, "w": 0, "h": 0},
        }
        with open(sb_path, "w", encoding="utf-8") as f:
            json.dump(sb_data, f, indent=2)

    # Generate debug overlay
    _draw_overlay(frame, polygon, dbg / "frame0_overlay.png")

    logger.info(f"Table ROI detected: {polygon}")


def _detect_table_polygon(frame: np.ndarray) -> list[list[int]]:
    """Auto-detect the table as a 4-point polygon.

    Strategy:
      1) Canny + HoughLinesP → find dominant quad
      2) Fallback: HSV/threshold → largest contour → approxPolyDP
      3) Last resort: center-biased rectangle
    """
    h, w = frame.shape[:2]

    # Strategy 1: Edge-based detection
    polygon = _detect_via_edges(frame)
    if polygon is not None:
        logger.info("Table detected via edge method.")
        return polygon

    # Strategy 2: Color/threshold-based detection
    polygon = _detect_via_color(frame)
    if polygon is not None:
        logger.info("Table detected via color method.")
        return polygon

    # Strategy 3: Center-biased fallback
    logger.warning("Auto-detection failed. Using center-biased rectangle.")
    cx, cy = w // 2, h // 2
    rw, rh = int(w * 0.5), int(h * 0.4)
    return [
        [cx - rw // 2, cy - rh // 2],
        [cx + rw // 2, cy - rh // 2],
        [cx + rw // 2, cy + rh // 2],
        [cx - rw // 2, cy + rh // 2],
    ]


def _detect_via_edges(frame: np.ndarray) -> list[list[int]] | None:
    """Detect table via Canny edges and HoughLinesP."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)

    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=100,
                            minLineLength=100, maxLineGap=20)
    if lines is None or len(lines) < 4:
        return None

    # Separate into roughly horizontal and vertical lines
    h_lines = []
    v_lines = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        angle = np.degrees(np.arctan2(abs(y2 - y1), abs(x2 - x1)))
        if angle < 30:
            h_lines.append((x1, y1, x2, y2))
        elif angle > 60:
            v_lines.append((x1, y1, x2, y2))

    if len(h_lines) < 2 or len(v_lines) < 2:
        return None

    # Sort horizontal lines by y-midpoint, pick top and bottom
    h_lines.sort(key=lambda l: (l[1] + l[3]) / 2)
    top_line = h_lines[0]
    bottom_line = h_lines[-1]

    # Sort vertical lines by x-midpoint, pick left and right
    v_lines.sort(key=lambda l: (l[0] + l[2]) / 2)
    left_line = v_lines[0]
    right_line = v_lines[-1]

    # Compute intersections of the 4 border lines
    corners = []
    for hl in [top_line, bottom_line]:
        for vl in [left_line, right_line]:
            pt = _line_intersection(hl, vl)
            if pt is not None:
                corners.append(pt)

    if len(corners) != 4:
        return None

    # Order clockwise from top-left
    polygon = _order_points_clockwise(corners)

    # Validate: convex and reasonable size
    h, w = frame.shape[:2]
    area = cv2.contourArea(np.array(polygon, dtype=np.float32))
    if area < (w * h * 0.05) or area > (w * h * 0.9):
        return None

    return [[int(p[0]), int(p[1])] for p in polygon]


def _detect_via_color(frame: np.ndarray) -> list[list[int]] | None:
    """Detect table via color thresholding (green/blue table surfaces)."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    h, w = frame.shape[:2]

    # Try green table first, then blue
    for lower, upper in [
        (np.array([35, 40, 40]), np.array([85, 255, 255])),   # green
        (np.array([90, 40, 40]), np.array([130, 255, 255])),   # blue
    ]:
        mask = cv2.inRange(hsv, lower, upper)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                                np.ones((15, 15), np.uint8))

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue

        largest = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(largest)
        if area < (w * h * 0.05):
            continue

        # Approximate to 4 points
        epsilon = 0.02 * cv2.arcLength(largest, True)
        approx = cv2.approxPolyDP(largest, epsilon, True)

        if len(approx) == 4:
            pts = approx.reshape(4, 2).tolist()
            pts = _order_points_clockwise(pts)
            return [[int(p[0]), int(p[1])] for p in pts]

        # If not exactly 4, use bounding rect of the contour
        rect = cv2.minAreaRect(largest)
        box = cv2.boxPoints(rect)
        pts = _order_points_clockwise(box.tolist())
        return [[int(p[0]), int(p[1])] for p in pts]

    return None


def _line_intersection(line1: tuple, line2: tuple) -> list[float] | None:
    """Find intersection point of two line segments (extended to full lines)."""
    x1, y1, x2, y2 = line1
    x3, y3, x4, y4 = line2

    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denom) < 1e-6:
        return None

    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
    ix = x1 + t * (x2 - x1)
    iy = y1 + t * (y2 - y1)
    return [ix, iy]


def _order_points_clockwise(pts: list) -> list:
    """Order 4 points in clockwise order starting from top-left."""
    pts = np.array(pts, dtype=np.float32)
    center = pts.mean(axis=0)
    angles = np.arctan2(pts[:, 1] - center[1], pts[:, 0] - center[0])
    order = np.argsort(angles)
    ordered = pts[order].tolist()

    # Rotate so that top-left (min x+y) is first
    sums = [p[0] + p[1] for p in ordered]
    start_idx = sums.index(min(sums))
    return ordered[start_idx:] + ordered[:start_idx]


def _draw_overlay(frame: np.ndarray, polygon: list, out_path: Path) -> None:
    """Draw polygon overlay on frame and save."""
    vis = frame.copy()
    pts = np.array(polygon, dtype=np.int32).reshape((-1, 1, 2))
    cv2.polylines(vis, [pts], isClosed=True, color=(0, 255, 0), thickness=3)
    for i, pt in enumerate(polygon):
        cv2.circle(vis, (pt[0], pt[1]), 8, (0, 0, 255), -1)
        cv2.putText(vis, str(i), (pt[0] + 10, pt[1] - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
    cv2.imwrite(str(out_path), vis)
