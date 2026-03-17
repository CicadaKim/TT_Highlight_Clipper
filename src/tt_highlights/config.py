"""Config loading utilities."""

import os
import yaml
from pathlib import Path

_DEFAULT_CONFIG = Path(__file__).parent / "default_config.yaml"


def load_default_config() -> dict:
    """Load the built-in default config."""
    with open(_DEFAULT_CONFIG, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_config(path: str | Path) -> dict:
    """Load a config.yaml, merging with defaults."""
    defaults = load_default_config()
    with open(path, "r", encoding="utf-8") as f:
        user = yaml.safe_load(f) or {}
    return _deep_merge(defaults, user)


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base."""
    merged = base.copy()
    for k, v in override.items():
        if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged
