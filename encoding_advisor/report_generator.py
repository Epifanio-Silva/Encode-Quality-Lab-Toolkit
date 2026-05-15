from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class Report:
    source: dict[str, Any]
    recommendation: dict[str, Any]
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "source": self.source,
            "recommendation": self.recommendation,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=False)

    def to_markdown(self) -> str:
        rec = self.recommendation
        source = self.source
        video = source.get("video") or {}
        audio = source.get("audio") or []

        lines = [
            "# Encoding Profile Recommendation",
            "",
            f"Generated: {self.generated_at}",
            "",
            "## Source Summary",
            "",
        ]
        if video:
            lines.extend(
                [
                    f"- Input: `{source.get('input', 'unknown')}`",
                    f"- Container: `{source.get('container', 'unknown')}`",
                    f"- Video: `{video.get('codec', 'unknown')}` `{video.get('resolution', 'unknown')}` at `{video.get('frame_rate', 'unknown')}` fps",
                    f"- Scan: `{video.get('field_order', 'unknown')}`",
                    f"- Color: primaries `{video.get('color_primaries', 'unknown')}`, transfer `{video.get('color_transfer', 'unknown')}`, pixel format `{video.get('pix_fmt', 'unknown')}`",
                    f"- Audio tracks: `{len(audio)}`",
                ]
            )
        elif source:
            lines.append(f"- Input: `{source.get('input', 'not provided')}`")
            if source.get("probe_error"):
                lines.append(f"- Probe error: `{source['probe_error']}`")
        else:
            lines.append("- Source was not probed.")

        lines.extend(
            [
                "",
                "## Recommendation",
                "",
                f"- Use case: `{rec['use_case']}`",
                f"- Video codec: `{rec['video_codec']}`",
                f"- Profile: `{rec['profile']}`",
                f"- Audio codec: `{rec['audio_codec']}`",
                f"- Audio target: `{rec.get('audio_sample_rate', 'unknown')}` Hz, `{rec.get('audio_channels', 'unknown')}` channels",
                f"- Color target: `{rec.get('output_color_space', 'source')}` via `{rec.get('color_mode', 'preserve')}`",
                f"- Packaging: `{rec['packaging']}`",
                f"- Segment format: `{rec.get('segment_format', 'unknown')}`",
                f"- Rate control: `{rec['rate_control']}`",
                f"- Segment duration: `{rec['segment_duration']}` seconds" if rec["segment_duration"] is not None else "- Segment duration: `none`",
                f"- GOP duration: `{rec['gop_duration']}` seconds",
                f"- Keyframe interval: `{rec['keyframe_interval_frames']}` frames",
                f"- Closed GOP: `{rec['closed_gop']}`",
                f"- IDR aligned: `{rec['idr_aligned']}`",
                "",
                "## Bitrate Ladder",
                "",
                "| Resolution | Bitrate |",
                "| --- | ---: |",
            ]
        )
        for rendition in rec["ladder"]:
            lines.append(f"| {rendition['resolution']} | {rendition['bitrate']} |")

        lines.extend(
            [
                "",
                "## Recommendation Sources",
                "",
                "| Decision | Value | Basis | Confidence |",
                "| --- | --- | --- | --- |",
            ]
        )
        for item in rec.get("decisions", {}).values():
            value = item.get("value")
            if isinstance(value, list):
                value = f"{len(value)} renditions"
            lines.append(
                f"| {item.get('name')} | `{value}` | `{item.get('basis')}` | `{item.get('confidence')}` |"
            )

        lines.extend(["", "## Rationale", ""])
        for key, value in rec["rationale"].items():
            lines.append(f"- {key.title()}: {value}")

        lines.extend(["", "## Warnings", ""])
        if rec["warnings"]:
            for warning in rec["warnings"]:
                lines.append(f"- {warning}")
        else:
            lines.append("- No warnings generated.")

        lines.extend(
            [
                "",
                "## Validation Checks Included In This Version",
                "",
                "- Unsupported source codec warning",
                "- Interlaced source warning",
                "- Variable frame rate hint",
                "- Missing audio warning",
                "- Codec/device compatibility warnings",
                "- GOP and segment duration relationship warning",
                "- Basic bitrate/resolution sanity checks",
            ]
        )
        return "\n".join(lines) + "\n"


def build_report(source: dict[str, Any], recommendation: dict[str, Any]) -> Report:
    return Report(
        source=source,
        recommendation=recommendation,
        generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
    )


def write_reports(report: Report, output_dir: Path, report_name: str | None = None) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = _report_base_name(report_name, timestamp)
    json_path = output_dir / f"{base_name}.json"
    md_path = output_dir / f"{base_name}.md"

    json_path.write_text(report.to_json() + "\n", encoding="utf-8")
    md_path.write_text(report.to_markdown(), encoding="utf-8")
    return {
        "json": json_path,
        "markdown": md_path,
    }


def _report_base_name(report_name: str | None, timestamp: str) -> str:
    if not report_name:
        return f"encoding_profile_{timestamp}"
    clean_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", report_name.strip()).strip("._-")
    if not clean_name:
        return f"encoding_profile_{timestamp}"
    if clean_name.endswith(".json") or clean_name.endswith(".md"):
        clean_name = clean_name.rsplit(".", 1)[0]
    return clean_name
