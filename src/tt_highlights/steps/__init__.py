"""Pipeline steps package."""

# Step execution order
STEP_ORDER = [
    "preprocess",
    "setup",
    "audio_events",
    "video_activity",
    "rally_segment",
    "scoreboard_ocr",
    "ball_tracking",
    "features",
    "scoring",
    "selection",
    "export",
]


def get_step_function(step_name: str):
    """Import and return the run() function for a given step."""
    if step_name not in STEP_ORDER:
        raise ValueError(
            f"Unknown step: {step_name}. Valid steps: {STEP_ORDER}"
        )
    module = __import__(
        f"tt_highlights.steps.{step_name}", fromlist=["run"]
    )
    return module.run
