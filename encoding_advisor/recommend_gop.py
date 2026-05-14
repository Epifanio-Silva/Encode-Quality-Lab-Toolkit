from __future__ import annotations

from math import isclose

from .config_loader import advisor_rules
from .recommendation import decision


def recommend_gop(frame_rate: float | None, segment_duration: float | None, use_case: str) -> dict[str, object]:
    warnings: list[str] = []
    gop_rules = advisor_rules()["gop_rules"]
    rule_id = "gop.default"
    active_rule = gop_rules["default"]
    candidates = [float(value) for value in active_rule["candidates"]]
    if segment_duration is None:
        gop_duration = 2.0
        rationale = "Progressive MP4 does not require segment-boundary GOP alignment; use a moderate GOP for compression efficiency and seekability."
        keyframe_interval = round(frame_rate * gop_duration) if frame_rate else None
        if keyframe_interval is None:
            warnings.append("Frame rate is unknown, so keyframe interval in frames could not be calculated.")
        return {
            "gop_duration": _clean_number(gop_duration),
            "keyframe_interval_frames": keyframe_interval,
            "warnings": warnings,
            "rationale": rationale,
            "decisions": {
                "gop_duration": decision("gop_duration", _clean_number(gop_duration), rationale, "advisor_policy", "gop.progressive_mp4.seekable_default", "medium"),
                "keyframe_interval_frames": decision("keyframe_interval_frames", keyframe_interval, "Calculated as frame_rate multiplied by GOP duration.", "calculation", "gop.keyint.frame_rate_times_gop", "high" if keyframe_interval is not None else "low"),
                "closed_gop": decision("closed_gop", True, "Closed GOP is still a conservative encoding default.", "advisor_policy", "gop.progressive_mp4.closed_gop", "medium"),
                "idr_aligned": decision("idr_aligned", False, "There are no ABR segment boundaries to align for progressive MP4.", "advisor_policy", "gop.progressive_mp4.no_segment_idr_alignment", "high"),
            },
        }
    if use_case == "low_latency":
        rule_id = "gop.low_latency"
        candidates = [float(value) for value in gop_rules["low_latency"]["candidates"]]
    if use_case == "vod" and segment_duration >= gop_rules["vod_long_segments"]["min_segment_duration"]:
        rule_id = "gop.vod_long_segments"
        candidates = [float(value) for value in gop_rules["vod_long_segments"]["candidates"]]

    gop_duration = _choose_divisible_gop(segment_duration, candidates)
    if gop_duration is None:
        gop_duration = 2.0 if segment_duration >= 2 else segment_duration
        warnings.append(
            f"Segment duration {segment_duration:g}s is not evenly divisible by common GOP durations. Use a segment duration that is an integer multiple of GOP duration."
        )

    keyframe_interval = None
    if frame_rate:
        keyframe_interval = round(frame_rate * gop_duration)

    if keyframe_interval is None:
        warnings.append("Frame rate is unknown, so keyframe interval in frames could not be calculated.")

    rationale = active_rule["reason"]

    return {
        "gop_duration": _clean_number(gop_duration),
        "keyframe_interval_frames": keyframe_interval,
        "warnings": warnings,
        "rationale": rationale,
        "decisions": {
            "gop_duration": decision(
                "gop_duration",
                _clean_number(gop_duration),
                rationale,
                "advisor_policy",
                rule_id,
                "high" if not warnings else "medium",
            ),
            "keyframe_interval_frames": decision(
                "keyframe_interval_frames",
                keyframe_interval,
                "Calculated as frame_rate multiplied by GOP duration.",
                "calculation",
                "gop.keyint.frame_rate_times_gop",
                "high" if keyframe_interval is not None else "low",
            ),
            "closed_gop": decision(
                "closed_gop",
                True,
                "Closed GOPs are preferred for ABR switching and independent segments.",
                "advisor_policy",
                f"{rule_id}.closed_gop",
                "high",
            ),
            "idr_aligned": decision(
                "idr_aligned",
                True,
                "Segment boundaries should start on IDR frames across all renditions.",
                "advisor_policy",
                f"{rule_id}.idr_aligned",
                "high",
            ),
        },
    }


def _choose_divisible_gop(segment_duration: float, candidates: list[float]) -> float | None:
    for candidate in candidates:
        quotient = segment_duration / candidate
        if isclose(quotient, round(quotient), rel_tol=0, abs_tol=0.001):
            return candidate
    return None


def _clean_number(value: float) -> int | float:
    return int(value) if value.is_integer() else value
