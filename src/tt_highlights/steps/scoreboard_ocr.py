"""Step: scoreboard_ocr – detect score changes via OCR (optional)."""

import json
import logging
from pathlib import Path

import cv2
import numpy as np

from ..job import artifacts_dir, debug_dir

logger = logging.getLogger(__name__)


def run(job: dict, config: dict, job_path: str) -> None:
    """Execute the scoreboard_ocr step."""
    art = artifacts_dir(job_path)
    dbg = debug_dir(job_path)

    ocr_cfg = config["ocr"]

    # Check if OCR is enabled
    if not ocr_cfg.get("enabled", True):
        logger.info("OCR disabled in config. Writing disabled output.")
        _write_disabled(art)
        return

    # Check for scoreboard ROI
    sb_path = art / "scoreboard_roi.json"
    if not sb_path.exists():
        logger.info("No scoreboard_roi.json found. Skipping OCR.")
        _write_disabled(art)
        return

    with open(sb_path, "r", encoding="utf-8") as f:
        sb_data = json.load(f)

    if not sb_data.get("enabled", False):
        logger.info("Scoreboard ROI is disabled. Skipping OCR.")
        _write_disabled(art)
        return

    # Scale scoreboard coordinates from original to proxy resolution
    from ..job import proxy_scale
    psx, psy = proxy_scale(job_path)

    rect = sb_data["rect"]
    rx = int(rect["x"] * psx)
    ry = int(rect["y"] * psy)
    rw = int(rect["w"] * psx)
    rh = int(rect["h"] * psy)
    if rw <= 0 or rh <= 0:
        logger.info("Scoreboard ROI has zero size. Skipping OCR.")
        _write_disabled(art)
        return

    # Build perspective warp from polygon if available (handles tilted scoreboards)
    sb_polygon = sb_data.get("polygon")
    sb_warp_matrix = None
    if sb_polygon and len(sb_polygon) == 4:
        import numpy as np
        src_pts = np.array(
            [[int(p[0] * psx), int(p[1] * psy)] for p in sb_polygon],
            dtype=np.float32,
        )
        dst_pts = np.array(
            [[0, 0], [rw, 0], [rw, rh], [0, rh]], dtype=np.float32,
        )
        sb_warp_matrix = cv2.getPerspectiveTransform(src_pts, dst_pts)

    # Load rallies
    with open(art / "rallies.json", "r", encoding="utf-8") as f:
        rallies_data = json.load(f)

    rallies = rallies_data["rallies"]
    if not rallies:
        logger.info("No rallies to process. Writing empty OCR events.")
        _write_disabled(art)
        return

    proxy_path = art / "proxy.mp4"
    if not proxy_path.exists():
        raise FileNotFoundError("proxy.mp4 not found.")

    import pytesseract

    sample_fps = ocr_cfg["sample_fps"]
    window_pre = ocr_cfg["window_pre_sec"]
    window_post = ocr_cfg["window_post_sec"]
    whitelist = ocr_cfg["whitelist"]

    cap = cv2.VideoCapture(str(proxy_path))
    video_fps = cap.get(cv2.CAP_PROP_FPS)
    if video_fps <= 0:
        video_fps = 30.0

    events = []
    ocr_samples_dir = dbg / "ocr_samples"
    ocr_samples_dir.mkdir(parents=True, exist_ok=True)

    for rally in rallies:
        rid = rally["id"]
        end_t = rally.get("end_refined", rally["end"])

        # Sample window around rally end
        t_start = max(0.0, end_t - window_pre)
        t_end = end_t + window_post

        scores_timeline = []
        sample_interval = 1.0 / sample_fps

        t = t_start
        while t <= t_end:
            frame_num = int(t * video_fps)
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
            ret, frame = cap.read()
            if not ret:
                t += sample_interval
                continue

            # Crop ROI (use perspective warp if polygon available)
            if sb_warp_matrix is not None:
                roi = cv2.warpPerspective(frame, sb_warp_matrix, (rw, rh))
            else:
                roi = frame[ry:ry + rh, rx:rx + rw]
            if roi.size == 0:
                t += sample_interval
                continue

            # Preprocess for OCR
            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            _, thresh = cv2.threshold(gray, 0, 255,
                                      cv2.THRESH_BINARY + cv2.THRESH_OTSU)

            # OCR
            custom_config = f'--oem 3 --psm 7 -c tessedit_char_whitelist={whitelist}'
            text = pytesseract.image_to_string(thresh, config=custom_config).strip()

            # Parse digits
            digits = ''.join(c for c in text if c.isdigit())
            if digits:
                scores_timeline.append({"t": round(t, 3), "text": digits})

            t += sample_interval

        if len(scores_timeline) < 2:
            continue

        # Find score change point
        change_event = _find_score_change(scores_timeline, rid)
        if change_event:
            events.append(change_event)

            # Update rallies end_refined
            if change_event["confidence"] > 0.5:
                rally["end_refined"] = change_event["t"]

    cap.release()

    # Write ocr_events.json
    output = {
        "enabled": True,
        "events": events,
    }
    with open(art / "ocr_events.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    # Update rallies.json with refined ends
    with open(art / "rallies.json", "w", encoding="utf-8") as f:
        json.dump(rallies_data, f, indent=2)

    logger.info(f"OCR: {len(events)} score change events detected.")


def _find_score_change(timeline: list[dict], rally_id: int) -> dict | None:
    """Find the first score change in the OCR timeline."""
    if len(timeline) < 2:
        return None

    # Use majority vote to stabilize: find the most common text before and after
    texts = [t["text"] for t in timeline]
    mid = len(texts) // 2

    before_texts = texts[:mid]
    after_texts = texts[mid:]

    if not before_texts or not after_texts:
        return None

    before_mode = max(set(before_texts), key=before_texts.count)
    after_mode = max(set(after_texts), key=after_texts.count)

    if before_mode == after_mode:
        return None

    # Find the transition point
    for i in range(1, len(timeline)):
        if timeline[i]["text"] != timeline[i - 1]["text"]:
            # Compute confidence based on consistency
            consistent_after = sum(1 for t in texts[i:] if t == after_mode)
            confidence = consistent_after / max(1, len(texts[i:]))

            # Compute delta (try parsing as integers)
            delta = _parse_score_delta(before_mode, after_mode)

            # Determine scorer side from delta
            scorer_side = None
            if delta[0] > 0 and delta[1] == 0:
                scorer_side = "left"
            elif delta[1] > 0 and delta[0] == 0:
                scorer_side = "right"

            return {
                "rally_id": rally_id,
                "t": timeline[i]["t"],
                "delta": delta,
                "scorer_side": scorer_side,
                "confidence": round(confidence, 3),
            }

    return None


def _parse_score_delta(before: str, after: str) -> list[int]:
    """Try to parse score difference. Returns [delta_left, delta_right]."""
    try:
        # Assume scores are concatenated like "119" meaning 11-9
        # Simple heuristic: if length is even, split in half
        if len(before) == len(after) and len(before) >= 2:
            mid = len(before) // 2
            b_left, b_right = int(before[:mid]), int(before[mid:])
            a_left, a_right = int(after[:mid]), int(after[mid:])
            return [a_left - b_left, a_right - b_right]
    except (ValueError, IndexError):
        pass
    return [0, 0]


def _write_disabled(art: Path) -> None:
    """Write disabled ocr_events.json."""
    output = {"enabled": False, "events": []}
    with open(art / "ocr_events.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
