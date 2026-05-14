from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"


@lru_cache(maxsize=None)
def load_yaml_config(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return data


def advisor_rules() -> dict[str, Any]:
    return load_yaml_config("advisor_rules.yaml")


def ladder_presets() -> dict[str, Any]:
    return load_yaml_config("ladder_presets.yaml")
