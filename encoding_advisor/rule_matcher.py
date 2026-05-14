from __future__ import annotations

from typing import Any

from .media_properties import is_hdr


def first_matching_rule(
    rules: list[dict[str, Any]],
    *,
    source: dict[str, Any] | None = None,
    use_case: str | None = None,
    devices: list[str] | None = None,
    priority: str | None = None,
    codec: str | None = None,
) -> dict[str, Any]:
    default_rule = None
    for rule in rules:
        if rule.get("default"):
            default_rule = rule
            continue
        if rule_matches(rule, source=source, use_case=use_case, devices=devices, priority=priority, codec=codec):
            return rule
    if default_rule:
        return default_rule
    raise ValueError("No matching advisor rule found and no default rule is configured.")


def rule_matches(
    rule: dict[str, Any],
    *,
    source: dict[str, Any] | None = None,
    use_case: str | None = None,
    devices: list[str] | None = None,
    priority: str | None = None,
    codec: str | None = None,
) -> bool:
    device_set = {device.lower() for device in devices or []}

    if "use_case" in rule and rule["use_case"] != use_case:
        return False
    if "use_cases" in rule and use_case not in rule["use_cases"]:
        return False
    if "priority" in rule and rule["priority"] != priority:
        return False
    if "priorities" in rule and priority not in rule["priorities"]:
        return False
    if "codec" in rule and rule["codec"] != codec:
        return False
    if "any_device" in rule and not (device_set & {item.lower() for item in rule["any_device"]}):
        return False
    if "none_device" in rule and (device_set & {item.lower() for item in rule["none_device"]}):
        return False
    if "source" in rule and not _source_matches(rule["source"], source or {}):
        return False
    return True


def _source_matches(source_rule: dict[str, Any], source: dict[str, Any]) -> bool:
    video = source.get("video") or {}
    width = video.get("width") or 0
    height = video.get("height") or 0

    if source_rule.get("hdr") is True and not is_hdr(video):
        return False
    if "min_width" in source_rule and width < int(source_rule["min_width"]):
        return False
    if "min_height" in source_rule and height < int(source_rule["min_height"]):
        return False
    if "max_width" in source_rule and width > int(source_rule["max_width"]):
        return False
    if "max_height" in source_rule and height > int(source_rule["max_height"]):
        return False
    return True
