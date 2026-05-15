from __future__ import annotations

from typing import Any

from .config_loader import advisor_rules
from .recommendation import decision
from .rule_matcher import first_matching_rule


def recommend_codec(source: dict[str, Any], use_case: str, devices: list[str], priority: str) -> dict[str, Any]:
    warnings: list[str] = []
    rules = advisor_rules()
    codec_rule = first_matching_rule(
        rules["codec_rules"],
        source=source,
        use_case=use_case,
        devices=devices,
        priority=priority,
    )
    video_codec = codec_rule["recommend"]["video_codec"]
    profile = codec_rule["recommend"]["profile"]
    rationale = codec_rule["reason"]
    codec_confidence = codec_rule["confidence"]

    if video_codec == "av1" and use_case in {"live", "live_ott", "low_latency"}:
        warnings.append("AV1 is usually not a first-pass choice for live or low-latency workflows unless encoder latency and device support are known.")

    audio_rule = first_matching_rule(
        rules["audio_rules"],
        source=source,
        use_case=use_case,
        devices=devices,
        priority=priority,
    )
    audio_codec = audio_rule["recommend"]["audio_codec"]
    audio_sample_rate = audio_rule["recommend"].get("audio_sample_rate", 48000)
    audio_channels = audio_rule["recommend"].get("audio_channels", 2)

    rate_rule = first_matching_rule(
        rules["rate_control_rules"],
        source=source,
        use_case=use_case,
        devices=devices,
        priority=priority,
    )
    rate_control = rate_rule["recommend"]["rate_control"]

    return {
        "video_codec": video_codec,
        "profile": profile,
        "audio_codec": audio_codec,
        "audio_sample_rate": audio_sample_rate,
        "audio_channels": audio_channels,
        "rate_control": rate_control,
        "rationale": rationale,
        "warnings": warnings,
        "decisions": {
            "video_codec": decision("video_codec", video_codec, rationale, "advisor_policy", codec_rule["id"], codec_confidence),
            "profile": decision("profile", profile, f"{profile} profile selected for {video_codec}.", "advisor_policy", f"{codec_rule['id']}.profile", codec_confidence),
            "audio_codec": decision("audio_codec", audio_codec, audio_rule["reason"], "advisor_policy", audio_rule["id"], audio_rule["confidence"]),
            "audio_sample_rate": decision("audio_sample_rate", audio_sample_rate, "48 kHz audio is the safest baseline for HLS/DASH packaging and device playback.", "advisor_policy", f"{audio_rule['id']}.sample_rate", audio_rule["confidence"]),
            "audio_channels": decision("audio_channels", audio_channels, "Stereo is the broad compatibility baseline unless a premium surround profile is selected.", "advisor_policy", f"{audio_rule['id']}.channels", audio_rule["confidence"]),
            "rate_control": decision("rate_control", rate_control, rate_rule["reason"], "advisor_policy", rate_rule["id"], rate_rule["confidence"]),
        },
    }
