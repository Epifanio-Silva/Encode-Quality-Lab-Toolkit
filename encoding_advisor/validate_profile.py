from __future__ import annotations

from typing import Any

from .media_properties import is_10_bit_or_higher, is_hdr


SUPPORTED_SOURCE_CODECS = {"h264", "hevc", "h265", "av1", "mpeg2video", "prores", "dnxhd", "dnxhr", "mpeg4"}
H264_INCOMPATIBLE_HDR_PROFILES = {"main10"}


def validate_profile(source: dict[str, Any], recommendation: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    video = source.get("video") or {}
    audio = source.get("audio") or []

    codec = video.get("codec")
    if codec and codec not in SUPPORTED_SOURCE_CODECS:
        warnings.append(f"Source video codec '{codec}' is not in the starter supported-codec list.")
    if codec == "mpeg2video":
        warnings.append("MPEG-2 source detected. If this is a captured TS or broadcast source, treat it as a distribution/reference-risk source rather than a clean mezzanine.")

    container = (source.get("container") or "").lower()
    if "mpegts" in container:
        warnings.append("MPEG-TS source detected. Check for timestamp discontinuities, packet loss, and decode concealment before using strict VMAF pass/fail thresholds.")

    field_order = (video.get("field_order") or "").lower()
    if field_order and field_order not in {"progressive", "unknown"}:
        warnings.append(f"Source appears interlaced or field-coded ({field_order}). Consider deinterlacing for streaming ABR outputs.")

    if video.get("is_vfr_hint"):
        warnings.append("Source may be variable frame rate. Normalize frame rate before ABR packaging if timestamps are unstable.")

    if source and not audio:
        warnings.append("No audio tracks were detected. Confirm this is expected before packaging.")

    if recommendation["video_codec"] == "h264" and is_hdr(video):
        warnings.append("HDR source detected with H.264 recommendation. HEVC Main10 is usually preferred for HDR delivery.")

    if is_10_bit_or_higher(video) and not is_hdr(video) and recommendation.get("profile") != "main10":
        warnings.append("10-bit SDR source detected. The current recommendation may down-convert to 8-bit; use HEVC Main10 if preserving 10-bit SDR is required.")

    if recommendation["video_codec"] == "av1" and "ios" in {device.lower() for device in recommendation.get("devices", [])}:
        warnings.append("AV1 support across iOS devices varies by generation. Keep H.264 or HEVC fallback renditions for broad Apple playback.")

    segment = recommendation.get("segment_duration")
    gop = recommendation.get("gop_duration")
    if segment and gop and float(segment) % float(gop) != 0:
        warnings.append("GOP duration does not divide evenly into segment duration. Segment boundaries may drift from IDR frames.")

    for rendition in recommendation.get("ladder", []):
        bitrate_kbps = _parse_kbps(rendition.get("bitrate"))
        resolution = rendition.get("resolution", "")
        if bitrate_kbps and resolution.endswith("1080") and bitrate_kbps < 3000:
            warnings.append(f"{resolution} at {bitrate_kbps}k may be low for H.264 except for simple content.")
        if bitrate_kbps and resolution.endswith("2160") and recommendation["video_codec"] == "h264":
            warnings.append("4K H.264 ladder detected. HEVC or AV1 is usually more practical for 4K delivery.")

    return warnings
def _parse_kbps(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value.lower().removesuffix("k"))
    except ValueError:
        return None
