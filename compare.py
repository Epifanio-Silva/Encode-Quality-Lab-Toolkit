from __future__ import annotations

import argparse
import csv
import html
import json
from pathlib import Path
from typing import Any


STATUS_RANK = {
    "PASS": 2,
    "WARN": 1,
    "FAIL": 0,
    "SKIP": -1,
    "": -1,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eqlab compare",
        description="Compare completed validation reports without rerunning VMAF, SSIM, or PSNR.",
    )
    parser.add_argument("--baseline", required=True, help="Baseline validation_report.json.")
    parser.add_argument("--candidate", action="append", required=True, help="Candidate validation_report.json. Repeat for multiple runs.")
    parser.add_argument("--baseline-name", help="Display name for the baseline run.")
    parser.add_argument("--candidate-name", action="append", help="Display name for each candidate, in the same order as --candidate.")
    parser.add_argument("--output-dir", default="comparisons", help="Directory for comparison reports.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    baseline_path = Path(args.baseline)
    candidate_paths = [Path(path) for path in args.candidate]
    output_dir = Path(args.output_dir)

    if not baseline_path.exists():
        print(f"ERROR: baseline report does not exist: {baseline_path}")
        return 2
    for path in candidate_paths:
        if not path.exists():
            print(f"ERROR: candidate report does not exist: {path}")
            return 2

    baseline = load_run(baseline_path, args.baseline_name)
    candidate_names = args.candidate_name or []
    if candidate_names and len(candidate_names) != len(candidate_paths):
        print("ERROR: --candidate-name must be provided once for each --candidate.")
        return 2
    candidates = [
        load_run(path, candidate_names[index] if index < len(candidate_names) else None)
        for index, path in enumerate(candidate_paths)
    ]

    report = build_comparison_report(baseline, candidates)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(report, output_dir / "comparison_report.json")
    write_csv(report, output_dir / "comparison_summary.csv")
    write_run_matrix_csv(report, output_dir / "comparison_runs.csv")
    write_markdown(report, output_dir / "comparison_report.md")
    write_html(report, output_dir / "comparison_report.html")

    print("Encoding Comparison")
    print(f"Baseline: {baseline['name']}")
    for candidate in candidates:
        print(f"Candidate: {candidate['name']}")
    print()
    print(f"JSON report: {output_dir / 'comparison_report.json'}")
    print(f"CSV report: {output_dir / 'comparison_summary.csv'}")
    print(f"Run matrix CSV: {output_dir / 'comparison_runs.csv'}")
    print(f"Markdown report: {output_dir / 'comparison_report.md'}")
    print(f"HTML report: {output_dir / 'comparison_report.html'}")
    return 0


def load_run(path: Path, name: str | None = None) -> dict[str, Any]:
    report = json.loads(path.read_text(encoding="utf-8"))
    run_name = name or infer_run_name(path)
    return {
        "name": run_name,
        "path": str(path),
        "source": report.get("source"),
        "source_info": report.get("source_info", {}),
        "source_summary": report.get("source_summary") or build_source_summary(report.get("source_info", {})),
        "profile": report.get("profile"),
        "source_integrity": report.get("source_integrity", {}),
        "validation_policy": report.get("validation_policy", {}),
        "summary": report.get("summary", {}),
        "renditions": {
            row.get("id"): slim_rendition(row)
            for row in report.get("renditions", [])
            if row.get("id")
        },
    }


def infer_run_name(path: Path) -> str:
    parent = path.parent.name
    return parent if parent else path.stem


def slim_rendition(row: dict[str, Any]) -> dict[str, Any]:
    keys = [
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
        "expected_bitrate",
        "frame_rate",
        "duration",
        "quality_status",
        "precheck_status",
        "bitrate_status",
        "bitrate_notes",
        "target_bitrate_kbps",
        "actual_bitrate_kbps",
        "bitrate_mbps",
        "bitrate_delta_percent",
        "vmaf_max",
        "vmaf_mean",
        "vmaf_min",
        "vmaf_p1",
        "vmaf_p5",
        "vmaf_low_frames_below_70",
        "vmaf_low_frames_below_80",
        "psnr_avg",
        "ssim_all",
        "quality_per_mbps",
        "worst_timestamp",
        "notes",
    ]
    slim = {key: row.get(key) for key in keys}
    slim["metric_reference"] = slim.get("metric_reference") or "original_source"
    slim["metric_mode"] = slim.get("metric_mode") or "native"
    slim["scale_method"] = slim.get("scale_method") or "source_to_rendition"
    slim["metric_space_resolution"] = slim.get("metric_space_resolution") or slim.get("resolution")
    return slim


def build_comparison_report(baseline: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    rendition_ids = sorted(
        set(baseline["renditions"].keys()).union(
            *(set(candidate["renditions"].keys()) for candidate in candidates)
        )
    )
    rows = []
    for rendition_id in rendition_ids:
        baseline_row = baseline["renditions"].get(rendition_id)
        for candidate in candidates:
            candidate_row = candidate["renditions"].get(rendition_id)
            rows.append(compare_rendition(baseline, baseline_row, candidate, candidate_row, rendition_id))
    run_matrix = build_run_matrix(baseline, candidates, rendition_ids)

    return {
        "baseline": run_summary(baseline),
        "candidates": [run_summary(candidate) for candidate in candidates],
        "source_warnings": compare_sources(baseline, candidates),
        "summary": build_summary(rows),
        "best_by_rendition": build_best_by_rendition(baseline, candidates, rendition_ids),
        "run_matrix": run_matrix,
        "comparisons": rows,
    }


def build_run_matrix(baseline: dict[str, Any], candidates: list[dict[str, Any]], rendition_ids: list[str]) -> list[dict[str, Any]]:
    rows = []
    for run, role in [(baseline, "baseline"), *[(candidate, "candidate") for candidate in candidates]]:
        for rendition_id in rendition_ids:
            rendition = run["renditions"].get(rendition_id)
            if not rendition:
                continue
            baseline_row = baseline["renditions"].get(rendition_id)
            rows.append(run_matrix_row(run, role, rendition, baseline_row))
    return rows


def run_matrix_row(
    run: dict[str, Any],
    role: str,
    rendition: dict[str, Any],
    baseline_row: dict[str, Any] | None,
) -> dict[str, Any]:
    bitrate = number(rendition.get("actual_bitrate_kbps"))
    base_bitrate = number(baseline_row.get("actual_bitrate_kbps")) if baseline_row else None
    return {
        "run_name": run["name"],
        "role": role,
        "rendition_id": rendition.get("id"),
        "file": rendition.get("file"),
        "metric_reference": rendition.get("metric_reference"),
        "metric_mode": rendition.get("metric_mode"),
        "scale_method": rendition.get("scale_method"),
        "metric_space_resolution": rendition.get("metric_space_resolution"),
        "codec": rendition.get("codec"),
        "resolution": rendition.get("resolution"),
        "target_bitrate_kbps": rendition.get("target_bitrate_kbps"),
        "actual_bitrate_kbps": bitrate,
        "bitrate_savings_percent": percent_savings(base_bitrate, bitrate),
        "quality_status": rendition.get("quality_status"),
        "bitrate_status": rendition.get("bitrate_status"),
        "vmaf_mean": rendition.get("vmaf_mean"),
        "vmaf_min": rendition.get("vmaf_min"),
        "vmaf_max": rendition.get("vmaf_max"),
        "vmaf_p1": rendition.get("vmaf_p1"),
        "vmaf_p5": rendition.get("vmaf_p5"),
        "vmaf_mean_delta": delta(rendition.get("vmaf_mean"), baseline_row.get("vmaf_mean")) if baseline_row else None,
        "vmaf_p1_delta": delta(rendition.get("vmaf_p1"), baseline_row.get("vmaf_p1")) if baseline_row else None,
        "vmaf_p5_delta": delta(rendition.get("vmaf_p5"), baseline_row.get("vmaf_p5")) if baseline_row else None,
        "psnr_avg": rendition.get("psnr_avg"),
        "psnr_delta": delta(rendition.get("psnr_avg"), baseline_row.get("psnr_avg")) if baseline_row else None,
        "ssim_all": rendition.get("ssim_all"),
        "ssim_delta": delta(rendition.get("ssim_all"), baseline_row.get("ssim_all")) if baseline_row else None,
        "quality_per_mbps": rendition.get("quality_per_mbps"),
        "quality_per_mbps_delta": delta(rendition.get("quality_per_mbps"), baseline_row.get("quality_per_mbps")) if baseline_row else None,
        "worst_timestamp": rendition.get("worst_timestamp"),
        "notes": rendition.get("notes"),
    }


def compare_rendition(
    baseline: dict[str, Any],
    baseline_row: dict[str, Any] | None,
    candidate: dict[str, Any],
    candidate_row: dict[str, Any] | None,
    rendition_id: str,
) -> dict[str, Any]:
    if not baseline_row or not candidate_row:
        return {
            "rendition_id": rendition_id,
            "baseline_run": baseline["name"],
            "candidate_run": candidate["name"],
            "status": "MISSING",
            "notes": "Missing rendition in baseline or candidate report.",
        }

    base_bitrate = number(baseline_row.get("actual_bitrate_kbps"))
    cand_bitrate = number(candidate_row.get("actual_bitrate_kbps"))
    bitrate_savings = percent_savings(base_bitrate, cand_bitrate)
    vmaf_delta = delta(candidate_row.get("vmaf_mean"), baseline_row.get("vmaf_mean"))
    p1_delta = delta(candidate_row.get("vmaf_p1"), baseline_row.get("vmaf_p1"))
    p5_delta = delta(candidate_row.get("vmaf_p5"), baseline_row.get("vmaf_p5"))
    psnr_delta = delta(candidate_row.get("psnr_avg"), baseline_row.get("psnr_avg"))
    ssim_delta = delta(candidate_row.get("ssim_all"), baseline_row.get("ssim_all"))

    return {
        "rendition_id": rendition_id,
        "baseline_run": baseline["name"],
        "candidate_run": candidate["name"],
        "status": "OK",
        "metric_reference": candidate_row.get("metric_reference") or baseline_row.get("metric_reference"),
        "metric_mode": candidate_row.get("metric_mode") or baseline_row.get("metric_mode"),
        "scale_method": candidate_row.get("scale_method") or baseline_row.get("scale_method"),
        "metric_space_resolution": candidate_row.get("metric_space_resolution") or baseline_row.get("metric_space_resolution"),
        "resolution": candidate_row.get("resolution") or baseline_row.get("resolution"),
        "target_bitrate_kbps": candidate_row.get("target_bitrate_kbps") or baseline_row.get("target_bitrate_kbps"),
        "baseline_quality_status": baseline_row.get("quality_status"),
        "candidate_quality_status": candidate_row.get("quality_status"),
        "baseline_bitrate_kbps": base_bitrate,
        "candidate_bitrate_kbps": cand_bitrate,
        "bitrate_savings_percent": bitrate_savings,
        "baseline_vmaf_mean": baseline_row.get("vmaf_mean"),
        "candidate_vmaf_mean": candidate_row.get("vmaf_mean"),
        "vmaf_mean_delta": vmaf_delta,
        "baseline_vmaf_p1": baseline_row.get("vmaf_p1"),
        "candidate_vmaf_p1": candidate_row.get("vmaf_p1"),
        "vmaf_p1_delta": p1_delta,
        "baseline_vmaf_p5": baseline_row.get("vmaf_p5"),
        "candidate_vmaf_p5": candidate_row.get("vmaf_p5"),
        "vmaf_p5_delta": p5_delta,
        "baseline_psnr": baseline_row.get("psnr_avg"),
        "candidate_psnr": candidate_row.get("psnr_avg"),
        "psnr_delta": psnr_delta,
        "baseline_ssim": baseline_row.get("ssim_all"),
        "candidate_ssim": candidate_row.get("ssim_all"),
        "ssim_delta": ssim_delta,
        "baseline_quality_per_mbps": baseline_row.get("quality_per_mbps"),
        "candidate_quality_per_mbps": candidate_row.get("quality_per_mbps"),
        "quality_per_mbps_delta": delta(candidate_row.get("quality_per_mbps"), baseline_row.get("quality_per_mbps")),
        "baseline_notes": baseline_row.get("notes"),
        "candidate_notes": candidate_row.get("notes"),
    }


def build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok_rows = [row for row in rows if row.get("status") == "OK"]
    return {
        "total_comparisons": len(rows),
        "matched_comparisons": len(ok_rows),
        "missing_comparisons": len(rows) - len(ok_rows),
        "average_bitrate_savings_percent": average(row.get("bitrate_savings_percent") for row in ok_rows),
        "average_vmaf_delta": average(row.get("vmaf_mean_delta") for row in ok_rows),
        "average_p1_delta": average(row.get("vmaf_p1_delta") for row in ok_rows),
        "average_p5_delta": average(row.get("vmaf_p5_delta") for row in ok_rows),
    }


def build_best_by_rendition(baseline: dict[str, Any], candidates: list[dict[str, Any]], rendition_ids: list[str]) -> list[dict[str, Any]]:
    runs = [baseline, *candidates]
    winners = []
    for rendition_id in rendition_ids:
        available = [
            (run, run["renditions"].get(rendition_id))
            for run in runs
            if run["renditions"].get(rendition_id)
        ]
        if not available:
            continue
        quality_winner = max(available, key=lambda item: quality_sort_key(item[1]))
        efficiency_candidates = [
            item for item in available
            if item[1].get("quality_status") != "FAIL" and item[1].get("quality_per_mbps") is not None
        ]
        efficiency_winner = max(efficiency_candidates, key=lambda item: number(item[1].get("quality_per_mbps")) or 0) if efficiency_candidates else None
        winners.append(
            {
                "rendition_id": rendition_id,
                "best_quality_run": quality_winner[0]["name"],
                "best_quality_vmaf_mean": quality_winner[1].get("vmaf_mean"),
                "best_efficiency_run": efficiency_winner[0]["name"] if efficiency_winner else "",
                "best_efficiency_quality_per_mbps": efficiency_winner[1].get("quality_per_mbps") if efficiency_winner else None,
            }
        )
    return winners


def quality_sort_key(row: dict[str, Any]) -> tuple[float, float, float, float]:
    return (
        STATUS_RANK.get(row.get("quality_status", ""), -1),
        number(row.get("vmaf_mean")) or -1,
        number(row.get("vmaf_p1")) or -1,
        -(number(row.get("actual_bitrate_kbps")) or 0),
    )


def run_summary(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": run["name"],
        "path": run["path"],
        "source": run.get("source"),
        "source_summary": run.get("source_summary", {}),
        "profile": run.get("profile"),
        "source_integrity": run.get("source_integrity", {}).get("status"),
        "summary": run.get("summary", {}),
    }


def build_source_summary(source_info: dict[str, Any]) -> dict[str, Any]:
    width = source_info.get("width")
    height = source_info.get("height")
    container_raw = source_info.get("container_raw") or source_info.get("container")
    return {
        "container": container_label(source_info.get("container")),
        "container_raw": container_raw,
        "duration_seconds": source_info.get("duration"),
        "bitrate": source_info.get("bit_rate"),
        "video_codec": source_info.get("codec"),
        "resolution": f"{width}x{height}" if width and height else "",
        "frame_rate": source_info.get("frame_rate"),
        "video_bitrate": source_info.get("bit_rate"),
        "audio": [],
    }



def container_label(format_name: Any) -> str:
    if not format_name:
        return ""
    raw = str(format_name)
    lower = raw.lower()
    first = lower.split(",", 1)[0].strip()
    if lower == "quicktime / mov":
        return "QuickTime / MOV"
    if first == "mov":
        return "MOV / MP4 family"
    if first == "mp4":
        return "MP4"
    if first == "mpegts":
        return "MPEG-TS"
    if first == "matroska":
        return "Matroska / MKV"
    return first or raw

def compare_sources(baseline: dict[str, Any], candidates: list[dict[str, Any]]) -> list[str]:
    warnings = []
    base = baseline.get("source_summary", {})
    for candidate in candidates:
        candidate_summary = candidate.get("source_summary", {})
        for key, label in [
            ("video_codec", "video codec"),
            ("resolution", "resolution"),
            ("frame_rate", "frame rate"),
        ]:
            if base.get(key) and candidate_summary.get(key) and str(base.get(key)) != str(candidate_summary.get(key)):
                warnings.append(
                    f"{candidate['name']} source {label} differs from baseline: {candidate_summary.get(key)} vs {base.get(key)}."
                )
        base_duration = number(base.get("duration_seconds"))
        candidate_duration = number(candidate_summary.get("duration_seconds"))
        if base_duration and candidate_duration and abs(base_duration - candidate_duration) > 0.25:
            warnings.append(
                f"{candidate['name']} source duration differs from baseline: {candidate_duration:.3f}s vs {base_duration:.3f}s."
            )
        if baseline.get("source") and candidate.get("source") and baseline.get("source") != candidate.get("source"):
            warnings.append(f"{candidate['name']} source path differs from baseline: {candidate.get('source')}.")
    return warnings


def number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def delta(candidate: Any, baseline: Any) -> float | None:
    cand = number(candidate)
    base = number(baseline)
    if cand is None or base is None:
        return None
    return round(cand - base, 6)


def percent_savings(baseline: float | None, candidate: float | None) -> float | None:
    if baseline in (None, 0) or candidate is None:
        return None
    return round(((baseline - candidate) / baseline) * 100, 3)


def average(values: Any) -> float | None:
    numeric = [number(value) for value in values]
    numeric = [value for value in numeric if value is not None]
    if not numeric:
        return None
    return round(sum(numeric) / len(numeric), 6)


def write_json(report: dict[str, Any], output_path: Path) -> None:
    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def write_csv(report: dict[str, Any], output_path: Path) -> None:
    fieldnames = [
        "rendition_id",
        "baseline_run",
        "candidate_run",
        "status",
        "metric_reference",
        "metric_mode",
        "scale_method",
        "metric_space_resolution",
        "resolution",
        "target_bitrate_kbps",
        "baseline_quality_status",
        "candidate_quality_status",
        "baseline_bitrate_kbps",
        "candidate_bitrate_kbps",
        "bitrate_savings_percent",
        "baseline_vmaf_mean",
        "candidate_vmaf_mean",
        "vmaf_mean_delta",
        "baseline_vmaf_p1",
        "candidate_vmaf_p1",
        "vmaf_p1_delta",
        "baseline_vmaf_p5",
        "candidate_vmaf_p5",
        "vmaf_p5_delta",
        "psnr_delta",
        "ssim_delta",
        "baseline_quality_per_mbps",
        "candidate_quality_per_mbps",
        "quality_per_mbps_delta",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(report["comparisons"])


def write_run_matrix_csv(report: dict[str, Any], output_path: Path) -> None:
    fieldnames = [
        "run_name",
        "role",
        "rendition_id",
        "file",
        "metric_reference",
        "metric_mode",
        "scale_method",
        "metric_space_resolution",
        "codec",
        "resolution",
        "target_bitrate_kbps",
        "actual_bitrate_kbps",
        "bitrate_savings_percent",
        "quality_status",
        "bitrate_status",
        "vmaf_mean",
        "vmaf_min",
        "vmaf_max",
        "vmaf_p1",
        "vmaf_p5",
        "vmaf_mean_delta",
        "vmaf_p1_delta",
        "vmaf_p5_delta",
        "psnr_avg",
        "psnr_delta",
        "ssim_all",
        "ssim_delta",
        "quality_per_mbps",
        "quality_per_mbps_delta",
        "worst_timestamp",
        "notes",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(report["run_matrix"])


def write_markdown(report: dict[str, Any], output_path: Path) -> None:
    lines = [
        "# Encoding Run Comparison",
        "",
        f"Baseline: `{report['baseline']['name']}`",
        "",
        "## Summary",
        "",
        f"- Matched comparisons: `{report['summary']['matched_comparisons']}`",
        f"- Missing comparisons: `{report['summary']['missing_comparisons']}`",
        f"- Average bitrate savings: `{format_percent(report['summary']['average_bitrate_savings_percent'])}`",
        f"- Average VMAF delta: `{format_metric(report['summary']['average_vmaf_delta'])}`",
        "",
        "## Source Summary",
        "",
        *source_summary_markdown(report["baseline"].get("source_summary", {})),
        "",
        "## Source Consistency",
        "",
    ]
    if report.get("source_warnings"):
        lines.extend(f"- {warning}" for warning in report["source_warnings"])
    else:
        lines.append("No obvious source mismatches detected across validation reports.")
    lines.extend([
        "",
        "## Best By Rendition",
        "",
        "| Rendition | Best Quality Run | Best Mean VMAF | Best Efficiency Run | Best Quality/Mbps |",
        "| --- | --- | ---: | --- | ---: |",
    ])
    for row in report["best_by_rendition"]:
        lines.append(
            f"| {row['rendition_id']} | {row['best_quality_run']} | {format_metric(row['best_quality_vmaf_mean'])} | "
            f"{row['best_efficiency_run']} | {format_metric(row['best_efficiency_quality_per_mbps'])} |"
        )
    lines.extend(
        [
            "",
            "## Run Metrics Matrix",
            "",
            "| Run | Role | Rendition | Metric Reference | Metric Mode | Scale Method | Metric Space | Codec | Bitrate kbps | Quality | Mean | Min | P1 | P5 | Quality/Mbps | Savings | VMAF Δ | Worst Time | Notes |",
            "| --- | --- | --- | --- | --- | --- | ---: | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for row in sorted(report["run_matrix"], key=lambda item: (item.get("rendition_id", ""), item.get("role", ""), item.get("run_name", ""))):
        lines.append(
            f"| {row.get('run_name', '')} | {row.get('role', '')} | {row.get('rendition_id', '')} | "
            f"{row.get('metric_reference', '')} | {row.get('metric_mode', '')} | {row.get('scale_method', '')} | "
            f"{row.get('metric_space_resolution', '')} | {row.get('codec', '')} | {format_metric(row.get('actual_bitrate_kbps'), 0)} | "
            f"{row.get('quality_status', '')} | {format_metric(row.get('vmaf_mean'))} | "
            f"{format_metric(row.get('vmaf_min'))} | {format_metric(row.get('vmaf_p1'))} | "
            f"{format_metric(row.get('vmaf_p5'))} | {format_metric(row.get('quality_per_mbps'))} | "
            f"{format_percent(row.get('bitrate_savings_percent'))} | {format_metric(row.get('vmaf_mean_delta'))} | "
            f"{row.get('worst_timestamp', '')} | {row.get('notes', '')} |"
        )
    lines.extend(
        [
            "",
            "## Candidate Deltas",
            "",
            "These deltas compare each candidate's source-referenced validation score against the baseline run's source-referenced validation score. They are not direct encode-vs-encode VMAF measurements.",
            "",
            "| Rendition | Candidate | Quality | Bitrate Savings | VMAF Δ | P1 Δ | P5 Δ | SSIM Δ | Notes |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in report["comparisons"]:
        lines.append(
            f"| {row.get('rendition_id', '')} | {row.get('candidate_run', '')} | "
            f"{row.get('baseline_quality_status', '')}->{row.get('candidate_quality_status', '')} | "
            f"{format_percent(row.get('bitrate_savings_percent'))} | {format_metric(row.get('vmaf_mean_delta'))} | "
            f"{format_metric(row.get('vmaf_p1_delta'))} | {format_metric(row.get('vmaf_p5_delta'))} | "
            f"{format_metric(row.get('ssim_delta'), 6)} | {comparison_note(row)} |"
        )
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_html(report: dict[str, Any], output_path: Path) -> None:
    esc = html.escape
    total_runs = 1 + len(report["candidates"])
    total_renditions = len(report["best_by_rendition"])
    avg_savings = format_percent(report["summary"]["average_bitrate_savings_percent"])
    avg_vmaf_delta = format_metric(report["summary"]["average_vmaf_delta"])
    parts = [
        "<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'><title>Encoding Comparison</title>",
        "<style>body{font-family:Arial;margin:24px;background:#f7f7f7;color:#222}.card{background:#fff;border:1px solid #ddd;border-radius:8px;padding:16px;margin-bottom:18px;box-shadow:0 1px 3px rgba(0,0,0,.08)}.summary-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px}.summary-box{border-radius:8px;padding:12px;background:#f0f0f0;text-align:center;font-weight:bold}table{width:100%;border-collapse:collapse;background:#fff;font-size:14px}th{background:#333;color:#fff;padding:8px;text-align:left;position:sticky;top:0}td{border:1px solid #ddd;padding:8px;vertical-align:top}.pos{color:#146c43;font-weight:bold}.neg{color:#b02a37;font-weight:bold}.badge{display:inline-block;padding:3px 8px;border-radius:999px;font-size:12px;font-weight:bold}.badge.pass{background:#198754;color:#fff}.badge.warn{background:#ffc107;color:#222}.badge.fail{background:#dc3545;color:#fff}.mono,code{font-family:Consolas,monospace}.muted{color:#666}.section-table{margin-top:10px}</style></head><body>",
        "<h1>Encoding Run Comparison</h1>",
        "<div class='card'><h2>Summary</h2>",
        "<div class='summary-grid'>",
        f"<div class='summary-box'>Runs<br>{total_runs}</div>",
        f"<div class='summary-box'>Renditions<br>{total_renditions}</div>",
        f"<div class='summary-box'>Matched<br>{report['summary']['matched_comparisons']}</div>",
        f"<div class='summary-box'>Missing<br>{report['summary']['missing_comparisons']}</div>",
        f"<div class='summary-box'>Avg Savings<br>{avg_savings}</div>",
        f"<div class='summary-box'>Avg VMAF Delta<br>{avg_vmaf_delta}</div>",
        "</div>",
        "</div>",
        "<div class='card'><h2>Runs</h2><table><tr><th>Role</th><th>Name</th><th>Source Integrity</th><th>PASS</th><th>WARN</th><th>FAIL</th><th>Report</th></tr>",
        run_summary_row(report["baseline"], "baseline"),
        *[run_summary_row(candidate, "candidate") for candidate in report["candidates"]],
        "</table></div>",
        *source_summary_html(report["baseline"].get("source_summary", {})),
        source_consistency_html(report.get("source_warnings", [])),
        "<div class='card'><h2>Best By Rendition</h2><table><tr><th>Rendition</th><th>Best Quality Run</th><th>Best Mean VMAF</th><th>Best Efficiency Run</th><th>Best Quality/Mbps</th></tr>",
    ]
    for row in report["best_by_rendition"]:
        parts.append(
            f"<tr><td><code>{esc(row['rendition_id'])}</code></td><td>{esc(row['best_quality_run'])}</td>"
            f"<td>{format_metric(row['best_quality_vmaf_mean'])}</td><td>{esc(row['best_efficiency_run'])}</td>"
            f"<td>{format_metric(row['best_efficiency_quality_per_mbps'])}</td></tr>"
        )
    parts.append("</table></div>")
    parts.append("<div class='card'><h2>Run Metrics Matrix</h2><p class='muted'>VMAF, PSNR, and SSIM values are source-referenced metrics. Candidate rows are not measured against the baseline encode; they are compared against the same original source, then their scores are compared to the baseline scores.</p><table><tr><th>Run</th><th>Role</th><th>Rendition</th><th>Metric Reference</th><th>Metric Mode</th><th>Scale Method</th><th>Metric Space</th><th>Codec</th><th>Bitrate kbps</th><th>Quality</th><th>Mean</th><th>Min</th><th>P1</th><th>P5</th><th>Quality/Mbps</th><th>Savings</th><th>VMAF Δ</th><th>Worst Time</th><th>Notes</th></tr>")
    for row in sorted(report["run_matrix"], key=lambda item: (item.get("rendition_id", ""), item.get("role", ""), item.get("run_name", ""))):
        q_class = status_class(row.get("quality_status", ""))
        parts.append(
            f"<tr><td>{esc(row.get('run_name', ''))}</td><td>{esc(row.get('role', ''))}</td>"
            f"<td><code>{esc(row.get('rendition_id', ''))}</code></td><td>{esc(row.get('metric_reference', ''))}</td>"
            f"<td>{esc(row.get('metric_mode', ''))}</td><td>{esc(row.get('scale_method', ''))}</td>"
            f"<td>{esc(row.get('metric_space_resolution', ''))}</td><td>{esc(row.get('codec', ''))}</td>"
            f"<td>{format_metric(row.get('actual_bitrate_kbps'), 0)}</td><td><span class='badge {q_class}'>{esc(row.get('quality_status', ''))}</span></td>"
            f"<td>{format_metric(row.get('vmaf_mean'))}</td><td>{format_metric(row.get('vmaf_min'))}</td>"
            f"<td>{format_metric(row.get('vmaf_p1'))}</td><td>{format_metric(row.get('vmaf_p5'))}</td>"
            f"<td>{format_metric(row.get('quality_per_mbps'))}</td><td>{format_percent(row.get('bitrate_savings_percent'))}</td>"
            f"<td>{format_delta(row.get('vmaf_mean_delta'))}</td><td>{esc(row.get('worst_timestamp', ''))}</td>"
            f"<td>{esc(row.get('notes', ''))}</td></tr>"
        )
    parts.append("</table></div>")
    parts.append("<div class='card'><h2>Candidate Deltas</h2><p class='muted'>Deltas compare candidate-vs-source scores against baseline-vs-source scores. They are not direct encode-vs-encode VMAF measurements.</p><table><tr><th>Rendition</th><th>Candidate</th><th>Quality</th><th>Bitrate Savings</th><th>VMAF Δ</th><th>P1 Δ</th><th>P5 Δ</th><th>SSIM Δ</th><th>Notes</th></tr>")
    for row in report["comparisons"]:
        parts.append(
            f"<tr><td><code>{esc(row.get('rendition_id', ''))}</code></td><td>{esc(row.get('candidate_run', ''))}</td>"
            f"<td>{esc(row.get('baseline_quality_status', ''))}->{esc(row.get('candidate_quality_status', ''))}</td>"
            f"<td>{format_percent(row.get('bitrate_savings_percent'))}</td><td>{format_delta(row.get('vmaf_mean_delta'))}</td>"
            f"<td>{format_delta(row.get('vmaf_p1_delta'))}</td><td>{format_delta(row.get('vmaf_p5_delta'))}</td>"
            f"<td>{format_delta(row.get('ssim_delta'), 6)}</td><td>{esc(comparison_note(row))}</td></tr>"
        )
    parts.append("</table></div></body></html>")
    output_path.write_text("\n".join(parts), encoding="utf-8")


def run_summary_row(run: dict[str, Any], role: str) -> str:
    summary = run.get("summary", {})
    return (
        f"<tr><td>{html.escape(role)}</td><td><code>{html.escape(run.get('name', ''))}</code></td>"
        f"<td>{html.escape(str(run.get('source_integrity') or ''))}</td>"
        f"<td>{summary.get('pass', '')}</td><td>{summary.get('warn', '')}</td><td>{summary.get('fail', '')}</td>"
        f"<td class='mono'>{html.escape(run.get('path', ''))}</td></tr>"
    )


def source_summary_markdown(summary: dict[str, Any]) -> list[str]:
    return [
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
    parts = ["<div class='card'><h2>Baseline Source Summary</h2><table><tr><th>Property</th><th>Value</th></tr>"]
    for label, value in rows:
        parts.append(f"<tr><td>{esc(label)}</td><td>{esc(str(value or ''))}</td></tr>")
    parts.append("</table></div>")
    return parts


def source_consistency_html(warnings: list[str]) -> str:
    esc = html.escape
    if not warnings:
        return "<div class='card'><h2>Source Consistency</h2><p>No obvious source mismatches detected across validation reports.</p></div>"
    items = "".join(f"<li>{esc(warning)}</li>" for warning in warnings)
    return f"<div class='card'><h2>Source Consistency</h2><ul>{items}</ul></div>"


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


def status_class(status: str) -> str:
    status = status.lower()
    if status in {"pass", "warn", "fail"}:
        return status
    return ""


def comparison_note(row: dict[str, Any]) -> str:
    if row.get("status") != "OK":
        return row.get("notes", "")
    notes = []
    savings = number(row.get("bitrate_savings_percent"))
    vmaf_delta = number(row.get("vmaf_mean_delta"))
    if savings is not None and savings > 0:
        notes.append(f"{savings:.1f}% bitrate savings")
    elif savings is not None and savings < 0:
        notes.append(f"{abs(savings):.1f}% bitrate increase")
    if vmaf_delta is not None and vmaf_delta > 0:
        notes.append(f"VMAF +{vmaf_delta:.3f}")
    elif vmaf_delta is not None and vmaf_delta < 0:
        notes.append(f"VMAF {vmaf_delta:.3f}")
    return "; ".join(notes) if notes else "No material delta"


def format_metric(value: Any, decimals: int = 3) -> str:
    numeric = number(value)
    return "" if numeric is None else f"{numeric:.{decimals}f}"


def format_percent(value: Any) -> str:
    numeric = number(value)
    return "" if numeric is None else f"{numeric:+.1f}%"


def format_delta(value: Any, decimals: int = 3) -> str:
    numeric = number(value)
    if numeric is None:
        return ""
    css = "pos" if numeric > 0 else "neg" if numeric < 0 else ""
    return f"<span class='{css}'>{numeric:+.{decimals}f}</span>" if css else f"{numeric:.{decimals}f}"


if __name__ == "__main__":
    raise SystemExit(main())
