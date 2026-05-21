from __future__ import annotations

from typing import Any

from .recommendation import decision


ASPECT_POLICIES = ["fit", "fill", "stretch", "native"]


def recommend_aspect_policy(source: dict[str, Any], ladder: list[dict[str, Any]]) -> dict[str, Any]:
    source_ratio = _source_aspect_ratio(source)
    ladder_ratio = _dominant_ladder_ratio(ladder)

    if source_ratio and ladder_ratio and not _ratios_close(source_ratio, ladder_ratio):
        policy = "fit"
        reason = (
            f"Source aspect ratio {source_ratio:.3f}:1 does not match the ladder aspect ratio "
            f"{ladder_ratio:.3f}:1. Fit preserves the full frame and pads to the target rendition size."
        )
        rule_id = "aspect.non_standard_source.fit"
        confidence = "high"
    else:
        policy = "fit"
        reason = "Fit preserves source geometry while still producing the exact ladder frame sizes expected by packaging and validation."
        rule_id = "aspect.standard.fit"
        confidence = "medium"

    return {
        "aspect_policy": policy,
        "rationale": reason,
        "decisions": {
            "aspect_policy": decision(
                "aspect_policy",
                policy,
                reason,
                "advisor_policy",
                rule_id,
                confidence,
            )
        },
    }


def _source_aspect_ratio(source: dict[str, Any]) -> float | None:
    video = source.get("video", {}) if isinstance(source, dict) else {}
    width = _positive_int(video.get("width"))
    height = _positive_int(video.get("height"))
    if not width or not height:
        return None
    return width / height


def _dominant_ladder_ratio(ladder: list[dict[str, Any]]) -> float | None:
    ratios: list[float] = []
    for rendition in ladder:
        parsed = _parse_resolution(rendition.get("resolution"))
        if parsed:
            width, height = parsed
            ratios.append(width / height)
    if not ratios:
        return None
    return max(set(ratios), key=ratios.count)


def _parse_resolution(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, str) or "x" not in value:
        return None
    raw_width, raw_height = value.lower().split("x", 1)
    width = _positive_int(raw_width)
    height = _positive_int(raw_height)
    if not width or not height:
        return None
    return width, height


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _ratios_close(a: float, b: float, tolerance: float = 0.01) -> bool:
    return abs(a - b) / b <= tolerance
