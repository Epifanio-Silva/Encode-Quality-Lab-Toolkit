from __future__ import annotations

import json
import shutil
import subprocess
from fractions import Fraction
from typing import Any


class ProbeError(RuntimeError):
    """Raised when ffprobe cannot inspect a source."""


def probe_source(input_uri: str) -> dict[str, Any]:
    if not shutil.which("ffprobe"):
        raise ProbeError("ffprobe was not found on PATH. Install FFmpeg or run without --input to test recommendations.")

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        input_uri,
    ]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=45)
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"ffprobe timed out while probing {input_uri}") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() or "unknown ffprobe error"
        hint = _input_hint(input_uri)
        raise ProbeError(f"ffprobe failed for {input_uri}: {stderr}{hint}") from exc

    raw = json.loads(result.stdout)
    return parse_ffprobe(raw, input_uri)


def _input_hint(input_uri: str) -> str:
    hints = []
    if any(char in input_uri for char in "“”‘’"):
        hints.append("smart quotes were detected; use straight shell quotes instead")
    if "\\" in input_uri:
        hints.append("backslashes were detected; on macOS/Linux, use forward slashes or an absolute POSIX path")
    if not hints:
        return ""
    return " Hint: " + "; ".join(hints) + "."


def parse_ffprobe(raw: dict[str, Any], input_uri: str) -> dict[str, Any]:
    streams = raw.get("streams", [])
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    format_info = raw.get("format", {})

    container_raw = format_info.get("format_name")
    container = _container_label(container_raw, format_info)
    source: dict[str, Any] = {
        "input": input_uri,
        "container": container,
        "container_raw": container_raw,
        "duration": _to_float(format_info.get("duration")),
        "bitrate": _to_int(format_info.get("bit_rate")),
        "video": {},
        "audio": [],
        "raw_stream_count": len(streams),
    }

    if video_streams:
        video = video_streams[0]
        frame_rate = _parse_rate(video.get("avg_frame_rate") or video.get("r_frame_rate"))
        source["video"] = {
            "codec": video.get("codec_name"),
            "codec_long_name": video.get("codec_long_name"),
            "profile": video.get("profile"),
            "width": video.get("width"),
            "height": video.get("height"),
            "resolution": _resolution(video.get("width"), video.get("height")),
            "frame_rate": frame_rate,
            "frame_rate_raw": video.get("avg_frame_rate") or video.get("r_frame_rate"),
            "pix_fmt": video.get("pix_fmt"),
            "bitrate": _to_int(video.get("bit_rate")),
            "field_order": video.get("field_order"),
            "color_space": video.get("color_space"),
            "color_transfer": video.get("color_transfer"),
            "color_primaries": video.get("color_primaries"),
            "bits_per_raw_sample": _to_int(video.get("bits_per_raw_sample")),
            "is_vfr_hint": _is_vfr_hint(video),
        }

    for audio in audio_streams:
        source["audio"].append(
            {
                "codec": audio.get("codec_name"),
                "profile": audio.get("profile"),
                "channels": audio.get("channels"),
                "channel_layout": audio.get("channel_layout"),
                "sample_rate": _to_int(audio.get("sample_rate")),
                "bitrate": _to_int(audio.get("bit_rate")),
                "language": audio.get("tags", {}).get("language"),
            }
        )

    return source



def _container_label(format_name: str | None, format_info: dict[str, Any] | None = None) -> str | None:
    if not format_name:
        return None
    format_info = format_info or {}
    raw = str(format_name)
    major_brand = str(format_info.get("tags", {}).get("major_brand") or format_info.get("major_brand") or "").lower()
    first = raw.split(",", 1)[0].strip().lower()
    if first == "mov":
        if major_brand in {"qt", "qt  "} or "qt" in raw.lower():
            return "QuickTime / MOV"
        return "MOV / MP4 family"
    if first == "mpegts":
        return "MPEG-TS"
    if first == "matroska":
        return "Matroska / MKV"
    if first == "mp4":
        return "MP4"
    return first or raw

def _parse_rate(value: str | None) -> float | None:
    if not value or value == "0/0":
        return None
    try:
        return round(float(Fraction(value)), 3)
    except (ValueError, ZeroDivisionError):
        return None


def _is_vfr_hint(video: dict[str, Any]) -> bool:
    avg = video.get("avg_frame_rate")
    real = video.get("r_frame_rate")
    return bool(avg and real and avg != "0/0" and real != "0/0" and avg != real)


def _resolution(width: int | None, height: int | None) -> str | None:
    if width and height:
        return f"{width}x{height}"
    return None


def _to_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
