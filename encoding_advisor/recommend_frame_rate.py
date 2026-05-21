from __future__ import annotations

from typing import Any

from .media_properties import is_hdr
from .recommendation import decision


APPLE_HDR_FRAME_RATE = 30000 / 1001
APPLE_HDR_FRAME_RATE_TEXT = "30000/1001"


def recommend_frame_rate(source: dict[str, Any], video_codec: str, priority: str, devices: list[str]) -> dict[str, Any]:
    video = source.get("video") or {}
    source_frame_rate = _to_float(video.get("frame_rate"))
    source_frame_rate_raw = video.get("frame_rate_raw")
    hdr = is_hdr(video)
    apple_target = bool({"ios", "apple_tv"} & set(devices))

    if hdr and video_codec == "hevc" and apple_target and priority == "4k_hdr_quality" and source_frame_rate and source_frame_rate > 30:
        reason = "Apple-oriented HEVC HDR workflows cap first-pass test encodes to 29.97 fps when the HDR source is above 30 fps, matching common HLS authoring guidance before testing higher frame-rate ladders."
        return {
            "frame_rate_mode": "apple_hdr_30fps_cap",
            "target_frame_rate": APPLE_HDR_FRAME_RATE,
            "target_frame_rate_raw": APPLE_HDR_FRAME_RATE_TEXT,
            "source_frame_rate": source_frame_rate,
            "source_frame_rate_raw": source_frame_rate_raw,
            "decisions": {
                "frame_rate_mode": decision("frame_rate_mode", "apple_hdr_30fps_cap", reason, "advisor_policy", "frame_rate.apple_hdr_30fps_cap", "high"),
                "target_frame_rate": decision("target_frame_rate", APPLE_HDR_FRAME_RATE_TEXT, reason, "advisor_policy", "frame_rate.apple_hdr_30fps_cap.target", "high"),
            },
            "warnings": [
                "HDR HEVC source is above 30 fps. First-pass Apple-native HLS test is capped to 30000/1001 fps; create a separate high-frame-rate ladder after 30 fps playback is proven."
            ],
        }

    reason = "The source frame rate is preserved for this workflow."
    return {
        "frame_rate_mode": "source",
        "target_frame_rate": source_frame_rate,
        "target_frame_rate_raw": source_frame_rate_raw,
        "source_frame_rate": source_frame_rate,
        "source_frame_rate_raw": source_frame_rate_raw,
        "decisions": {
            "frame_rate_mode": decision("frame_rate_mode", "source", reason, "advisor_policy", "frame_rate.source", "medium"),
            "target_frame_rate": decision("target_frame_rate", source_frame_rate_raw or source_frame_rate, reason, "source_metadata", "frame_rate.source.target", "medium" if source_frame_rate else "low"),
        },
        "warnings": [],
    }


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
