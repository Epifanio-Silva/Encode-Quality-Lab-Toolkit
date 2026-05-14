from __future__ import annotations

from typing import Any

from .config_loader import advisor_rules, ladder_presets
from .recommendation import decision
from .rule_matcher import first_matching_rule


def recommend_ladder(
    source: dict[str, Any],
    codec: str,
    use_case: str,
    priority: str,
    complexity: dict[str, float | str],
) -> list[dict[str, str]]:
    selection_rule = _select_ladder_rule(source, codec, use_case, priority)
    preset_name = selection_rule["preset"]
    multiplier = float(complexity["bitrate_multiplier"])
    source_height = (source.get("video") or {}).get("height")

    ladder = []
    for rendition in ladder_presets()["presets"][preset_name]:
        resolution = rendition["resolution"]
        bitrate = _parse_kbps(rendition["bitrate"])
        height = int(resolution.split("x")[1])
        if source_height and height > source_height:
            continue
        adjusted = _round_bitrate(bitrate * multiplier)
        ladder.append(
            {
                "resolution": resolution,
                "bitrate": f"{adjusted}k",
                "basis": "preset",
                "rule_id": f"ladder.preset.{preset_name}",
            }
        )

    return ladder or [{"resolution": "426x240", "bitrate": f"{_round_bitrate(450 * multiplier)}k", "basis": "preset", "rule_id": f"ladder.preset.{preset_name}"}]


def recommend_ladder_decision(
    ladder: list[dict[str, str]],
    source: dict[str, Any],
    codec: str,
    use_case: str,
    priority: str,
    complexity: dict[str, float | str],
) -> dict[str, Any]:
    selection_rule = _select_ladder_rule(source, codec, use_case, priority)
    preset_name = selection_rule["preset"]
    return decision(
        "ladder",
        ladder,
        f"{selection_rule['reason']} Applied content complexity multiplier {complexity['bitrate_multiplier']}.",
        "preset",
        selection_rule["id"],
        "medium",
    )


def _select_ladder_rule(source: dict[str, Any], codec: str, use_case: str, priority: str) -> dict[str, Any]:
    return first_matching_rule(
        advisor_rules()["ladder_selection_rules"],
        source=source,
        use_case=use_case,
        priority=priority,
        codec=codec,
    )


def _round_bitrate(value: float) -> int:
    if value >= 1000:
        return int(round(value / 100.0) * 100)
    return int(round(value / 50.0) * 50)


def _parse_kbps(value: str) -> int:
    return int(value.lower().removesuffix("k"))
