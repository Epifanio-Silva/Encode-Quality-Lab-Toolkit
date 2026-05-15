from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml

from encoding_advisor.ffmpeg_utils import CODEC_ENCODERS, format_command, parse_bitrate_kbps


def probe_source_fps(source: Path) -> str | None:
    """Return the display frame rate of the source as a string (e.g. '60000/1001'), or None."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(source),
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        fps = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
        return fps if fps and fps != "0/0" else None
    except Exception:
        return None


DEFAULT_CRF = {
    "h264": 20,
    "hevc": 24,
    "av1": 30,
}

DEFAULT_PRESET = {
    "h264": "medium",
    "hevc": "slow",
    "av1": "6",
}


DEFAULT_ENCODER_PROFILES = {
    "profiles": {
        "crf_capped": {
            "description": "VOD/default CRF encode with ABR bitrate caps.",
            "mode": "crf",
            "crf": DEFAULT_CRF,
            "preset": DEFAULT_PRESET,
            "maxrate_multiplier": 1.0,
            "bufsize_multiplier": 2.0,
        }
    },
    "selection": {"default": "crf_capped"},
}


def load_encoder_profiles(path: Path) -> dict[str, Any]:
    if not path.exists():
        return DEFAULT_ENCODER_PROFILES
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    return deep_merge(dict(DEFAULT_ENCODER_PROFILES), loaded)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = deep_merge(dict(base[key]), value)
        else:
            base[key] = value
    return base


def print_encoder_profiles(encoder_profiles: dict[str, Any]) -> None:
    print("Encoder profiles:")
    for name, profile in encoder_profiles.get("profiles", {}).items():
        print(f"- {name}: {profile.get('description', '')}")


def select_encoder_profile(recommendation: dict[str, Any], encoder_profiles: dict[str, Any]) -> str:
    selection = encoder_profiles.get("selection", {})
    priority = recommendation.get("priority")
    use_case = recommendation.get("use_case")
    priority_map = selection.get("priority", {})
    use_case_map = selection.get("use_case", {})
    if priority in priority_map:
        return priority_map[priority]
    if use_case in use_case_map:
        return use_case_map[use_case]
    return selection.get("default", "crf_capped")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eqlab encode",
        description="Encode renditions from an advisor JSON profile ladder.",
    )
    parser.add_argument("--input", help="Source file to encode.")
    parser.add_argument("--profile", help="Advisor JSON profile report.")
    parser.add_argument("--output-dir", default="encodes", help="Directory for encoded renditions, logs, and plan/results files.")
    parser.add_argument("--encoder-profiles", default="config/encoder_profiles.yaml", help="Encoder profiles YAML file.")
    parser.add_argument("--encoder-profile", help="Named encoder profile to use. Defaults to advisor-based auto selection.")
    parser.add_argument("--list-encoder-profiles", action="store_true", help="List available encoder profiles and exit.")
    parser.add_argument("--preset", help="Override encoder preset for every rendition.")
    parser.add_argument("--crf", type=int, help="Override CRF for every rendition.")
    parser.add_argument("--audio-bitrate", default="192k", help="Audio bitrate for encoded outputs.")
    parser.add_argument("--audio-sample-rate", type=int, default=48000, help="Normalize encoded audio to this sample rate.")
    parser.add_argument("--audio-channels", type=int, default=2, help="Normalize encoded audio to this channel count.")
    parser.add_argument(
        "--color-mode",
        choices=["auto", "preserve", "bt709", "tonemap_bt709"],
        default="auto",
        help="Output color handling. Auto tonemaps HDR/PQ sources to BT.709 for H.264 compatibility outputs.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print planned FFmpeg commands without encoding.")
    parser.add_argument("--run", action="store_true", help="Execute planned FFmpeg commands.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = Path(args.output_dir)
    encoder_profiles = load_encoder_profiles(Path(args.encoder_profiles))

    if args.list_encoder_profiles:
        print_encoder_profiles(encoder_profiles)
        return 0

    if not args.input:
        print("ERROR: --input is required unless --list-encoder-profiles is used.")
        return 2
    if not args.profile:
        print("ERROR: --profile is required unless --list-encoder-profiles is used.")
        return 2

    source = Path(args.input)
    profile_path = Path(args.profile)
    if not source.exists():
        print(f"ERROR: source file does not exist: {source}")
        return 2
    if not profile_path.exists():
        print(f"ERROR: advisor profile does not exist: {profile_path}")
        return 2

    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    recommendation = profile.get("recommendation", {})
    selected_profile_name = args.encoder_profile or select_encoder_profile(recommendation, encoder_profiles)
    selected_profile = encoder_profiles.get("profiles", {}).get(selected_profile_name)
    if not selected_profile:
        print(f"ERROR: encoder profile '{selected_profile_name}' was not found.")
        return 2
    source_fps = probe_source_fps(source)
    if source_fps:
        print(f"Source frame rate: {source_fps}")
    jobs = build_ladder_jobs(
        source=source,
        source_info=profile.get("source", {}),
        recommendation=recommendation,
        output_dir=output_dir / "renditions",
        encoder_profile_name=selected_profile_name,
        encoder_profile=selected_profile,
        preset_override=args.preset,
        crf_override=args.crf,
        audio_bitrate=args.audio_bitrate,
        audio_sample_rate=args.audio_sample_rate,
        audio_channels=args.audio_channels,
        color_mode=args.color_mode,
        source_fps=source_fps,
    )

    print("Encoding Profile Encoder")
    print(f"Source: {source}")
    print(f"Advisor profile: {profile_path}")
    print(f"Output directory: {output_dir}")
    print(f"Codec/profile: {recommendation.get('video_codec')} / {recommendation.get('profile')}")
    print(f"Encoder profile: {selected_profile_name} ({selected_profile.get('mode', 'crf')})")
    print(f"Renditions: {len(jobs)}")
    print("Mode: run encodes" if args.run else "Mode: dry run only")
    print()

    for job in jobs:
        print(f"Rendition: {job['id']}")
        print(f"Output: {job['output']}")
        print(format_command(job["command"]))
        print()

    write_plan(output_dir, source, profile_path, recommendation, selected_profile_name, selected_profile, jobs)

    if not args.run:
        print("Use --run to execute these commands.")
        return 0

    results = run_jobs(jobs, output_dir)
    write_results(output_dir, results)
    failures = [result for result in results if result["returncode"] != 0]
    if failures:
        print(f"Completed with {len(failures)} failed encode(s). See logs in {output_dir / 'logs'}.")
        return 1
    print(f"All renditions completed. Results: {output_dir / 'encode_results.json'}")
    return 0


def build_ladder_jobs(
    *,
    source: Path,
    source_info: dict[str, Any],
    recommendation: dict[str, Any],
    output_dir: Path,
    encoder_profile_name: str,
    encoder_profile: dict[str, Any],
    preset_override: str | None,
    crf_override: int | None,
    audio_bitrate: str,
    audio_sample_rate: int,
    audio_channels: int,
    color_mode: str,
    source_fps: str | None = None,
) -> list[dict[str, Any]]:
    codec = recommendation["video_codec"]
    encoder = CODEC_ENCODERS[codec]
    profile = recommendation.get("profile")
    keyint = recommendation.get("keyframe_interval_frames")
    gop_duration = recommendation.get("gop_duration")
    ladder = recommendation.get("ladder") or []
    mode = encoder_profile.get("mode", "crf")
    preset = preset_override or codec_value(encoder_profile.get("preset", {}), codec, DEFAULT_PRESET.get(codec, "medium"))
    crf = crf_override if crf_override is not None else codec_value(encoder_profile.get("crf", {}), codec, DEFAULT_CRF.get(codec, 23))
    tune = codec_value(encoder_profile.get("tune", {}), codec)
    maxrate_multiplier = float(encoder_profile.get("maxrate_multiplier", 1.0))
    minrate_multiplier = encoder_profile.get("minrate_multiplier")
    bufsize_multiplier = float(encoder_profile.get("bufsize_multiplier", 2.0))
    pix_fmt = "yuv420p10le" if profile == "main10" else "yuv420p"
    output_color = resolve_output_color(codec, profile, color_mode, source_info)

    jobs = []
    for rendition in ladder:
        resolution = rendition["resolution"]
        scale_size = resolution.replace("x", ":")
        bitrate = rendition["bitrate"]
        rendition_id = f"{resolution}_{bitrate}".replace("x", "x").replace("k", "k")
        output = output_dir / f"{rendition_id}.mp4"
        target_kbps = parse_bitrate_kbps(bitrate)
        maxrate = f"{round(target_kbps * maxrate_multiplier)}k"
        bufsize = f"{round(target_kbps * bufsize_multiplier)}k"

        vf = build_video_filter(
            scale_size=scale_size,
            pix_fmt=pix_fmt,
            source_fps=source_fps,
            output_color=output_color,
        )
        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-sn",
            "-dn",
            "-map_metadata",
            "-1",
            "-vf",
            vf,
            "-af",
            "asetpts=PTS-STARTPTS",
            "-c:v",
            encoder,
            "-preset",
            str(preset),
        ]
        if tune:
            command.extend(["-tune", str(tune)])
        if mode == "cbr":
            minrate = f"{round(target_kbps * float(minrate_multiplier or maxrate_multiplier))}k"
            command.extend(["-b:v", bitrate, "-minrate", minrate, "-maxrate", maxrate, "-bufsize", bufsize])
        else:
            command.extend(["-crf", str(crf), "-maxrate", maxrate, "-bufsize", bufsize])
        if profile:
            command.extend(["-profile:v", profile])
        command.extend(["-pix_fmt", pix_fmt])
        if output_color in {"bt709", "tonemap_bt709"}:
            command.extend(["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"])
        add_aligned_gop_options(command, codec, keyint, gop_duration)
        if source_fps:
            command.extend(["-r", source_fps])
        command.extend([
            "-c:a",
            "aac",
            "-b:a",
            audio_bitrate,
            "-ar",
            str(audio_sample_rate),
            "-ac",
            str(audio_channels),
            "-movflags",
            "+faststart",
            str(output),
        ])

        jobs.append(
            {
                "id": rendition_id,
                "resolution": resolution,
                "bitrate": bitrate,
                "encoder_profile": encoder_profile_name,
                "rate_control_mode": mode,
                "preset": preset,
                "crf": crf if mode == "crf" else None,
                "maxrate": maxrate,
                "bufsize": bufsize,
                "audio_bitrate": audio_bitrate,
                "audio_sample_rate": audio_sample_rate,
                "audio_channels": audio_channels,
                "color_mode": output_color,
                "output": str(output),
                "command": command,
            }
        )
    return jobs


def resolve_output_color(codec: str, profile: str | None, color_mode: str, source_info: dict[str, Any]) -> str:
    if color_mode != "auto":
        return color_mode
    if codec == "h264" and profile != "main10":
        return "tonemap_bt709" if source_is_hdr(source_info) else "bt709"
    return "preserve"


def source_is_hdr(source_info: dict[str, Any]) -> bool:
    video = source_info.get("video", {}) if isinstance(source_info, dict) else {}
    transfer = str(video.get("color_transfer") or "").lower()
    primaries = str(video.get("color_primaries") or "").lower()
    color_space = str(video.get("color_space") or "").lower()
    return transfer in {"smpte2084", "arib-std-b67"} or "2020" in primaries or "2020" in color_space


def build_video_filter(
    *,
    scale_size: str,
    pix_fmt: str,
    source_fps: str | None,
    output_color: str,
) -> str:
    filters = []
    if source_fps:
        filters.append(f"fps={source_fps}")
    filters.append("setpts=PTS-STARTPTS")
    if output_color == "tonemap_bt709":
        filters.extend([
            "zscale=t=linear:npl=100",
            "tonemap=tonemap=hable:desat=0",
            "zscale=p=bt709:t=bt709:m=bt709",
            f"format={pix_fmt}",
            f"scale={scale_size}:flags=lanczos",
        ])
    else:
        filters.extend([
            f"scale={scale_size}:flags=lanczos",
            f"format={pix_fmt}",
        ])
    return ",".join(filters)


def add_aligned_gop_options(
    command: list[str],
    codec: str,
    keyint: int | None,
    gop_duration: int | float | str | None,
) -> None:
    if not keyint:
        return
    command.extend(["-g", str(keyint), "-keyint_min", str(keyint), "-sc_threshold", "0"])
    if gop_duration:
        command.extend(["-force_key_frames", f"expr:gte(t,n_forced*{gop_duration})"])
    if codec == "hevc":
        command.extend([
            "-forced-idr",
            "1",
            "-x265-params",
            f"keyint={keyint}:min-keyint={keyint}:scenecut=0:open-gop=0",
        ])
    elif codec == "h264":
        command.extend([
            "-x264-params",
            f"keyint={keyint}:min-keyint={keyint}:scenecut=0:open-gop=0",
        ])


def codec_value(values: dict[str, Any], codec: str, default: Any = None) -> Any:
    if not isinstance(values, dict):
        return default
    return values.get(codec, default)


def write_plan(
    output_dir: Path,
    source: Path,
    profile_path: Path,
    recommendation: dict[str, Any],
    encoder_profile_name: str,
    encoder_profile: dict[str, Any],
    jobs: list[dict[str, Any]],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    plan = {
        "source": str(source),
        "profile": str(profile_path),
        "recommendation_summary": {
            "video_codec": recommendation.get("video_codec"),
            "profile": recommendation.get("profile"),
            "audio_codec": recommendation.get("audio_codec"),
            "audio_sample_rate": recommendation.get("audio_sample_rate"),
            "audio_channels": recommendation.get("audio_channels"),
            "color_mode": recommendation.get("color_mode"),
            "output_color_space": recommendation.get("output_color_space"),
            "packaging": recommendation.get("packaging"),
            "segment_format": recommendation.get("segment_format"),
            "gop_duration": recommendation.get("gop_duration"),
            "keyframe_interval_frames": recommendation.get("keyframe_interval_frames"),
        },
        "encoder_profile": {
            "name": encoder_profile_name,
            "description": encoder_profile.get("description"),
            "mode": encoder_profile.get("mode", "crf"),
            "settings": encoder_profile,
        },
        "jobs": [
            {
                **job,
                "command_text": format_command(job["command"]),
            }
            for job in jobs
        ],
    }
    (output_dir / "encode_plan.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")


def run_jobs(jobs: list[dict[str, Any]], output_dir: Path) -> list[dict[str, Any]]:
    logs_dir = output_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    for job in jobs:
        Path(job["output"]).parent.mkdir(parents=True, exist_ok=True)

    results = []
    for job in jobs:
        print(f"Running: {job['id']}")
        start = time.monotonic()
        completed = subprocess.run(job["command"], capture_output=True, text=True)
        elapsed = round(time.monotonic() - start, 3)

        stdout_path = logs_dir / f"{job['id']}.stdout.log"
        stderr_path = logs_dir / f"{job['id']}.stderr.log"
        stdout_path.write_text(completed.stdout, encoding="utf-8")
        stderr_path.write_text(completed.stderr, encoding="utf-8")

        output = Path(job["output"])
        result = {
            "id": job["id"],
            "resolution": job["resolution"],
            "bitrate": job["bitrate"],
            "output": job["output"],
            "output_size_bytes": output.stat().st_size if output.exists() else None,
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
    (output_dir / "encode_results.json").write_text(json.dumps({"renditions": results}, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
