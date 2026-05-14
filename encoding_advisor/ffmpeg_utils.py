from __future__ import annotations

import shlex


CODEC_ENCODERS = {
    "h264": "libx264",
    "hevc": "libx265",
    "av1": "libsvtav1",
}


def format_command(command: list[str]) -> str:
    return " \\\n  ".join(shlex.quote(part) for part in command)


def parse_bitrate_kbps(value: str) -> int:
    normalized = value.lower().strip()
    if normalized.endswith("k"):
        return int(normalized[:-1])
    if normalized.endswith("m"):
        return int(float(normalized[:-1]) * 1000)
    return int(normalized)
