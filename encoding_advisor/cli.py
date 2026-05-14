from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .classify_content import normalize_complexity
from .config_loader import ladder_presets
from .probe_source import ProbeError, probe_source
from .recommend_codec import recommend_codec
from .recommend_gop import recommend_gop
from .recommend_ladder import recommend_ladder, recommend_ladder_decision
from .recommend_packaging import recommend_packaging
from .report_generator import build_report, write_reports
from .validate_profile import validate_profile


USE_CASES = ["live", "live_ott", "vod", "mobile", "ott", "broadcast", "low_latency"]
PROTOCOLS = ["hls", "hls_ts", "hls_fmp4", "dash", "dash_fmp4", "hls_dash", "hls_dash_cmaf", "cmaf", "progressive_mp4"]
DEVICES = ["ios", "android", "web", "roku", "fire_tv", "apple_tv", "smart_tv", "legacy_stb"]
PRIORITIES = ["compatibility", "quality", "lowest_latency", "smallest_file_size", "mobile_efficiency", "bandwidth_savings", "4k_hdr_quality"]
CONTENT_COMPLEXITIES = ["low", "medium", "high", "very_high", "sports", "gaming", "animation", "screen"]
SOURCE_TYPES = ["live", "vod", "file", "mezzanine", "contribution", "camera"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eqlab advise",
        description="Recommend first-pass video streaming encoding profiles for Encode Quality Lab Toolkit workflows.",
    )
    parser.add_argument("--input", help="Source file or stream URL to inspect with ffprobe.")
    parser.add_argument(
        "--use-case",
        default="vod",
        choices=USE_CASES,
        help="Primary delivery use case.",
    )
    parser.add_argument(
        "--protocol",
        default="hls_dash",
        choices=PROTOCOLS,
        help="Target packaging protocol.",
    )
    parser.add_argument(
        "--devices",
        nargs="+",
        default=["ios", "android", "web"],
        help="Target devices, such as ios android web roku fire_tv apple_tv smart_tv legacy_stb.",
    )
    parser.add_argument(
        "--priority",
        default="compatibility",
        choices=PRIORITIES,
        help="Optimization priority.",
    )
    parser.add_argument(
        "--segment-duration",
        type=float,
        default=None,
        help="Target segment duration in seconds. Defaults are chosen by use case.",
    )
    parser.add_argument(
        "--content-complexity",
        default="medium",
        choices=CONTENT_COMPLEXITIES,
        help="Content complexity hint used to adjust the starter ladder.",
    )
    parser.add_argument(
        "--source-type",
        default=None,
        choices=SOURCE_TYPES,
        help="Optional source type hint.",
    )
    parser.add_argument(
        "--list-options",
        action="store_true",
        help="Print available CLI option values and exit without generating a report.",
    )
    parser.add_argument(
        "--explain-rules",
        action="store_true",
        help="Print the matched recommendation decisions and rule IDs after the summary.",
    )
    parser.add_argument(
        "--output-dir",
        default="reports",
        help="Directory for JSON and Markdown reports.",
    )
    parser.add_argument(
        "--report-name",
        help="Base filename for JSON and Markdown reports, without extension. Defaults to encoding_profile_<timestamp>.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full JSON recommendation to stdout.",
    )
    parser.add_argument(
        "--allow-probe-failure",
        action="store_true",
        help="Continue with a generic recommendation if --input cannot be probed.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list_options:
        print_options()
        return 0

    source: dict[str, Any] = {}
    if args.input:
        try:
            source = probe_source(args.input)
        except ProbeError as exc:
            if not args.allow_probe_failure:
                print(f"ERROR: {exc}")
                print("No recommendation was generated because --input was provided but source probing failed.")
                print("Use --allow-probe-failure to intentionally generate a generic recommendation without source analysis.")
                return 2
            source = {"input": args.input, "probe_error": str(exc)}

    complexity = normalize_complexity(args.content_complexity)
    packaging = recommend_packaging(args.use_case, args.protocol, args.segment_duration)
    codec = recommend_codec(
        source=source,
        use_case=args.use_case,
        devices=args.devices,
        priority=args.priority,
    )
    gop = recommend_gop(
        frame_rate=source.get("video", {}).get("frame_rate"),
        segment_duration=packaging["segment_duration"],
        use_case=args.use_case,
    )
    ladder = recommend_ladder(
        source=source,
        codec=codec["video_codec"],
        use_case=args.use_case,
        priority=args.priority,
        complexity=complexity,
    )
    ladder_decision = recommend_ladder_decision(
        ladder=ladder,
        source=source,
        codec=codec["video_codec"],
        use_case=args.use_case,
        priority=args.priority,
        complexity=complexity,
    )

    recommendation: dict[str, Any] = {
        "use_case": args.use_case,
        "source_type": args.source_type,
        "devices": args.devices,
        "priority": args.priority,
        "video_codec": codec["video_codec"],
        "profile": codec["profile"],
        "audio_codec": codec["audio_codec"],
        "packaging": packaging["packaging"],
        "segment_format": packaging["segment_format"],
        "rate_control": codec["rate_control"],
        "segment_duration": packaging["segment_duration"],
        "segment_strategy": packaging["segment_strategy"],
        "gop_duration": gop["gop_duration"],
        "keyframe_interval_frames": gop["keyframe_interval_frames"],
        "closed_gop": gop["decisions"]["closed_gop"]["value"],
        "idr_aligned": gop["decisions"]["idr_aligned"]["value"],
        "ladder": ladder,
        "rationale": {
            "codec": codec["rationale"],
            "packaging": packaging["rationale"],
            "gop": gop["rationale"],
            "ladder": "Starter ladder adjusted for content complexity and capped to the source resolution when known.",
        },
        "decisions": {
            **codec["decisions"],
            **packaging["decisions"],
            **gop["decisions"],
            "ladder": ladder_decision,
        },
    }

    warnings = []
    if source.get("probe_error"):
        warnings.append(source["probe_error"])
    warnings.extend(gop["warnings"])
    warnings.extend(codec["warnings"])
    warnings.extend(validate_profile(source, recommendation))
    recommendation["warnings"] = warnings

    report = build_report(source, recommendation)
    paths = write_reports(report, Path(args.output_dir), args.report_name)

    print_summary(source, recommendation, paths)
    if args.explain_rules:
        print_rule_explanation(recommendation)
    if args.json:
        print(report.to_json())

    return 0


def print_summary(source: dict[str, Any], recommendation: dict[str, Any], paths: dict[str, Path]) -> None:
    video = source.get("video") or {}
    source_line = "not probed"
    if video:
        source_line = f"{video.get('codec', 'unknown')} {video.get('resolution', 'unknown')} @ {video.get('frame_rate', 'unknown')} fps"

    print("Encode Quality Lab Toolkit - Advisor")
    print(f"Source: {source_line}")
    print(f"Codec: {recommendation['video_codec']} / {recommendation['profile']}")
    print(f"Rate control: {recommendation['rate_control']}")
    print(f"Packaging: {recommendation['packaging']}")
    if recommendation["segment_duration"] is None:
        print(f"GOP: {recommendation['gop_duration']}s GOP, {recommendation['keyframe_interval_frames']} frame keyint")
    else:
        print(f"Segment/GOP: {recommendation['segment_duration']}s segments, {recommendation['gop_duration']}s GOP, {recommendation['keyframe_interval_frames']} frame keyint")
    print(f"Ladder renditions: {len(recommendation['ladder'])}")
    print(f"Warnings: {len(recommendation['warnings'])}")
    print(f"JSON report: {paths['json']}")
    print(f"Markdown report: {paths['markdown']}")


def print_options() -> None:
    print("Encode Quality Lab Toolkit Advisor Options")
    print()
    _print_values("--use-case", USE_CASES)
    _print_values("--protocol", PROTOCOLS)
    _print_values("--devices", DEVICES)
    _print_values("--priority", PRIORITIES)
    _print_values("--content-complexity", CONTENT_COMPLEXITIES)
    _print_values("--source-type", SOURCE_TYPES)
    _print_values("ladder presets", sorted(ladder_presets()["presets"].keys()))
    print()
    print("--input accepts a local file path or stream URL readable by ffprobe.")
    print("--segment-duration accepts seconds, such as 1, 2, 4, 6, or 8.")


def print_rule_explanation(recommendation: dict[str, Any]) -> None:
    print()
    print("Matched Decisions")
    for item in recommendation["decisions"].values():
        value = item["value"]
        if isinstance(value, list):
            value = f"{len(value)} renditions"
        print(f"- {item['name']}: {value} [{item['basis']}, {item['rule_id']}, confidence={item['confidence']}]")


def _print_values(label: str, values: list[str]) -> None:
    print(f"{label}:")
    for value in values:
        print(f"  - {value}")
    print()
