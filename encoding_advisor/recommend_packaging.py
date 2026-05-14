from __future__ import annotations

from .config_loader import advisor_rules
from .recommendation import decision


def recommend_packaging(use_case: str, protocol: str, requested_segment_duration: float | None) -> dict[str, object]:
    packaging_rule = advisor_rules()["packaging_rules"].get(use_case) or advisor_rules()["packaging_rules"]["ott"]
    strategy = packaging_rule["segment_strategy"]
    rationale = packaging_rule["reason"]

    packaging, segment_format, packaging_reason = _normalize_protocol(protocol)
    if segment_format == "none":
        segment_duration = None
        strategy = "single-file progressive MP4 output without ABR segment boundaries"
        rationale = "Progressive MP4 output is optimized for direct file playback or download, not segmented ABR switching."
    else:
        segment_duration = requested_segment_duration or packaging_rule["default_segment_duration"]

    return {
        "packaging": packaging,
        "segment_format": segment_format,
        "segment_duration": _clean_number(segment_duration),
        "segment_strategy": strategy,
        "rationale": rationale,
        "decisions": {
            "packaging": decision(
                "packaging",
                packaging,
                packaging_reason,
                "user_input",
                "packaging.protocol.user_selected",
                "high",
            ),
            "segment_format": decision(
                "segment_format",
                segment_format,
                "Segment format is derived from the selected packaging protocol.",
                "advisor_policy",
                "packaging.segment_format.from_protocol",
                "high",
            ),
            "segment_duration": decision(
                "segment_duration",
                _clean_number(segment_duration),
                "Progressive MP4 has no ABR segment duration." if segment_duration is None else "Segment duration came from user input or use-case defaults.",
                "advisor_policy" if segment_duration is None else "user_input" if requested_segment_duration else "advisor_policy",
                "packaging.segment_duration.none" if segment_duration is None else "packaging.segment_duration.user_selected" if requested_segment_duration else f"packaging.segment_duration.default.{use_case}",
                "high" if segment_duration is None or requested_segment_duration else "medium",
            ),
            "segment_strategy": decision(
                "segment_strategy",
                strategy,
                rationale,
                "advisor_policy",
                f"packaging.strategy.{use_case}",
                "medium",
            ),
        },
    }


def _normalize_protocol(protocol: str) -> tuple[str, str, str]:
    aliases = {
        "hls": ("hls_ts", "mpeg_ts", "HLS defaults to MPEG-TS segments unless hls_fmp4 is requested."),
        "hls_ts": ("hls_ts", "mpeg_ts", "HLS with MPEG-TS segments was requested."),
        "hls_fmp4": ("hls_fmp4", "fmp4", "HLS with fragmented MP4 segments was requested."),
        "dash": ("dash_fmp4", "fmp4", "DASH defaults to fragmented MP4 segments."),
        "dash_fmp4": ("dash_fmp4", "fmp4", "DASH with fragmented MP4 segments was requested."),
        "hls_dash": ("hls_dash_cmaf", "cmaf", "Parallel HLS+DASH defaults to CMAF-style fragmented MP4 segments."),
        "hls_dash_cmaf": ("hls_dash_cmaf", "cmaf", "HLS+DASH CMAF packaging was requested."),
        "cmaf": ("hls_dash_cmaf", "cmaf", "Requested CMAF packaging is represented as HLS+DASH CMAF."),
        "progressive_mp4": ("progressive_mp4", "none", "Single-file progressive MP4 was requested; this is not segmented fMP4/CMAF."),
    }
    return aliases[protocol]


def _clean_number(value: float | None) -> int | float | None:
    if value is None:
        return None
    return int(value) if float(value).is_integer() else value
