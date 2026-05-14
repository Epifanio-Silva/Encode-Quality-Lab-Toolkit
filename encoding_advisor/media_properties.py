from __future__ import annotations

from typing import Any


HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}


def is_hdr(video: dict[str, Any]) -> bool:
    transfer = (video.get("color_transfer") or "").lower()
    primaries = (video.get("color_primaries") or "").lower()
    color_space = (video.get("color_space") or "").lower()

    return transfer in HDR_TRANSFERS or "2020" in primaries or "2020" in color_space


def is_10_bit_or_higher(video: dict[str, Any]) -> bool:
    pix_fmt = (video.get("pix_fmt") or "").lower()
    bit_depth = video.get("bits_per_raw_sample") or 0
    return "10" in pix_fmt or "12" in pix_fmt or bit_depth >= 10
