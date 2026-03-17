"""Step: scoring – compute category-wise scores for each rally."""

import json
import logging
from pathlib import Path

from ..job import artifacts_dir

logger = logging.getLogger(__name__)

CATEGORIES = ["long_rally", "impact", "reaction"]


def run(job: dict, config: dict, job_path: str) -> None:
    """Execute the scoring step."""
    art = artifacts_dir(job_path)

    with open(art / "features.json", "r", encoding="utf-8") as f:
        features_data = json.load(f)

    weights = config["scoring"]["weights"]
    minimums = config.get("scoring", {}).get("minimums", {})
    rally_features = features_data["rally_features"]

    candidates = {}
    for category in CATEGORIES:
        cat_weights = weights.get(category, {})
        scored = []

        for feat in rally_features:
            score = 0.0
            reasons = []

            for feature_name, weight in cat_weights.items():
                # Use normalized value if available, fallback to raw/flat
                norm = feat.get("norm", feat)
                raw_value = feat.get("raw", feat).get(feature_name, 0) or 0
                value = norm.get(feature_name, 0) or 0
                contribution = value * weight
                score += contribution
                if contribution > 0:
                    reasons.append({
                        "feature": feature_name,
                        "raw": round(float(raw_value), 4),
                        "norm": round(float(value), 4),
                        "weight": weight,
                        "contribution": round(contribution, 4),
                    })

            # Sort reasons by contribution (descending)
            reasons.sort(key=lambda r: r["contribution"], reverse=True)
            scored.append({
                "rally_id": feat["rally_id"],
                "score": round(score, 4),
                "reasons": reasons[:5],
            })

        # Sort by score descending
        scored.sort(key=lambda x: x["score"], reverse=True)
        candidates[category] = scored

    output = {"candidates": candidates}
    with open(art / "scores.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    for cat in CATEGORIES:
        top = candidates[cat][:3]
        top_str = ", ".join(f"R{c['rally_id']}({c['score']:.1f})" for c in top)
        logger.info(f"  {cat}: {top_str}")

    logger.info(f"Scoring done for {len(rally_features)} rallies across {len(CATEGORIES)} categories.")
