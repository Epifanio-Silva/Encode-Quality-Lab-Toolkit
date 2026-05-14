from __future__ import annotations

import argparse
import csv
import html
import json
import re
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

import yaml

from encoding_advisor.ffmpeg_utils import parse_bitrate_kbps


METRIC_CONTEXT = {
    "metric_reference": "original_source",
    "metric_mode": "native",
    "scale_method": "source_to_rendition",
    "metric_description": "Metrics compare each encoded rendition against the original source after scaling the source to the rendition resolution.",
}


DEFAULT_VALIDATION_RULES = {
    "policy_name": "builtin_default_streaming_validation",
    "policy_version": 1,
    "source_integrity": {
        "start_time_warn_seconds": 0.5,
        "low_bppf_warn": 0.08,
        "risky_containers": ["mpegts"],
        "risky_codecs": ["mpeg2video"],
        "decode_warning_patterns": [
            {
                "severity": "POOR",
                "rule_id": "source.decode.corrupt_frame",
                "pattern": "corrupt decoded frame",
                "message": "Source decode reported corrupt decoded frames.",
            },
            {
                "severity": "POOR",
                "rule_id": "source.decode.concealment",
                "pattern": "concealing",
                "message": "Source decode required error concealment.",
            },
            {
                "severity": "POOR",
                "rule_id": "source.decode.packet_corrupt",
                "pattern": "packet corrupt",
                "message": "Source decode reported corrupt packets.",
            },
            {
                "severity": "POOR",
                "rule_id": "source.decode.missing_picture",
                "pattern": "missing picture",
                "message": "Source decode reported missing pictures.",
            },
            {
                "severity": "POOR",
                "rule_id": "source.decode.decode_error",
                "pattern": "error while decoding",
                "message": "Source decode reported frame decode errors.",
            },
            {
                "severity": "WARN",
                "rule_id": "source.timestamps.non_monotonic",
                "pattern": "non-monoton",
                "message": "Source decode reported non-monotonic timestamps.",
            },
            {
                "severity": "WARN",
                "rule_id": "source.timestamps.discontinuity",
                "pattern": "dts discontinuity",
                "message": "Source decode reported DTS discontinuities.",
            },
            {
                "severity": "WARN",
                "rule_id": "source.timestamps.invalid",
                "pattern": "invalid timestamps",
                "message": "Source decode reported invalid timestamps.",
            },
        ],
    },
    "precheck": {
        "duration_tolerance_seconds": 0.25,
        "fps_tolerance": 0.01,
        "frame_count_tolerance": 5,
    },
    "bitrate_accuracy": {
        "enabled": True,
        "warn_percent_below_target": 35,
        "warn_percent_above_target": 20,
        "fail_percent_above_target": 50,
        "fail_percent_below_target": None,
        "ignore_below_target_for_crf": True,
    },
    "quality": {
        "fail": {
            "vmaf_mean_below": 80,
            "vmaf_p1_below": 60,
            "vmaf_p5_below": 70,
            "low_frames_below_70_percent_above": 5,
        },
        "warn": {
            "vmaf_mean_below": 90,
            "vmaf_min_below": 80,
            "vmaf_p1_below": 70,
            "vmaf_p5_below": 80,
            "mean_min_delta_above": 15,
        },
        "pass": {
            "excellent_mean_at_least": 95,
            "excellent_min_at_least": 90,
        },
    },
    "source_adjustment": {
        "pass": {"strict_min_vmaf_fail_below": 70},
        "warn": {"min_vmaf_is_diagnostic": True},
        "poor": {
            "min_vmaf_is_diagnostic": True,
            "downgrade_min_only_failures_to_warn": True,
        },
    },
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eqlab validate",
        description="Validate expected encoded renditions against a source using VMAF, SSIM, PSNR, prechecks, charts, and thumbnails.",
    )
    parser.add_argument("--source", required=True, help="Reference source file.")
    parser.add_argument("--profile", required=True, help="Advisor JSON profile used by encoder.py.")
    parser.add_argument("--encoded-dir", required=True, help="Directory containing expected encoded rendition MP4 files.")
    parser.add_argument("--output-dir", default="validation", help="Directory for validation reports and artifacts.")
    parser.add_argument("--target-display", default="1920x1080", help="Target display resolution for future upscaled checks.")
    parser.add_argument("--worst-frames", type=int, default=5, help="Number of worst VMAF frames to include per rendition.")
    parser.add_argument("--duration-tolerance", type=float, default=0.25, help="Allowed source/encoded duration delta in seconds.")
    parser.add_argument("--fps-tolerance", type=float, default=0.01, help="Allowed source/encoded frame-rate delta.")
    parser.add_argument("--frame-count-tolerance", type=int, default=5, help="Allowed delta between estimated source frame count and encoded frame count.")
    parser.add_argument("--rules", default="config/validation_rules.yaml", help="Validation rules YAML file.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source = Path(args.source)
    profile_path = Path(args.profile)
    encoded_dir = Path(args.encoded_dir)
    output_dir = Path(args.output_dir)
    rules_path = Path(args.rules)

    if not source.exists():
        print(f"ERROR: source file does not exist: {source}")
        return 2
    if not profile_path.exists():
        print(f"ERROR: advisor profile does not exist: {profile_path}")
        return 2
    if not encoded_dir.exists():
        print(f"ERROR: encoded directory does not exist: {encoded_dir}")
        return 2

    validation_rules = load_validation_rules(rules_path)
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    recommendation = profile.get("recommendation", {})
    profile_source = profile.get("source", {})
    source_info = ffprobe_video(source)
    if not source_info:
        print(f"ERROR: source has no video stream: {source}")
        return 2

    output_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = output_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    source_integrity = assess_source_integrity(
        source,
        source_info,
        profile_source,
        validation_rules,
        logs_dir / "source_integrity.stderr.log",
    )
    rows = []
    expected = expected_renditions(recommendation, encoded_dir)

    print("Encoding Validation")
    print(f"Source: {source}")
    print(f"Advisor profile: {profile_path}")
    print(f"Encoded directory: {encoded_dir}")
    print(f"Source integrity: {source_integrity['status']} - {source_integrity['summary']}")
    print(f"Expected renditions: {len(expected)}")
    print()

    for rendition in expected:
        print(f"Validating: {rendition['id']}")
        encoded = rendition["path"]
        if not encoded.exists():
            rows.append(missing_row(rendition))
            print("  missing")
            continue

        encoded_info = ffprobe_video(encoded)
        if not encoded_info:
            rows.append(invalid_row(rendition, "Encoded file has no video stream."))
            print("  invalid video")
            continue

        precheck_status, precheck_warnings = build_precheck(
            source_info,
            encoded_info,
            expected_resolution=rendition["resolution"],
            duration_tolerance=args.duration_tolerance,
            fps_tolerance=args.fps_tolerance,
            frame_count_tolerance=args.frame_count_tolerance,
            validation_rules=validation_rules,
        )

        metrics_dir = output_dir / "metrics"
        charts_dir = output_dir / "charts"
        thumbnails_dir = output_dir / "thumbnails"
        metrics_dir.mkdir(parents=True, exist_ok=True)

        artifact_stem = safe_stem(rendition["id"])
        vmaf_json = metrics_dir / f"{artifact_stem}_vmaf_native.json"
        psnr_log = metrics_dir / f"{artifact_stem}_psnr_native.log"
        ssim_log = metrics_dir / f"{artifact_stem}_ssim_native.log"

        run_vmaf_native(encoded, source, encoded_info, vmaf_json, logs_dir / f"{artifact_stem}_vmaf.stderr.log")
        metrics = parse_vmaf_json(vmaf_json)
        psnr_avg = run_pair_metric("psnr", encoded, source, encoded_info, psnr_log, logs_dir / f"{artifact_stem}_psnr.stderr.log")
        ssim_all = run_pair_metric("ssim", encoded, source, encoded_info, ssim_log, logs_dir / f"{artifact_stem}_ssim.stderr.log")

        chart_path = create_vmaf_svg_chart(
            metrics["frames"],
            encoded_info["frame_rate"],
            charts_dir / f"{artifact_stem}_vmaf.svg",
            f"{rendition['id']} VMAF Over Time",
        )
        actual_bitrate_mbps = bitrate_mbps(encoded_info["bit_rate"])
        bitrate_accuracy = evaluate_bitrate_accuracy(
            rendition["bitrate"],
            actual_bitrate_mbps,
            validation_rules,
        )
        worst_frames = create_worst_frame_artifacts(
            encoded,
            metrics["frames"],
            encoded_info["frame_rate"],
            thumbnails_dir,
            artifact_stem,
            args.worst_frames,
            output_dir,
        )
        worst_frame = worst_frames[0] if worst_frames else None

        row = {
            "id": rendition["id"],
            "file": encoded.name,
            **METRIC_CONTEXT,
            "reference_resolution": source_info.get("resolution") or f"{source_info.get('width', '')}x{source_info.get('height', '')}",
            "distorted_resolution": f"{encoded_info['width']}x{encoded_info['height']}",
            "metric_space_resolution": f"{encoded_info['width']}x{encoded_info['height']}",
            "expected_resolution": rendition["resolution"],
            "expected_bitrate": rendition["bitrate"],
            "codec": encoded_info["codec"],
            "resolution": f"{encoded_info['width']}x{encoded_info['height']}",
            "frame_rate": encoded_info["frame_rate"],
            "bitrate_mbps": actual_bitrate_mbps,
            "target_bitrate_kbps": bitrate_accuracy["target_kbps"],
            "actual_bitrate_kbps": bitrate_accuracy["actual_kbps"],
            "bitrate_delta_percent": bitrate_accuracy["delta_percent"],
            "bitrate_status": bitrate_accuracy["status"],
            "bitrate_notes": bitrate_accuracy["notes"],
            "duration": round(encoded_info["duration"], 3),
            "precheck_status": precheck_status,
            "precheck_warnings": precheck_warnings,
            "quality_status": classify_quality(metrics, source_integrity, validation_rules),
            "notes": build_quality_notes(metrics, source_integrity, validation_rules),
            "worst_frame": worst_frame["frame_num"] if worst_frame else "",
            "worst_timestamp": worst_frame["timestamp_label"] if worst_frame else "",
            "chart": chart_path.relative_to(output_dir).as_posix() if chart_path else "",
            "worst_frames": worst_frames,
            "psnr_avg": psnr_avg,
            "ssim_all": ssim_all,
            "quality_per_mbps": quality_per_mbps(metrics["vmaf_mean"], bitrate_mbps(encoded_info["bit_rate"])),
            **metrics,
        }
        rows.append(row)
        print(f"  {row['quality_status']} VMAF mean={format_optional_metric(row['vmaf_mean'])} min={format_optional_metric(row['vmaf_min'])}")

    report = build_report(source, profile_path, source_info, profile_source, recommendation, source_integrity, validation_rules, rows)
    write_json(report, output_dir / "validation_report.json")
    write_csv(rows, output_dir / "validation_summary.csv")
    write_markdown(report, output_dir / "validation_report.md")
    write_html(report, output_dir / "validation_report.html")

    print()
    print(f"JSON report: {output_dir / 'validation_report.json'}")
    print(f"Markdown report: {output_dir / 'validation_report.md'}")
    print(f"HTML report: {output_dir / 'validation_report.html'}")
    return 0 if all(row.get("quality_status") != "FAIL" for row in rows) else 1


def load_validation_rules(path: Path) -> dict[str, Any]:
    if not path.exists():
        return dict(DEFAULT_VALIDATION_RULES)
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    return deep_merge(dict(DEFAULT_VALIDATION_RULES), loaded)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = deep_merge(dict(base[key]), value)
        else:
            base[key] = value
    return base


def run_cmd(cmd: list[str]) -> tuple[str, str]:
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed:\n{' '.join(cmd)}\n\nSTDERR:\n{result.stderr}")
    return result.stdout, result.stderr


def ffprobe_video(path: Path) -> dict[str, Any] | None:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,width,height,avg_frame_rate,r_frame_rate,bit_rate,duration,nb_frames,start_time",
        "-show_entries",
        "format=format_name,duration,bit_rate,start_time",
        "-of",
        "json",
        str(path),
    ]
    stdout, _ = run_cmd(cmd)
    data = json.loads(stdout)
    if not data.get("streams"):
        return None
    stream = data["streams"][0]
    fmt = data.get("format", {})
    frame_rate = stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "unknown"
    duration = stream.get("duration") or fmt.get("duration") or 0
    bit_rate = stream.get("bit_rate") or fmt.get("bit_rate") or 0
    nb_frames_raw = stream.get("nb_frames")
    nb_frames = int(nb_frames_raw) if nb_frames_raw and nb_frames_raw != "N/A" else None
    container_raw = fmt.get("format_name")
    return {
        "codec": stream.get("codec_name", "unknown"),
        "width": int(stream.get("width", 0)),
        "height": int(stream.get("height", 0)),
        "frame_rate": frame_rate,
        "bit_rate": int(bit_rate) if bit_rate else 0,
        "duration": float(duration) if duration else 0.0,
        "nb_frames": nb_frames,
        "start_time": _to_float(stream.get("start_time") or fmt.get("start_time")),
        "container": container_label(container_raw, fmt),
        "container_raw": container_raw,
    }



def container_label(format_name: Any, format_info: dict[str, Any] | None = None) -> str:
    if not format_name:
        return ""
    raw = str(format_name)
    lower = raw.lower()
    format_info = format_info or {}
    tags = format_info.get("tags", {}) if isinstance(format_info.get("tags"), dict) else {}
    major_brand = str(tags.get("major_brand") or format_info.get("major_brand") or "").lower().strip()
    first = lower.split(",", 1)[0].strip()
    if lower == "quicktime / mov":
        return "QuickTime / MOV"
    if first == "mov":
        if major_brand in {"qt", "qt  "} or major_brand.startswith("qt"):
            return "QuickTime / MOV"
        return "MOV / MP4 family"
    if first == "mp4":
        return "MP4"
    if first == "mpegts":
        return "MPEG-TS"
    if first == "matroska":
        return "Matroska / MKV"
    return first or raw

def assess_source_integrity(
    source: Path,
    source_info: dict[str, Any],
    profile_source: dict[str, Any],
    validation_rules: dict[str, Any],
    stderr_log: Path,
) -> dict[str, Any]:
    issues = []
    source_rules = validation_rules.get("source_integrity", {})
    container = str(
        profile_source.get("container_raw")
        or source_info.get("container_raw")
        or profile_source.get("container")
        or source_info.get("container")
        or ""
    ).lower().replace("-", "")
    codec = str(source_info.get("codec") or "").lower()
    width = int(source_info.get("width") or 0)
    height = int(source_info.get("height") or 0)
    fps = parse_frame_rate(source_info.get("frame_rate", "unknown"))
    bit_rate = int(source_info.get("bit_rate") or 0)
    start_time = source_info.get("start_time")
    risky_containers = {str(item).lower().replace("-", "") for item in source_rules.get("risky_containers", [])}
    risky_codecs = {str(item).lower() for item in source_rules.get("risky_codecs", [])}
    start_warn = float(source_rules.get("start_time_warn_seconds", 0.5))
    low_bppf_warn = float(source_rules.get("low_bppf_warn", 0.08))

    if any(risky_container in container for risky_container in risky_containers):
        issues.append(source_issue("WARN", "source.container.mpegts", "MPEG-TS source detected; this often indicates a captured or transport-oriented source rather than a clean mezzanine reference."))
    if codec in risky_codecs:
        issues.append(source_issue("WARN", "source.codec.mpeg2video", "MPEG-2 video source detected; VMAF may penalize encodes that smooth or remove source compression artifacts."))
    if start_time and abs(float(start_time)) > start_warn:
        issues.append(source_issue("WARN", "source.timestamps.start_offset", f"Source starts at {float(start_time):.3f}s instead of near zero; timestamp normalization is recommended before strict comparisons."))
    if bit_rate and width and height and fps:
        bits_per_pixel_frame = bit_rate / (width * height * fps)
        if bits_per_pixel_frame < low_bppf_warn:
            issues.append(source_issue("WARN", "source.bitrate.low_bppf", f"Source bitrate density is low ({bits_per_pixel_frame:.3f} bits/pixel/frame) for use as a pristine reference."))

    decode_findings = scan_source_decode(source, stderr_log, source_rules)
    issues.extend(decode_findings["issues"])

    severe_count = sum(1 for issue in issues if issue["severity"] == "POOR")
    warn_count = sum(1 for issue in issues if issue["severity"] == "WARN")
    if severe_count:
        status = "POOR"
        summary = "Source is not a clean objective-metric reference; interpret VMAF with visual review and percentile context."
    elif warn_count:
        status = "WARN"
        summary = "Source is usable, but objective metrics need context."
    else:
        status = "PASS"
        summary = "Source appears suitable as a quality reference."

    return {
        "status": status,
        "summary": summary,
        "policy": source_integrity_policy(status),
        "decode_scan": {
            "stderr_log": str(stderr_log),
            "returncode": decode_findings["returncode"],
            "matched_warning_count": decode_findings["matched_warning_count"],
        },
        "issues": issues,
    }


def source_issue(severity: str, rule_id: str, message: str, evidence: str | None = None) -> dict[str, Any]:
    issue = {
        "severity": severity,
        "rule_id": rule_id,
        "message": message,
    }
    if evidence:
        issue["evidence"] = evidence
    return issue


def scan_source_decode(source: Path, stderr_log: Path, source_rules: dict[str, Any]) -> dict[str, Any]:
    cmd = ["ffmpeg", "-v", "warning", "-i", str(source), "-map", "0:v:0", "-an", "-f", "null", "-"]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stderr_log.write_text(result.stderr, encoding="utf-8")
    text = result.stderr.lower()
    patterns = source_rules.get("decode_warning_patterns", [])
    issues = []
    matched_count = 0
    for pattern in patterns:
        severity = pattern["severity"]
        rule_id = pattern["rule_id"]
        needle = pattern["pattern"]
        message = pattern["message"]
        count = text.count(needle)
        if count:
            matched_count += count
            issues.append(source_issue(severity, rule_id, f"{message} Count: {count}.", needle))
    if result.returncode != 0:
        issues.append(source_issue("POOR", "source.decode.scan_failed", f"Source decode scan failed with return code {result.returncode}."))
    return {
        "returncode": result.returncode,
        "matched_warning_count": matched_count,
        "issues": issues,
    }


def source_integrity_policy(status: str) -> str:
    if status == "PASS":
        return "Strict metric thresholds are appropriate when the source is a clean reference."
    if status == "WARN":
        return "Use mean, percentile, and worst-frame review. Avoid failing only on a single minimum VMAF frame."
    return "The source is not a clean reference. Validation can flag broad failures, but worst-frame VMAF should be treated as diagnostic."


def _to_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def expected_renditions(recommendation: dict[str, Any], encoded_dir: Path) -> list[dict[str, Any]]:
    items = []
    for rendition in recommendation.get("ladder", []):
        resolution = rendition["resolution"]
        bitrate = rendition["bitrate"]
        rendition_id = f"{resolution}_{bitrate}"
        items.append(
            {
                "id": rendition_id,
                "resolution": resolution,
                "bitrate": bitrate,
                "path": encoded_dir / f"{rendition_id}.mp4",
            }
        )
    return items


def build_precheck(
    source_info: dict[str, Any],
    encoded_info: dict[str, Any],
    *,
    expected_resolution: str,
    duration_tolerance: float,
    fps_tolerance: float,
    frame_count_tolerance: int = 5,
    validation_rules: dict[str, Any] | None = None,
) -> tuple[str, str]:
    warnings = []
    precheck_rules = (validation_rules or {}).get("precheck", {})
    duration_tolerance = duration_tolerance if duration_tolerance is not None else float(precheck_rules.get("duration_tolerance_seconds", 0.25))
    fps_tolerance = fps_tolerance if fps_tolerance is not None else float(precheck_rules.get("fps_tolerance", 0.01))
    frame_count_tolerance = frame_count_tolerance if frame_count_tolerance is not None else int(precheck_rules.get("frame_count_tolerance", 5))
    source_duration = source_info.get("duration", 0.0)
    encoded_duration = encoded_info.get("duration", 0.0)
    if source_duration and encoded_duration:
        delta = abs(source_duration - encoded_duration)
        if delta > duration_tolerance:
            warnings.append(f"Duration mismatch: source={source_duration:.3f}s encoded={encoded_duration:.3f}s delta={delta:.3f}s")

    source_fps = parse_frame_rate(source_info.get("frame_rate", "unknown"))
    encoded_fps = parse_frame_rate(encoded_info.get("frame_rate", "unknown"))
    if source_fps and encoded_fps:
        delta = abs(source_fps - encoded_fps)
        if delta > fps_tolerance:
            warnings.append(f"Frame-rate mismatch: source={source_fps:.3f} encoded={encoded_fps:.3f}")

    actual_resolution = f"{encoded_info.get('width', 0)}x{encoded_info.get('height', 0)}"
    if actual_resolution != expected_resolution:
        warnings.append(f"Resolution mismatch: expected={expected_resolution} actual={actual_resolution}")
    if encoded_info.get("width", 0) <= 0 or encoded_info.get("height", 0) <= 0:
        warnings.append("Invalid encoded resolution detected")

    encoded_frames = encoded_info.get("nb_frames")
    if encoded_frames is not None:
        src_fps = parse_frame_rate(source_info.get("frame_rate", "unknown"))
        src_duration = source_info.get("duration", 0.0)
        if src_fps and src_duration:
            expected_frames = round(src_duration * src_fps)
            delta = abs(encoded_frames - expected_frames)
            if delta > frame_count_tolerance:
                warnings.append(
                    f"Frame count mismatch: source_estimated={expected_frames} encoded={encoded_frames} delta={delta} "
                    f"(possible PTS offset or duplicate frames in encode)"
                )

    return ("PASS" if not warnings else "WARN", "; ".join(warnings) if warnings else "None")


def parse_frame_rate(rate_str: str) -> float:
    if not rate_str or rate_str == "unknown" or rate_str == "0/0":
        return 0.0
    try:
        return float(Fraction(rate_str))
    except Exception:
        return 0.0


def run_vmaf_native(encoded: Path, source: Path, encoded_info: dict[str, Any], output_json: Path, stderr_log: Path) -> None:
    width = encoded_info["width"]
    height = encoded_info["height"]
    filter_complex = (
        f"[0:v]setpts=PTS-STARTPTS,format=yuv420p[dist];"
        f"[1:v]setpts=PTS-STARTPTS,scale={width}:{height}:flags=bicubic,format=yuv420p[ref];"
        f"[dist][ref]libvmaf=log_fmt=json:log_path={output_json}"
    )
    cmd = ["ffmpeg", "-y", "-i", str(encoded), "-i", str(source), "-filter_complex", filter_complex, "-f", "null", "-"]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stderr_log.write_text(result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"VMAF failed for {encoded}: {result.stderr}")


def run_pair_metric(metric: str, encoded: Path, source: Path, encoded_info: dict[str, Any], stats_file: Path, stderr_log: Path) -> float | None:
    width = encoded_info["width"]
    height = encoded_info["height"]
    filter_complex = (
        f"[0:v]setpts=PTS-STARTPTS,format=yuv420p[dist];"
        f"[1:v]setpts=PTS-STARTPTS,scale={width}:{height}:flags=bicubic,format=yuv420p[ref];"
        f"[dist][ref]{metric}=stats_file={stats_file}"
    )
    cmd = ["ffmpeg", "-y", "-i", str(encoded), "-i", str(source), "-filter_complex", filter_complex, "-f", "null", "-"]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stderr_log.write_text(result.stderr, encoding="utf-8")
    if result.returncode != 0:
        return None
    if metric == "psnr":
        return parse_metric_from_stderr(result.stderr, r"average:([0-9.]+)")
    if metric == "ssim":
        return parse_metric_from_stderr(result.stderr, r"All:([0-9.]+)")
    return None


def parse_metric_from_stderr(stderr: str, pattern: str) -> float | None:
    match = re.search(pattern, stderr)
    return float(match.group(1)) if match else None


def parse_vmaf_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    pooled = data.get("pooled_metrics", {})
    vmaf = pooled.get("vmaf") or pooled.get("integer_vmaf")
    if not vmaf:
        raise ValueError(f"No VMAF pooled metrics found in {path}")
    frames = data.get("frames", [])
    scores = []
    for frame in frames:
        score = get_frame_vmaf(frame)
        if score is not None:
            scores.append(float(score))
    percentiles = vmaf_percentiles(scores)
    return {
        "vmaf_min": vmaf.get("min"),
        "vmaf_max": vmaf.get("max"),
        "vmaf_mean": vmaf.get("mean"),
        "vmaf_harmonic_mean": vmaf.get("harmonic_mean"),
        "vmaf_p1": percentiles.get("p1"),
        "vmaf_p5": percentiles.get("p5"),
        "vmaf_low_frames_below_70": sum(1 for score in scores if score < 70),
        "vmaf_low_frames_below_80": sum(1 for score in scores if score < 80),
        "vmaf_frame_count": len(scores),
        "frames": frames,
    }


def vmaf_percentiles(scores: list[float]) -> dict[str, float | None]:
    if not scores:
        return {"p1": None, "p5": None}
    ordered = sorted(scores)
    return {
        "p1": percentile_nearest_rank(ordered, 1),
        "p5": percentile_nearest_rank(ordered, 5),
    }


def percentile_nearest_rank(ordered_scores: list[float], percentile: int) -> float:
    index = round((percentile / 100) * (len(ordered_scores) - 1))
    index = min(len(ordered_scores) - 1, max(0, index))
    return ordered_scores[index]


def get_frame_vmaf(frame: dict[str, Any]) -> float | None:
    metrics = frame.get("metrics", {})
    return metrics.get("vmaf") or metrics.get("integer_vmaf")


def get_worst_frames(frames: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    scored = []
    for frame in frames:
        frame_num = frame.get("frameNum")
        vmaf = get_frame_vmaf(frame)
        if frame_num is not None and vmaf is not None:
            scored.append({"frame_num": int(frame_num), "vmaf": float(vmaf)})
    return sorted(scored, key=lambda item: item["vmaf"])[:limit]


def frame_to_timestamp(frame_num: int, frame_rate: str) -> float:
    fps = parse_frame_rate(frame_rate)
    return frame_num / fps if fps else 0.0


def format_timestamp(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    minutes = int(seconds // 60)
    remaining = seconds - (minutes * 60)
    return f"{minutes:02d}:{remaining:06.3f}"


def safe_stem(value: str) -> str:
    return "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in value)


def extract_thumbnail(video_path: Path, timestamp: float, output_path: Path) -> bool:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-ss", f"{timestamp:.3f}", "-i", str(video_path), "-frames:v", "1", "-q:v", "2", str(output_path)]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return result.returncode == 0


def create_worst_frame_artifacts(
    encoded: Path,
    frames: list[dict[str, Any]],
    frame_rate: str,
    thumbnails_dir: Path,
    artifact_stem: str,
    limit: int,
    output_dir: Path,
) -> list[dict[str, Any]]:
    artifacts = []
    for item in get_worst_frames(frames, limit):
        frame_num = item["frame_num"]
        timestamp = frame_to_timestamp(frame_num, frame_rate)
        thumb_path = thumbnails_dir / f"{artifact_stem}_frame_{frame_num}_vmaf_{item['vmaf']:.3f}.jpg"
        extracted = extract_thumbnail(encoded, timestamp, thumb_path)
        artifacts.append(
            {
                "frame_num": frame_num,
                "timestamp": timestamp,
                "timestamp_label": format_timestamp(timestamp),
                "vmaf": item["vmaf"],
                "thumbnail": thumb_path.relative_to(output_dir).as_posix() if extracted else "",
            }
        )
    return artifacts


def create_vmaf_svg_chart(frames: list[dict[str, Any]], frame_rate: str, output_path: Path, title: str) -> Path | None:
    scored = []
    for frame in frames:
        frame_num = frame.get("frameNum")
        vmaf = get_frame_vmaf(frame)
        if frame_num is not None and vmaf is not None:
            scored.append((frame_to_timestamp(int(frame_num), frame_rate), float(vmaf)))
    if not scored:
        return None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    width, height = 1000, 320
    ml, mr, mt, mb = 60, 24, 40, 44
    pw, ph = width - ml - mr, height - mt - mb
    min_time, max_time = min(t for t, _ in scored), max(t for t, _ in scored)
    time_span = max(max_time - min_time, 0.001)
    points = []
    for timestamp, score in scored:
        x = ml + ((timestamp - min_time) / time_span) * pw
        y = mt + ((100.0 - score) / 100.0) * ph
        points.append(f"{x:.2f},{y:.2f}")
    scores = [score for _, score in scored]
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="100%" height="100%" fill="#ffffff"/>
  <text x="{ml}" y="24" font-family="Arial" font-size="18" font-weight="bold">{html.escape(title)}</text>
  <text x="{ml}" y="{height - 12}" font-family="Arial" font-size="12" fill="#555">Mean={sum(scores)/len(scores):.3f} Min={min(scores):.3f} Max={max(scores):.3f}</text>
  <line x1="{ml}" y1="{mt}" x2="{ml}" y2="{mt + ph}" stroke="#333" stroke-width="1"/>
  <line x1="{ml}" y1="{mt + ph}" x2="{ml + pw}" y2="{mt + ph}" stroke="#333" stroke-width="1"/>
  <line x1="{ml}" y1="{mt + ph * 0.1}" x2="{ml + pw}" y2="{mt + ph * 0.1}" stroke="#ddd" stroke-width="1"/>
  <line x1="{ml}" y1="{mt + ph * 0.2}" x2="{ml + pw}" y2="{mt + ph * 0.2}" stroke="#eee" stroke-width="1"/>
  <line x1="{ml}" y1="{mt + ph * 0.5}" x2="{ml + pw}" y2="{mt + ph * 0.5}" stroke="#eee" stroke-width="1"/>
  <text x="12" y="{mt + 4}" font-family="Arial" font-size="12">100</text>
  <text x="20" y="{mt + ph * 0.1 + 4}" font-family="Arial" font-size="12">90</text>
  <text x="20" y="{mt + ph * 0.2 + 4}" font-family="Arial" font-size="12">80</text>
  <text x="20" y="{mt + ph * 0.5 + 4}" font-family="Arial" font-size="12">50</text>
  <text x="{ml}" y="{mt + ph + 24}" font-family="Arial" font-size="12">{format_timestamp(min_time)}</text>
  <text x="{ml + pw - 52}" y="{mt + ph + 24}" font-family="Arial" font-size="12">{format_timestamp(max_time)}</text>
  <polyline points="{' '.join(points)}" fill="none" stroke="#0d6efd" stroke-width="2"/>
</svg>
"""
    output_path.write_text(svg, encoding="utf-8")
    return output_path


def classify_quality(metrics: dict[str, Any], source_integrity: dict[str, Any], validation_rules: dict[str, Any]) -> str:
    vmaf_mean = metrics.get("vmaf_mean")
    vmaf_min = metrics.get("vmaf_min")
    vmaf_p1 = metrics.get("vmaf_p1")
    vmaf_p5 = metrics.get("vmaf_p5")
    frame_count = metrics.get("vmaf_frame_count") or 0
    low_below_70 = metrics.get("vmaf_low_frames_below_70") or 0
    if vmaf_mean is None or vmaf_min is None:
        return "FAIL"

    quality = validation_rules.get("quality", {})
    fail = quality.get("fail", {})
    warn = quality.get("warn", {})
    adjustment = validation_rules.get("source_adjustment", {})
    pass_adjustment = adjustment.get("pass", {})
    low_below_70_percent = (low_below_70 / frame_count) * 100 if frame_count else 0
    if vmaf_mean < float(fail.get("vmaf_mean_below", 80)):
        return "FAIL"
    if vmaf_p1 is not None and vmaf_p1 < float(fail.get("vmaf_p1_below", 60)):
        return "FAIL"
    if vmaf_p5 is not None and vmaf_p5 < float(fail.get("vmaf_p5_below", 70)):
        return "FAIL"
    if low_below_70_percent > float(fail.get("low_frames_below_70_percent_above", 5)):
        return "FAIL"

    source_status = source_integrity.get("status")
    drop = vmaf_mean - vmaf_min
    if source_status == "PASS" and vmaf_min < float(pass_adjustment.get("strict_min_vmaf_fail_below", 70)):
        return "FAIL"
    if source_status == "POOR":
        return "WARN"
    if source_status == "WARN" and vmaf_min < float(warn.get("vmaf_min_below", 80)):
        return "WARN"
    if (
        vmaf_mean < float(warn.get("vmaf_mean_below", 90))
        or vmaf_min < float(warn.get("vmaf_min_below", 80))
        or drop > float(warn.get("mean_min_delta_above", 15))
    ):
        return "WARN"
    if vmaf_p1 is not None and vmaf_p1 < float(warn.get("vmaf_p1_below", 70)):
        return "WARN"
    if vmaf_p5 is not None and vmaf_p5 < float(warn.get("vmaf_p5_below", 80)):
        return "WARN"
    return "PASS"


def build_quality_notes(metrics: dict[str, Any], source_integrity: dict[str, Any], validation_rules: dict[str, Any]) -> str:
    vmaf_mean = metrics.get("vmaf_mean")
    vmaf_min = metrics.get("vmaf_min")
    vmaf_p1 = metrics.get("vmaf_p1")
    vmaf_p5 = metrics.get("vmaf_p5")
    if vmaf_mean is None or vmaf_min is None:
        return "No VMAF score"
    notes = []
    quality = validation_rules.get("quality", {})
    pass_rules = quality.get("pass", {})
    warn = quality.get("warn", {})
    drop = vmaf_mean - vmaf_min
    if vmaf_mean >= float(pass_rules.get("excellent_mean_at_least", 95)) and vmaf_min >= float(pass_rules.get("excellent_min_at_least", 90)):
        notes.append("Excellent")
    elif vmaf_mean >= float(warn.get("vmaf_mean_below", 90)):
        notes.append("Very good")
    elif vmaf_mean >= float(quality.get("fail", {}).get("vmaf_mean_below", 80)):
        notes.append("Good; inspect visually")
    elif vmaf_mean >= 70:
        notes.append("Noticeable degradation")
    else:
        notes.append("Poor; needs investigation")
    if vmaf_min < float(validation_rules.get("source_adjustment", {}).get("pass", {}).get("strict_min_vmaf_fail_below", 70)):
        notes.append(f"CRITICAL low frame detected: min VMAF={vmaf_min:.3f}")
    elif vmaf_min < float(warn.get("vmaf_min_below", 80)):
        notes.append(f"Low frame warning: min VMAF={vmaf_min:.3f}")
    if drop > float(warn.get("mean_min_delta_above", 15)):
        notes.append(f"Large quality swing: mean-min delta={drop:.3f}")
    if vmaf_p1 is not None and vmaf_p1 < float(warn.get("vmaf_p1_below", 70)):
        notes.append(f"Low 1st percentile: p1={vmaf_p1:.3f}")
    if vmaf_p5 is not None and vmaf_p5 < float(warn.get("vmaf_p5_below", 80)):
        notes.append(f"Low 5th percentile: p5={vmaf_p5:.3f}")
    if source_integrity.get("status") == "POOR":
        notes.append("Source integrity is POOR; worst-frame VMAF is diagnostic, not a clean-reference failure by itself")
    elif source_integrity.get("status") == "WARN":
        notes.append("Source integrity warning; interpret VMAF with visual review")
    return "; ".join(notes)


def bitrate_mbps(bit_rate: int) -> float | str:
    return round(bit_rate / 1_000_000, 3) if bit_rate else ""


def quality_per_mbps(vmaf_mean: float | None, bitrate: float | str) -> float | None:
    if vmaf_mean is None or not bitrate:
        return None
    return vmaf_mean / float(bitrate)


def evaluate_bitrate_accuracy(target_bitrate: str, actual_bitrate_mbps: float | str, validation_rules: dict[str, Any]) -> dict[str, Any]:
    rules = validation_rules.get("bitrate_accuracy", {})
    enabled = bool(rules.get("enabled", True))
    target_kbps = parse_bitrate_kbps(target_bitrate) if target_bitrate else None
    actual_kbps = round(float(actual_bitrate_mbps) * 1000, 3) if actual_bitrate_mbps not in (None, "") else None
    if not enabled:
        return {
            "status": "SKIP",
            "target_kbps": target_kbps,
            "actual_kbps": actual_kbps,
            "delta_percent": None,
            "notes": "Bitrate accuracy check disabled by validation policy.",
        }
    if not target_kbps or actual_kbps is None:
        return {
            "status": "WARN",
            "target_kbps": target_kbps,
            "actual_kbps": actual_kbps,
            "delta_percent": None,
            "notes": "Unable to calculate bitrate accuracy.",
        }

    delta_percent = ((actual_kbps - target_kbps) / target_kbps) * 100
    warn_below = rules.get("warn_percent_below_target")
    warn_above = rules.get("warn_percent_above_target")
    fail_below = rules.get("fail_percent_below_target")
    fail_above = rules.get("fail_percent_above_target")
    ignore_below_for_crf = bool(rules.get("ignore_below_target_for_crf", True))

    status = "PASS"
    notes = "Actual bitrate is within configured tolerance."
    if fail_above is not None and delta_percent > float(fail_above):
        status = "FAIL"
        notes = f"Actual bitrate is {delta_percent:.1f}% above target."
    elif fail_below is not None and delta_percent < -float(fail_below) and not ignore_below_for_crf:
        status = "FAIL"
        notes = f"Actual bitrate is {abs(delta_percent):.1f}% below target."
    elif warn_above is not None and delta_percent > float(warn_above):
        status = "WARN"
        notes = f"Actual bitrate is {delta_percent:.1f}% above target."
    elif warn_below is not None and delta_percent < -float(warn_below):
        if ignore_below_for_crf:
            status = "PASS"
            notes = f"Actual bitrate is {abs(delta_percent):.1f}% below target; allowed for CRF/capped VBR efficiency."
        else:
            status = "WARN"
            notes = f"Actual bitrate is {abs(delta_percent):.1f}% below target."

    return {
        "status": status,
        "target_kbps": target_kbps,
        "actual_kbps": actual_kbps,
        "delta_percent": round(delta_percent, 3),
        "notes": notes,
    }


def missing_row(rendition: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": rendition["id"],
        "file": rendition["path"].name,
        **METRIC_CONTEXT,
        "expected_resolution": rendition["resolution"],
        "expected_bitrate": rendition["bitrate"],
        "quality_status": "FAIL",
        "precheck_status": "FAIL",
        "precheck_warnings": "Expected encoded file is missing.",
        "notes": "Missing encoded rendition.",
        "worst_frames": [],
    }


def invalid_row(rendition: dict[str, Any], reason: str) -> dict[str, Any]:
    row = missing_row(rendition)
    row["precheck_warnings"] = reason
    row["notes"] = reason
    return row


def build_report(
    source: Path,
    profile_path: Path,
    source_info: dict[str, Any],
    profile_source: dict[str, Any],
    recommendation: dict[str, Any],
    source_integrity: dict[str, Any],
    validation_rules: dict[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "source": str(source),
        "profile": str(profile_path),
        "source_info": source_info,
        "source_summary": build_source_summary(source_info, profile_source),
        "metric_context": METRIC_CONTEXT,
        "source_integrity": source_integrity,
        "validation_policy": {
            "name": validation_rules.get("policy_name"),
            "version": validation_rules.get("policy_version"),
            "rules": validation_rules,
        },
        "recommendation_summary": {
            "video_codec": recommendation.get("video_codec"),
            "profile": recommendation.get("profile"),
            "packaging": recommendation.get("packaging"),
            "segment_format": recommendation.get("segment_format"),
            "keyframe_interval_frames": recommendation.get("keyframe_interval_frames"),
        },
        "summary": {
            "total": len(rows),
            "pass": sum(1 for row in rows if row.get("quality_status") == "PASS"),
            "warn": sum(1 for row in rows if row.get("quality_status") == "WARN"),
            "fail": sum(1 for row in rows if row.get("quality_status") == "FAIL"),
        },
        "renditions": rows,
    }


def build_source_summary(source_info: dict[str, Any], profile_source: dict[str, Any] | None = None) -> dict[str, Any]:
    profile_source = profile_source or {}
    video = profile_source.get("video") or {}
    audio_tracks = profile_source.get("audio") or []
    width = video.get("width") or source_info.get("width")
    height = video.get("height") or source_info.get("height")
    frame_rate = video.get("frame_rate_raw") or source_info.get("frame_rate")
    frame_rate_display = video.get("frame_rate")
    container_raw = profile_source.get("container_raw") or source_info.get("container_raw") or profile_source.get("container") or source_info.get("container")
    return {
        "container": container_label(profile_source.get("container") or source_info.get("container"), profile_source),
        "container_raw": container_raw,
        "duration_seconds": profile_source.get("duration") or source_info.get("duration"),
        "bitrate": profile_source.get("bitrate") or source_info.get("bit_rate"),
        "video_codec": video.get("codec") or source_info.get("codec"),
        "video_profile": video.get("profile"),
        "resolution": video.get("resolution") or (f"{width}x{height}" if width and height else ""),
        "frame_rate": frame_rate,
        "frame_rate_display": frame_rate_display,
        "scan_type": video.get("field_order"),
        "pix_fmt": video.get("pix_fmt"),
        "video_bitrate": video.get("bitrate") or source_info.get("bit_rate"),
        "color_space": video.get("color_space"),
        "color_transfer": video.get("color_transfer"),
        "color_primaries": video.get("color_primaries"),
        "bit_depth": video.get("bits_per_raw_sample"),
        "audio": audio_tracks,
    }


def write_json(report: dict[str, Any], output_path: Path) -> None:
    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def write_csv(rows: list[dict[str, Any]], output_path: Path) -> None:
    fieldnames = [
        "id",
        "file",
        "metric_reference",
        "metric_mode",
        "scale_method",
        "reference_resolution",
        "distorted_resolution",
        "metric_space_resolution",
        "codec",
        "resolution",
        "expected_resolution",
        "target_bitrate_kbps",
        "actual_bitrate_kbps",
        "bitrate_delta_percent",
        "bitrate_status",
        "bitrate_notes",
        "bitrate_mbps",
        "duration",
        "precheck_status",
        "precheck_warnings",
        "quality_status",
        "vmaf_min",
        "vmaf_max",
        "vmaf_mean",
        "vmaf_harmonic_mean",
        "vmaf_p1",
        "vmaf_p5",
        "vmaf_low_frames_below_70",
        "vmaf_low_frames_below_80",
        "vmaf_frame_count",
        "psnr_avg",
        "ssim_all",
        "quality_per_mbps",
        "worst_frame",
        "worst_timestamp",
        "chart",
        "notes",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(report: dict[str, Any], output_path: Path) -> None:
    rows = report["renditions"]
    lines = [
        "# Validation Report",
        "",
        f"Source: `{report['source']}`",
        f"Profile: `{report['profile']}`",
        f"Validation Policy: `{report['validation_policy']['name']}` v`{report['validation_policy']['version']}`",
        f"Metric Context: `{report['metric_context']['metric_reference']}` / `{report['metric_context']['metric_mode']}` / `{report['metric_context']['scale_method']}`",
        "",
        report["metric_context"]["metric_description"],
        "",
        f"Source Integrity: `{report['source_integrity']['status']}` - {report['source_integrity']['summary']}",
        "",
        report["source_integrity"]["policy"],
        "",
        f"Total: `{report['summary']['total']}` PASS: `{report['summary']['pass']}` WARN: `{report['summary']['warn']}` FAIL: `{report['summary']['fail']}`",
        "",
        "## Source Summary",
        "",
    ]
    lines.extend(source_summary_markdown(report["source_summary"]))
    lines.extend(["", "## Source Integrity Findings", ""])
    if report["source_integrity"]["issues"]:
        lines.extend(["| Severity | Rule | Finding |", "| --- | --- | --- |"])
        for issue in report["source_integrity"]["issues"]:
            lines.append(f"| {issue['severity']} | `{issue['rule_id']}` | {issue['message']} |")
    else:
        lines.append("No source integrity issues were detected.")
    lines.extend(
        [
            "",
            "## Renditions",
            "",
            "| Rendition | Metric Mode | Scale Method | Quality | Precheck | Bitrate | Metric Space | Mean VMAF | P1 VMAF | P5 VMAF | Min VMAF | Frames <70 | Frames <80 | PSNR | SSIM | Worst Time | Notes |",
            "| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for row in rows:
        lines.append(
            f"| {row.get('id', '')} | {row.get('metric_mode', '')} | {row.get('scale_method', '')} | "
            f"{row.get('quality_status', '')} | {row.get('precheck_status', '')} | {format_bitrate_summary(row)} | "
            f"{row.get('metric_space_resolution') or row.get('resolution', '')} | {format_optional_metric(row.get('vmaf_mean'))} | "
            f"{format_optional_metric(row.get('vmaf_p1'))} | {format_optional_metric(row.get('vmaf_p5'))} | "
            f"{format_optional_metric(row.get('vmaf_min'))} | {row.get('vmaf_low_frames_below_70', '')} | "
            f"{row.get('vmaf_low_frames_below_80', '')} | {format_optional_metric(row.get('psnr_avg'))} | "
            f"{format_optional_metric(row.get('ssim_all'), 6)} | {row.get('worst_timestamp', '')} | {row.get('notes', '')} |"
        )
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_html(report: dict[str, Any], output_path: Path) -> None:
    esc = html.escape
    rows = report["renditions"]
    parts = [
        "<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'><title>Validation Report</title>",
        "<style>body{font-family:Arial;margin:24px;background:#f7f7f7;color:#222}.card{background:#fff;border:1px solid #ddd;border-radius:8px;padding:16px;margin-bottom:18px}table{width:100%;border-collapse:collapse;background:#fff}th{background:#333;color:#fff;padding:8px;text-align:left}td{border:1px solid #ddd;padding:8px;vertical-align:top}.PASS{background:#eaf8ee}.WARN{background:#fff8e1}.FAIL,.POOR{background:#fdebed}.badge{border-radius:999px;padding:3px 8px;font-weight:bold}.badge.PASS{background:#198754;color:#fff}.badge.WARN{background:#ffc107}.badge.FAIL,.badge.POOR{background:#dc3545;color:#fff}.chart{width:100%;max-width:1000px;border:1px solid #ddd}.thumbs{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px}.thumb img{width:100%;border:1px solid #ddd;border-radius:6px}</style></head><body>",
        "<h1>Validation Report</h1>",
        "<div class='card'><h2>Summary</h2>",
        f"<p>Source: <code>{esc(report['source'])}</code></p>",
        f"<p>Profile: <code>{esc(report['profile'])}</code></p>",
        f"<p>Validation Policy: <code>{esc(str(report['validation_policy']['name']))}</code> v{esc(str(report['validation_policy']['version']))}</p>",
        f"<p>Metric Context: <code>{esc(report['metric_context']['metric_reference'])}</code> / <code>{esc(report['metric_context']['metric_mode'])}</code> / <code>{esc(report['metric_context']['scale_method'])}</code></p>",
        f"<p>{esc(report['metric_context']['metric_description'])}</p>",
        f"<p>Source Integrity: <span class='badge {esc(report['source_integrity']['status'])}'>{esc(report['source_integrity']['status'])}</span> {esc(report['source_integrity']['summary'])}</p>",
        f"<p>{esc(report['source_integrity']['policy'])}</p>",
        f"<p>Total: {report['summary']['total']} PASS: {report['summary']['pass']} WARN: {report['summary']['warn']} FAIL: {report['summary']['fail']}</p>",
        "</div>",
    ]
    parts.extend(source_summary_html(report["source_summary"]))
    parts.append("<div class='card'><h2>Source Integrity Findings</h2>")
    if report["source_integrity"]["issues"]:
        parts.append("<table><tr><th>Severity</th><th>Rule</th><th>Finding</th></tr>")
        for issue in report["source_integrity"]["issues"]:
            severity = issue["severity"]
            parts.append(
                f"<tr class='{esc(severity)}'><td><span class='badge {esc(severity)}'>{esc(severity)}</span></td>"
                f"<td><code>{esc(issue['rule_id'])}</code></td><td>{esc(issue['message'])}</td></tr>"
            )
        parts.append("</table>")
    else:
        parts.append("<p>No source integrity issues were detected.</p>")
    parts.extend([
        "</div>",
        "<div class='card'><h2>Renditions</h2><table><tr><th>Rendition</th><th>Metric Mode</th><th>Scale Method</th><th>Quality</th><th>Precheck</th><th>Bitrate</th><th>Metric Space</th><th>Mean VMAF</th><th>P1 VMAF</th><th>P5 VMAF</th><th>Min VMAF</th><th>Frames &lt;70</th><th>Frames &lt;80</th><th>PSNR</th><th>SSIM</th><th>Worst Time</th><th>Notes</th></tr>",
    ]
    )
    for row in rows:
        q = row.get("quality_status", "")
        p = row.get("precheck_status", "")
        parts.append(
            f"<tr class='{q}'><td><code>{esc(row.get('id', ''))}</code></td><td>{esc(row.get('metric_mode', ''))}</td><td>{esc(row.get('scale_method', ''))}</td>"
            f"<td><span class='badge {q}'>{q}</span></td><td><span class='badge {p}'>{p}</span></td><td>{esc(format_bitrate_summary(row))}</td>"
            f"<td>{esc(str(row.get('metric_space_resolution') or row.get('resolution', '')))}</td><td>{format_optional_metric(row.get('vmaf_mean'))}</td>"
            f"<td>{format_optional_metric(row.get('vmaf_p1'))}</td><td>{format_optional_metric(row.get('vmaf_p5'))}</td>"
            f"<td>{format_optional_metric(row.get('vmaf_min'))}</td><td>{row.get('vmaf_low_frames_below_70', '')}</td>"
            f"<td>{row.get('vmaf_low_frames_below_80', '')}</td><td>{format_optional_metric(row.get('psnr_avg'))}</td>"
            f"<td>{format_optional_metric(row.get('ssim_all'), 6)}</td><td>{row.get('worst_timestamp', '')}</td><td>{esc(row.get('notes', ''))}</td></tr>"
        )
    parts.append("</table></div>")
    parts.append("<div class='card'><h2>Charts and Worst Frames</h2>")
    for row in rows:
        if not row.get("chart") and not row.get("worst_frames"):
            continue
        parts.append(f"<h3>{esc(row.get('id', ''))}</h3>")
        if row.get("chart"):
            parts.append(f"<img class='chart' src='{esc(row['chart'])}' alt='VMAF chart'>")
        if row.get("worst_frames"):
            parts.append("<div class='thumbs'>")
            for item in row["worst_frames"]:
                parts.append("<div class='thumb'>")
                if item.get("thumbnail"):
                    parts.append(f"<img src='{esc(item['thumbnail'])}' alt='Worst frame'>")
                parts.append(f"<p>Frame {item['frame_num']}<br>Time {item['timestamp_label']}<br>VMAF {item['vmaf']:.3f}</p></div>")
            parts.append("</div>")
    parts.append("</div></body></html>")
    output_path.write_text("\n".join(parts), encoding="utf-8")


def source_summary_markdown(summary: dict[str, Any]) -> list[str]:
    lines = [
        "| Property | Value |",
        "| --- | --- |",
        f"| Container | {md(summary.get('container'))} |",
        f"| Container Raw | {md(summary.get('container_raw'))} |",
        f"| Duration | {format_seconds(summary.get('duration_seconds'))} |",
        f"| Overall Bitrate | {format_bits_per_second(summary.get('bitrate'))} |",
        f"| Video Codec | {md(summary.get('video_codec'))} |",
        f"| Video Profile | {md(summary.get('video_profile'))} |",
        f"| Resolution | {md(summary.get('resolution'))} |",
        f"| Frame Rate | {md(format_frame_rate(summary))} |",
        f"| Scan Type | {md(summary.get('scan_type'))} |",
        f"| Pixel Format | {md(summary.get('pix_fmt'))} |",
        f"| Video Bitrate | {format_bits_per_second(summary.get('video_bitrate'))} |",
        f"| Color | {md(format_color(summary))} |",
        f"| Bit Depth | {md(summary.get('bit_depth'))} |",
        f"| Audio | {md(format_audio_tracks(summary.get('audio') or []))} |",
    ]
    return lines


def source_summary_html(summary: dict[str, Any]) -> list[str]:
    esc = html.escape
    rows = [
        ("Container", summary.get("container")),
        ("Container Raw", summary.get("container_raw")),
        ("Duration", format_seconds(summary.get("duration_seconds"))),
        ("Overall Bitrate", format_bits_per_second(summary.get("bitrate"))),
        ("Video Codec", summary.get("video_codec")),
        ("Video Profile", summary.get("video_profile")),
        ("Resolution", summary.get("resolution")),
        ("Frame Rate", format_frame_rate(summary)),
        ("Scan Type", summary.get("scan_type")),
        ("Pixel Format", summary.get("pix_fmt")),
        ("Video Bitrate", format_bits_per_second(summary.get("video_bitrate"))),
        ("Color", format_color(summary)),
        ("Bit Depth", summary.get("bit_depth")),
        ("Audio", format_audio_tracks(summary.get("audio") or [])),
    ]
    parts = ["<div class='card'><h2>Source Summary</h2><table><tr><th>Property</th><th>Value</th></tr>"]
    for label, value in rows:
        parts.append(f"<tr><td>{esc(label)}</td><td>{esc(str(value or ''))}</td></tr>")
    parts.append("</table></div>")
    return parts


def format_frame_rate(summary: dict[str, Any]) -> str:
    raw = summary.get("frame_rate")
    display = summary.get("frame_rate_display")
    if raw and display and str(raw) != str(display):
        return f"{raw} ({display})"
    return str(raw or "")


def format_color(summary: dict[str, Any]) -> str:
    values = [summary.get("color_space"), summary.get("color_transfer"), summary.get("color_primaries")]
    return " / ".join(str(value) for value in values if value)


def format_audio_tracks(audio_tracks: list[dict[str, Any]]) -> str:
    if not audio_tracks:
        return "None detected"
    parts = []
    for index, audio in enumerate(audio_tracks, start=1):
        codec = audio.get("codec") or "unknown"
        channels = audio.get("channel_layout") or audio.get("channels") or ""
        sample_rate = f"{audio.get('sample_rate')} Hz" if audio.get("sample_rate") else ""
        bitrate = format_bits_per_second(audio.get("bitrate"))
        detail = ", ".join(str(value) for value in [codec, channels, sample_rate, bitrate] if value)
        parts.append(f"{index}: {detail}")
    return "; ".join(parts)


def format_seconds(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        return f"{float(value):.3f}s"
    except (TypeError, ValueError):
        return str(value)


def format_bits_per_second(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    if numeric >= 1_000_000:
        return f"{numeric / 1_000_000:.3f} Mbps"
    if numeric >= 1_000:
        return f"{numeric / 1_000:.3f} kbps"
    return f"{numeric:.0f} bps"


def md(value: Any) -> str:
    return str(value or "").replace("|", "\\|")


def format_optional_metric(value: Any, decimals: int = 3) -> str:
    if value in (None, ""):
        return ""
    try:
        return f"{float(value):.{decimals}f}"
    except (TypeError, ValueError):
        return str(value)


def format_optional_percent(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        return f"{float(value):+.1f}%"
    except (TypeError, ValueError):
        return str(value)


def format_bitrate_summary(row: dict[str, Any]) -> str:
    status = row.get("bitrate_status", "")
    target = row.get("target_bitrate_kbps", "")
    actual = row.get("actual_bitrate_kbps", "")
    delta = format_optional_percent(row.get("bitrate_delta_percent"))
    if not target and not actual:
        return status
    return f"{status} target={format_optional_metric(target, 0)}k actual={format_optional_metric(actual, 0)}k delta={delta}"


if __name__ == "__main__":
    raise SystemExit(main())
