from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml

from encoding_advisor.ffmpeg_utils import CODEC_ENCODERS, format_command


REQUIRED_ENCODE_FIELDS = {"id", "codec", "preset", "crf", "maxrate", "bufsize"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eqlab experiment",
        description="Plan encode experiments for comparing candidate settings with objective quality metrics.",
    )
    parser.add_argument("--input", required=True, help="Source file to encode and compare against.")
    parser.add_argument("--profile", help="Advisor JSON report to use as the starting profile.")
    parser.add_argument(
        "--matrix",
        default="config/experiment_matrix.yaml",
        help="Experiment matrix YAML defining encode candidates.",
    )
    parser.add_argument(
        "--metrics",
        nargs="+",
        default=["vmaf", "ssimwave"],
        help="Quality metrics to evaluate, such as vmaf ssimwave psnr ssim.",
    )
    parser.add_argument(
        "--output-dir",
        default="experiments",
        help="Directory where experiment outputs and reports will be written.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the experiment plan without running encodes. This is the default unless --run is set.",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Execute planned FFmpeg encode commands.",
    )
    parser.add_argument(
        "--metrics-only",
        action="store_true",
        help="Run metrics against existing encoded outputs without re-running encodes.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = Path(args.output_dir)
    source_path = Path(args.input)

    if not source_path.exists():
        print(f"ERROR: source file does not exist: {source_path}")
        return 2

    try:
        profile = load_profile(Path(args.profile)) if args.profile else {}
        matrix = load_matrix(Path(args.matrix))
        validate_matrix(matrix)
    except ExperimentConfigError as exc:
        print(f"ERROR: {exc}")
        return 2

    recommendation = profile.get("recommendation", {})
    encodes_dir = output_dir / "encodes"
    planned_jobs = [
        build_encode_job(
            source=source_path,
            candidate=candidate,
            recommendation=recommendation,
            encodes_dir=encodes_dir,
        )
        for candidate in matrix["encodes"]
    ]

    run_encodes = bool(args.run)
    run_metrics = bool(args.run or args.metrics_only)

    print("Encoding Experiment Planner")
    print(f"Source: {source_path}")
    print(f"Advisor profile: {args.profile or 'not provided'}")
    print(f"Matrix: {args.matrix}")
    print(f"Metrics: {', '.join(args.metrics)}")
    print(f"Output directory: {output_dir}")
    if recommendation:
        print(f"Profile basis: {recommendation.get('video_codec', 'unknown')} / {recommendation.get('profile', 'unknown')}, {recommendation.get('packaging', 'unknown')}")
    print()
    if args.metrics_only:
        print("Mode: metrics only")
    else:
        print("Mode: run encodes" if run_encodes else "Mode: dry run only")
    print()
    print("Planned encode jobs:")
    for job in planned_jobs:
        print()
        print(f"Candidate: {job['id']}")
        print(f"Output: {job['output']}")
        print(format_command(job["command"]))
    print()

    write_plan(output_dir, planned_jobs, args, recommendation)

    if not run_encodes and not run_metrics:
        print("Use --run to execute these commands.")
        return 0

    results = load_existing_results(output_dir, planned_jobs)
    if run_encodes:
        results = run_jobs(planned_jobs, output_dir)
        write_results(output_dir, results)
        failures = [result for result in results if result["returncode"] != 0]
        if failures:
            print()
            print(f"Completed with {len(failures)} failed encode(s). See logs in {output_dir / 'logs'}.")
            return 1
        print()
        print(f"All encodes completed. Results: {output_dir / 'encode_results.json'}")

    if run_metrics:
        metric_results = run_metrics_for_jobs(source_path, results, args.metrics, output_dir)
        write_metrics(output_dir, metric_results)
        summary = build_summary(source_path, results, metric_results, matrix)
        write_summary(output_dir, summary)
        print()
        print(f"Metric results: {output_dir / 'metrics_results.json'}")
        print(f"Experiment summary: {output_dir / 'experiment_summary.md'}")
    return 0


class ExperimentConfigError(ValueError):
    pass


def load_profile(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ExperimentConfigError(f"advisor profile does not exist: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ExperimentConfigError(f"advisor profile is not valid JSON: {path}") from exc


def load_matrix(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ExperimentConfigError(f"experiment matrix does not exist: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ExperimentConfigError("experiment matrix must be a YAML mapping")
    return data


def validate_matrix(matrix: dict[str, Any]) -> None:
    encodes = matrix.get("encodes")
    if not isinstance(encodes, list) or not encodes:
        raise ExperimentConfigError("experiment matrix must include a non-empty encodes list")

    seen_ids = set()
    for index, candidate in enumerate(encodes, start=1):
        if not isinstance(candidate, dict):
            raise ExperimentConfigError(f"encode candidate #{index} must be a mapping")
        missing = sorted(REQUIRED_ENCODE_FIELDS - set(candidate))
        if missing:
            raise ExperimentConfigError(f"encode candidate #{index} is missing required fields: {', '.join(missing)}")
        if candidate["id"] in seen_ids:
            raise ExperimentConfigError(f"duplicate encode candidate id: {candidate['id']}")
        seen_ids.add(candidate["id"])
        if candidate["codec"] not in CODEC_ENCODERS:
            raise ExperimentConfigError(f"unsupported codec in candidate {candidate['id']}: {candidate['codec']}")


def build_encode_job(
    *,
    source: Path,
    candidate: dict[str, Any],
    recommendation: dict[str, Any],
    encodes_dir: Path,
) -> dict[str, Any]:
    codec = candidate["codec"]
    encoder = CODEC_ENCODERS[codec]
    output = encodes_dir / f"{candidate['id']}.mp4"
    keyint = recommendation.get("keyframe_interval_frames")
    profile = candidate.get("profile") or ("high" if codec == "h264" else recommendation.get("profile"))
    pix_fmt = candidate.get("pix_fmt") or ("yuv420p" if profile != "main10" else "yuv420p10le")

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-c:v",
        encoder,
        "-preset",
        str(candidate["preset"]),
        "-crf",
        str(candidate["crf"]),
        "-maxrate",
        str(candidate["maxrate"]),
        "-bufsize",
        str(candidate["bufsize"]),
    ]

    if profile:
        command.extend(["-profile:v", str(profile)])
    command.extend(["-pix_fmt", pix_fmt])

    if keyint:
        command.extend(["-g", str(keyint), "-keyint_min", str(keyint), "-sc_threshold", "0"])

    command.extend(["-c:a", "aac", "-b:a", "192k", str(output)])

    return {
        "id": candidate["id"],
        "output": str(output),
        "command": command,
    }


def write_plan(output_dir: Path, planned_jobs: list[dict[str, Any]], args: argparse.Namespace, recommendation: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    plan = {
        "source": args.input,
        "profile": args.profile,
        "matrix": args.matrix,
        "metrics": args.metrics,
        "recommendation_summary": {
            "video_codec": recommendation.get("video_codec"),
            "profile": recommendation.get("profile"),
            "packaging": recommendation.get("packaging"),
            "segment_format": recommendation.get("segment_format"),
            "gop_duration": recommendation.get("gop_duration"),
            "keyframe_interval_frames": recommendation.get("keyframe_interval_frames"),
        },
        "jobs": [
            {
                "id": job["id"],
                "output": job["output"],
                "command": job["command"],
                "command_text": format_command(job["command"]),
            }
            for job in planned_jobs
        ],
    }
    (output_dir / "plan.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")


def run_jobs(planned_jobs: list[dict[str, Any]], output_dir: Path) -> list[dict[str, Any]]:
    encodes_dir = output_dir / "encodes"
    logs_dir = output_dir / "logs"
    encodes_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for job in planned_jobs:
        print(f"Running: {job['id']}")
        start = time.monotonic()
        completed = subprocess.run(job["command"], capture_output=True, text=True)
        elapsed = round(time.monotonic() - start, 3)

        stdout_path = logs_dir / f"{job['id']}.stdout.log"
        stderr_path = logs_dir / f"{job['id']}.stderr.log"
        stdout_path.write_text(completed.stdout, encoding="utf-8")
        stderr_path.write_text(completed.stderr, encoding="utf-8")

        result = {
            "id": job["id"],
            "output": job["output"],
            "output_size_bytes": Path(job["output"]).stat().st_size if Path(job["output"]).exists() else None,
            "returncode": completed.returncode,
            "elapsed_seconds": elapsed,
            "stdout_log": str(stdout_path),
            "stderr_log": str(stderr_path),
        }
        results.append(result)

        status = "ok" if completed.returncode == 0 else f"failed ({completed.returncode})"
        print(f"  {status} in {elapsed}s")
    return results


def write_results(output_dir: Path, results: list[dict[str, Any]]) -> None:
    (output_dir / "encode_results.json").write_text(json.dumps({"encodes": results}, indent=2) + "\n", encoding="utf-8")


def load_existing_results(output_dir: Path, planned_jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results_path = output_dir / "encode_results.json"
    if results_path.exists():
        data = json.loads(results_path.read_text(encoding="utf-8"))
        return data.get("encodes", [])
    return [
        {
            "id": job["id"],
            "output": job["output"],
            "output_size_bytes": Path(job["output"]).stat().st_size if Path(job["output"]).exists() else None,
            "returncode": 0 if Path(job["output"]).exists() else None,
            "elapsed_seconds": None,
        }
        for job in planned_jobs
    ]


def run_metrics_for_jobs(source: Path, encode_results: list[dict[str, Any]], metrics: list[str], output_dir: Path) -> list[dict[str, Any]]:
    metrics_dir = output_dir / "metrics"
    logs_dir = output_dir / "logs"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    available_filters = ffmpeg_filters()
    results = []
    for encode in encode_results:
        output = Path(encode["output"])
        candidate_result = {
            "id": encode["id"],
            "output": encode["output"],
            "metrics": {},
        }
        if not output.exists():
            candidate_result["metrics_error"] = "encoded output does not exist"
            results.append(candidate_result)
            continue

        print(f"Metrics: {encode['id']}")
        for metric in metrics:
            metric = metric.lower()
            if metric == "vmaf":
                candidate_result["metrics"]["vmaf"] = run_vmaf(source, output, encode["id"], metrics_dir, logs_dir, available_filters)
            elif metric == "ssim":
                candidate_result["metrics"]["ssim"] = run_ffmpeg_pair_metric("ssim", source, output, encode["id"], metrics_dir, logs_dir, available_filters)
            elif metric == "psnr":
                candidate_result["metrics"]["psnr"] = run_ffmpeg_pair_metric("psnr", source, output, encode["id"], metrics_dir, logs_dir, available_filters)
            elif metric == "ssimwave":
                candidate_result["metrics"]["ssimwave"] = run_ssimwave(source, output)
            else:
                candidate_result["metrics"][metric] = {"status": "skipped", "reason": f"unsupported metric: {metric}"}
            status = candidate_result["metrics"][metric].get("status")
            value = candidate_result["metrics"][metric].get("value")
            print(f"  {metric}: {status}" + (f" ({value})" if value is not None else ""))
        results.append(candidate_result)
    return results


def run_vmaf(source: Path, encoded: Path, candidate_id: str, metrics_dir: Path, logs_dir: Path, available_filters: set[str]) -> dict[str, Any]:
    if "libvmaf" not in available_filters:
        return {"status": "skipped", "reason": "ffmpeg libvmaf filter is not available"}

    log_path = metrics_dir / f"{candidate_id}_vmaf.json"
    stderr_path = logs_dir / f"{candidate_id}_vmaf.stderr.log"
    filtergraph = (
        "[0:v]setpts=PTS-STARTPTS,format=yuv420p[dist];"
        "[1:v]setpts=PTS-STARTPTS,format=yuv420p[ref];"
        f"[dist][ref]libvmaf=log_fmt=json:log_path={shlex.quote(str(log_path))}"
    )
    command = ["ffmpeg", "-y", "-i", str(encoded), "-i", str(source), "-lavfi", filtergraph, "-f", "null", "-"]
    completed = subprocess.run(command, capture_output=True, text=True)
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        return {"status": "failed", "returncode": completed.returncode, "stderr_log": str(stderr_path)}

    try:
        data = json.loads(log_path.read_text(encoding="utf-8"))
        value = data.get("pooled_metrics", {}).get("vmaf", {}).get("mean")
    except (json.JSONDecodeError, OSError):
        value = None
    return {"status": "ok", "value": value, "log": str(log_path), "stderr_log": str(stderr_path)}


def run_ffmpeg_pair_metric(metric: str, source: Path, encoded: Path, candidate_id: str, metrics_dir: Path, logs_dir: Path, available_filters: set[str]) -> dict[str, Any]:
    if metric not in available_filters:
        return {"status": "skipped", "reason": f"ffmpeg {metric} filter is not available"}

    stats_path = metrics_dir / f"{candidate_id}_{metric}.log"
    stderr_path = logs_dir / f"{candidate_id}_{metric}.stderr.log"
    filtergraph = (
        "[0:v]setpts=PTS-STARTPTS,format=yuv420p[dist];"
        "[1:v]setpts=PTS-STARTPTS,format=yuv420p[ref];"
        f"[dist][ref]{metric}=stats_file={shlex.quote(str(stats_path))}"
    )
    command = ["ffmpeg", "-y", "-i", str(encoded), "-i", str(source), "-lavfi", filtergraph, "-f", "null", "-"]
    completed = subprocess.run(command, capture_output=True, text=True)
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        return {"status": "failed", "returncode": completed.returncode, "stderr_log": str(stderr_path)}
    return {"status": "ok", "value": parse_ffmpeg_metric_value(metric, completed.stderr), "log": str(stats_path), "stderr_log": str(stderr_path)}


def run_ssimwave(source: Path, encoded: Path) -> dict[str, Any]:
    if not shutil.which("ssimwave"):
        return {"status": "skipped", "reason": "ssimwave command not found on PATH"}
    return {"status": "skipped", "reason": "ssimwave integration is not implemented yet"}


def ffmpeg_filters() -> set[str]:
    completed = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True)
    filters = set()
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            filters.add(parts[1])
    return filters


def parse_ffmpeg_metric_value(metric: str, stderr: str) -> float | None:
    lines = [line for line in stderr.splitlines() if metric.upper() in line.upper()]
    if not lines:
        return None
    last = lines[-1]
    if metric == "ssim":
        marker = "All:"
        if marker in last:
            return _parse_float_after(last, marker)
    if metric == "psnr":
        marker = "average:"
        if marker in last:
            return _parse_float_after(last, marker)
    return None


def _parse_float_after(text: str, marker: str) -> float | None:
    try:
        tail = text.split(marker, 1)[1].strip()
        return float(tail.split()[0])
    except (IndexError, ValueError):
        return None


def write_metrics(output_dir: Path, results: list[dict[str, Any]]) -> None:
    (output_dir / "metrics_results.json").write_text(json.dumps({"candidates": results}, indent=2) + "\n", encoding="utf-8")


def build_summary(
    source: Path,
    encode_results: list[dict[str, Any]],
    metric_results: list[dict[str, Any]],
    matrix: dict[str, Any],
) -> dict[str, Any]:
    source_duration = probe_duration(source)
    encode_by_id = {item["id"]: item for item in encode_results}
    ranking_policy = matrix.get("ranking", {})

    rows = []
    for metric_item in metric_results:
        candidate_id = metric_item["id"]
        encode = encode_by_id.get(candidate_id, {})
        output_size = encode.get("output_size_bytes")
        output_path = Path(metric_item["output"])
        if output_size is None and output_path.exists():
            output_size = output_path.stat().st_size

        vmaf_stats = vmaf_stats_from_metric(metric_item.get("metrics", {}).get("vmaf", {}))
        ssim_value = metric_item.get("metrics", {}).get("ssim", {}).get("value")
        psnr_value = metric_item.get("metrics", {}).get("psnr", {}).get("value")
        bitrate_kbps = bitrate_from_size(output_size, source_duration)
        status = quality_status(vmaf_stats)

        rows.append(
            {
                "id": candidate_id,
                "output": metric_item["output"],
                "size_bytes": output_size,
                "size_human": human_size(output_size),
                "effective_total_bitrate_kbps": bitrate_kbps,
                "encode_seconds": encode.get("elapsed_seconds"),
                "vmaf_mean": vmaf_stats.get("mean"),
                "vmaf_min": vmaf_stats.get("min"),
                "vmaf_max": vmaf_stats.get("max"),
                "vmaf_mean_to_min_drop": vmaf_stats.get("mean_to_min_drop"),
                "ssim": ssim_value,
                "psnr": psnr_value,
                "quality_per_mbps": quality_per_mbps(vmaf_stats.get("mean"), bitrate_kbps),
                "status": status,
            }
        )

    recommended = recommend_candidate(rows, ranking_policy)
    return {
        "source": str(source),
        "source_duration_seconds": source_duration,
        "ranking_policy": ranking_policy,
        "recommended_candidate": recommended,
        "candidates": rows,
    }


def write_summary(output_dir: Path, summary: dict[str, Any]) -> None:
    (output_dir / "experiment_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (output_dir / "experiment_summary.md").write_text(summary_markdown(summary), encoding="utf-8")


def vmaf_stats_from_metric(metric: dict[str, Any]) -> dict[str, float | None]:
    log_path = metric.get("log")
    if metric.get("status") != "ok" or not log_path:
        return {"mean": None, "min": None, "max": None, "mean_to_min_drop": None}
    try:
        data = json.loads(Path(log_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"mean": metric.get("value"), "min": None, "max": None, "mean_to_min_drop": None}

    pooled = data.get("pooled_metrics", {}).get("vmaf", {})
    mean = pooled.get("mean") or metric.get("value")
    minimum = pooled.get("min")
    maximum = pooled.get("max")
    if minimum is None:
        frames = data.get("frames", [])
        values = [frame.get("metrics", {}).get("vmaf") for frame in frames]
        values = [value for value in values if value is not None]
        minimum = min(values) if values else None
        maximum = max(values) if values else None
    drop = round(mean - minimum, 6) if mean is not None and minimum is not None else None
    return {"mean": mean, "min": minimum, "max": maximum, "mean_to_min_drop": drop}


def quality_status(vmaf_stats: dict[str, float | None]) -> str:
    mean = vmaf_stats.get("mean")
    minimum = vmaf_stats.get("min")
    drop = vmaf_stats.get("mean_to_min_drop")
    if mean is None or minimum is None:
        return "UNKNOWN"
    if minimum < 70:
        return "FAIL"
    if mean >= 90 and minimum >= 80 and (drop is None or drop <= 15):
        return "PASS"
    if mean >= 90:
        return "WARN"
    return "FAIL"


def recommend_candidate(rows: list[dict[str, Any]], ranking_policy: dict[str, Any]) -> dict[str, Any] | None:
    minimum_vmaf = ranking_policy.get("minimum_vmaf", 0)
    eligible = [
        row
        for row in rows
        if row.get("vmaf_mean") is not None and row["vmaf_mean"] >= minimum_vmaf and row.get("size_bytes")
    ]
    if not eligible:
        return None
    selected = sorted(eligible, key=lambda row: (row["size_bytes"], -(row.get("vmaf_mean") or 0)))[0]
    return {
        "id": selected["id"],
        "reason": f"Smallest output above minimum VMAF threshold {minimum_vmaf}.",
    }


def summary_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Experiment Summary",
        "",
        f"Source: `{summary['source']}`",
        f"Duration: `{summary['source_duration_seconds']}` seconds",
        "",
    ]
    if summary.get("recommended_candidate"):
        rec = summary["recommended_candidate"]
        lines.extend([f"Recommended candidate: `{rec['id']}`", "", f"Reason: {rec['reason']}", ""])

    lines.extend(
        [
            "## Candidates",
            "",
        "| Candidate | Status | Size | Effective kbps | Encode sec | VMAF mean | VMAF min | SSIM | Quality/Mbps |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in summary["candidates"]:
        lines.append(
            "| {id} | {status} | {size} | {bitrate} | {seconds} | {vmaf_mean} | {vmaf_min} | {ssim} | {quality_per_mbps} |".format(
                id=row["id"],
                status=row["status"],
                size=row["size_human"],
                bitrate=format_number(row["effective_total_bitrate_kbps"]),
                seconds=format_number(row["encode_seconds"]),
                vmaf_mean=format_number(row["vmaf_mean"]),
                vmaf_min=format_number(row["vmaf_min"]),
                ssim=format_number(row["ssim"], digits=6),
                quality_per_mbps=format_number(row["quality_per_mbps"]),
            )
        )
    return "\n".join(lines) + "\n"


def probe_duration(path: Path) -> float | None:
    command = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)]
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        return None
    try:
        return float(completed.stdout.strip())
    except ValueError:
        return None


def bitrate_from_size(size_bytes: int | None, duration_seconds: float | None) -> float | None:
    if not size_bytes or not duration_seconds:
        return None
    return round((size_bytes * 8) / duration_seconds / 1000, 3)


def quality_per_mbps(vmaf_mean: float | None, bitrate_kbps: float | None) -> float | None:
    if vmaf_mean is None or not bitrate_kbps:
        return None
    return round(vmaf_mean / (bitrate_kbps / 1000), 3)


def human_size(size_bytes: int | None) -> str:
    if size_bytes is None:
        return "unknown"
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    return f"{size_bytes / 1024:.1f} KB"


def format_number(value: Any, digits: int = 3) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
