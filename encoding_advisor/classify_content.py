from __future__ import annotations


COMPLEXITY_MULTIPLIERS = {
    "low": 0.75,
    "medium": 1.0,
    "high": 1.25,
    "very_high": 1.4,
    "sports": 1.35,
    "gaming": 1.35,
    "animation": 0.9,
    "screen": 0.85,
}


def normalize_complexity(value: str) -> dict[str, float | str]:
    normalized = value.lower().strip()
    return {
        "label": normalized,
        "bitrate_multiplier": COMPLEXITY_MULTIPLIERS.get(normalized, 1.0),
    }
