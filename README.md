# Encode Quality Lab Toolkit

Encode Quality Lab Toolkit helps video engineers recommend, encode, validate, and compare streaming video profiles using FFmpeg, ffprobe, VMAF, PSNR, and SSIM.

The toolkit supports video engineering workflows around HLS, DASH, CMAF, DRM-ready delivery, live streaming, VOD, mobile delivery, OTT playback, and ABR ladder evaluation. It began as an encoding profile advisor, and now follows a modular workflow:

```text
advise -> encode -> validate -> compare
```

Use `./eqlab` as the local command interface from the project directory. The phase-specific Python scripts remain available as development entrypoints.

## What It Recommends

- Video codec: H.264, HEVC, or AV1
- Audio codec: AAC-LC, HE-AAC, AC-3, or E-AC-3 guidance
- Rate control mode for live, VOD, mobile, low-latency, and broadcast-like workflows
- GOP duration and keyframe interval
- Segment duration guidance
- Closed GOP and IDR alignment recommendations
- Starter bitrate ladders for H.264, HEVC, mobile, 1080p, and 4K workflows
- Packaging guidance for HLS, DASH, CMAF, and progressive MP4
- Validation warnings for common streaming issues

## Current Scope

The toolkit currently focuses on:

1. Inspecting source files or streams with `ffprobe`
2. Collecting delivery intent from CLI arguments
3. Recommending codec, rate control, GOP, packaging, and bitrate ladder
4. Encoding recommended ABR renditions with configurable FFmpeg profiles
5. Validating encoded renditions with VMAF, PSNR, SSIM, bitrate checks, source integrity checks, charts, and worst-frame thumbnails
6. Comparing completed validation reports across encode runs
7. Writing JSON, Markdown, CSV, and HTML reports where appropriate

Future versions can add per-title encoding, upscaled/display-space validation, GOP/IDR alignment checks, segment duration checks, manifest validation, DRM checks, audio loudness checks, and richer experiment ranking.

## Project Structure

```text
.
├── README.md
├── requirements.txt
├── advisor.py
├── eqlab
├── encoder.py
├── experiment.py
├── validate.py
├── compare.py
├── main.py
├── config/
│   ├── advisor_rules.yaml
│   ├── device_profiles.yaml
│   ├── encoder_profiles.yaml
│   ├── experiment_matrix.yaml
│   ├── ladder_presets.yaml
│   └── validation_rules.yaml
├── encoding_advisor/
│   ├── __init__.py
│   ├── classify_content.py
│   ├── config_loader.py
│   ├── probe_source.py
│   ├── recommend_codec.py
│   ├── recommend_gop.py
│   ├── recommend_ladder.py
│   ├── recommend_packaging.py
│   ├── recommendation.py
│   ├── report_generator.py
│   ├── rule_matcher.py
│   └── validate_profile.py
├── reports/
├── validation/
├── experiments/
└── samples/
```

## Requirements

- Python 3.10+
- FFmpeg / ffprobe available on your `PATH`

Install optional Python dependencies:

```bash
python -m pip install -r requirements.txt
```

The advisor uses PyYAML to load configurable recommendation policy from `config/*.yaml`.

## Preview Reports

After running the Indy sample workflow, open these generated HTML reports from the project directory:

- [Validation HTML preview](validation/indy_race_20s_crf_capped/validation_report.html)
- [Comparison HTML preview](comparisons/indy_race_20s/comparison_report.html)

These preview reports are generated artifacts and are ignored by git by default. They are meant for local review; publish curated samples separately when you want stable public demo URLs.

## End-to-End Workflow

The normal workflow is:

```text
advise -> encode -> validate -> compare
```

The advisor creates an explainable recommendation profile. The encoder turns that profile into rendition files. The validator measures those renditions against the original source. The comparer reads completed validation reports and compares runs without recalculating metrics.

Run commands through the local umbrella CLI from the project directory:

```bash
./eqlab advise --list-options
```

Later, when packaged, this can become a global `eqlab` command. The phase scripts can still be run directly during development, but `./eqlab` is the documented workflow interface.

### 1. Create an Advisor Profile

```bash
./eqlab advise \
  --input source/indy_race_20s.mov \
  --use-case vod \
  --protocol hls \
  --devices ios web roku apple_tv smart_tv \
  --priority 4k_hdr_quality \
  --segment-duration 6 \
  --content-complexity sports \
  --report-name indy_race_20s
```

Expected profile outputs:

```text
reports/indy_race_20s.json
reports/indy_race_20s.md
```

### 2. Encode the Recommended Ladder

Dry run first:

```bash
./eqlab encode \
  --input source/indy_race_20s.mov \
  --profile reports/indy_race_20s.json \
  --output-dir encodes/indy_race_20s_crf_capped \
  --encoder-profile crf_capped
```

Run the encode:

```bash
./eqlab encode \
  --input source/indy_race_20s.mov \
  --profile reports/indy_race_20s.json \
  --output-dir encodes/indy_race_20s_crf_capped \
  --encoder-profile crf_capped \
  --run
```

Expected encode outputs:

```text
encodes/indy_race_20s_crf_capped/encode_plan.json
encodes/indy_race_20s_crf_capped/encode_results.json
encodes/indy_race_20s_crf_capped/renditions/
encodes/indy_race_20s_crf_capped/logs/
```

### 3. Validate the Encoded Renditions

```bash
./eqlab validate \
  --source source/indy_race_20s.mov \
  --profile reports/indy_race_20s.json \
  --encoded-dir encodes/indy_race_20s_crf_capped/renditions \
  --output-dir validation/indy_race_20s_crf_capped
```

Expected validation outputs:

```text
validation/indy_race_20s_crf_capped/validation_report.json
validation/indy_race_20s_crf_capped/validation_summary.csv
validation/indy_race_20s_crf_capped/validation_report.md
validation/indy_race_20s_crf_capped/validation_report.html
validation/indy_race_20s_crf_capped/metrics/
validation/indy_race_20s_crf_capped/charts/
validation/indy_race_20s_crf_capped/thumbnails/
validation/indy_race_20s_crf_capped/logs/
```

Current validation metrics are source-referenced native rendition metrics:

```text
encoded rendition
vs
original source scaled to rendition resolution
```

### 4. Create Candidate Runs

To compare encoder settings, create additional encodes using the same advisor profile but different encoder profiles:

```bash
./eqlab encode \
  --input source/indy_race_20s.mov \
  --profile reports/indy_race_20s.json \
  --output-dir encodes/indy_race_20s_crf_quality \
  --encoder-profile crf_quality \
  --run
```

```bash
./eqlab encode \
  --input source/indy_race_20s.mov \
  --profile reports/indy_race_20s.json \
  --output-dir encodes/indy_race_20s_file_size \
  --encoder-profile file_size \
  --run
```

Validate each candidate run:

```bash
./eqlab validate \
  --source source/indy_race_20s.mov \
  --profile reports/indy_race_20s.json \
  --encoded-dir encodes/indy_race_20s_crf_quality/renditions \
  --output-dir validation/indy_race_20s_crf_quality
```

```bash
./eqlab validate \
  --source source/indy_race_20s.mov \
  --profile reports/indy_race_20s.json \
  --encoded-dir encodes/indy_race_20s_file_size/renditions \
  --output-dir validation/indy_race_20s_file_size
```

### 5. Compare Completed Validation Reports

```bash
./eqlab compare \
  --baseline validation/indy_race_20s_crf_capped/validation_report.json \
  --candidate validation/indy_race_20s_crf_quality/validation_report.json \
  --candidate validation/indy_race_20s_file_size/validation_report.json \
  --candidate-name crf_quality \
  --candidate-name file_size \
  --output-dir comparisons/indy_race_20s
```

Expected comparison outputs:

```text
comparisons/indy_race_20s/comparison_report.json
comparisons/indy_race_20s/comparison_summary.csv
comparisons/indy_race_20s/comparison_runs.csv
comparisons/indy_race_20s/comparison_report.md
comparisons/indy_race_20s/comparison_report.html
```

The comparison phase does not run VMAF again. It compares validation scores already measured against the original source:

```text
baseline encode vs original source
candidate encode vs original source
then compare those measured scores
```

## Example

```bash
./eqlab advise \
  --input source/indy_race_20s.mov \
  --use-case vod \
  --protocol hls \
  --devices ios web roku apple_tv smart_tv \
  --priority 4k_hdr_quality \
  --segment-duration 6 \
  --content-complexity sports
```

For a 4K/HDR sports-oriented workflow, use:

```bash
./eqlab advise \
  --input source/indy_race_20s.mov \
  --use-case vod \
  --protocol hls \
  --devices ios web roku apple_tv smart_tv \
  --priority 4k_hdr_quality \
  --segment-duration 6 \
  --content-complexity sports \
  --report-name indy_race_20s
```

This selects a UHD/HDR-oriented HEVC ladder when the source and device targets support it.

## CLI Option Legend

Print the available option values from the CLI:

```bash
./eqlab advise --list-options
```

Print the matched rules after generating a recommendation:

```bash
./eqlab advise --input source/indy_race_20s.mov --explain-rules
```

`--input`

Source file path or stream URL to inspect with `ffprobe`.

Examples:

- `source/indy_race_20s.mov`
- `/Volumes/media/indy_race_20s.mov`
- `/absolute/path/to/mezzanine.mov`
- `udp://239.1.1.1:1234`
- `srt://encoder.example.com:9000`
- `rtmp://origin.example.com/live/channel`

If `--input` is provided and cannot be probed, the command fails unless `--allow-probe-failure` is set.

`--use-case`

Allowed values:

| Value | Meaning |
| --- | --- |
| `live` | General live streaming workflow |
| `live_ott` | Live OTT ABR delivery |
| `vod` | Video-on-demand/file-based delivery |
| `mobile` | Mobile-first delivery |
| `ott` | General OTT device delivery |
| `broadcast` | Broadcast-like linear workflow |
| `low_latency` | Low-latency streaming workflow |

`--protocol`

Allowed values:

| Value | Meaning |
| --- | --- |
| `hls` | Alias for `hls_ts`; HLS with MPEG-TS segments |
| `hls_ts` | HLS with MPEG-TS segments |
| `hls_fmp4` | HLS with fragmented MP4 segments |
| `dash` | Alias for `dash_fmp4`; MPEG-DASH with fMP4 segments |
| `dash_fmp4` | MPEG-DASH with fragmented MP4 segments |
| `hls_dash` | Alias for `hls_dash_cmaf`; parallel HLS+DASH with CMAF/fMP4 segments |
| `hls_dash_cmaf` | HLS+DASH CMAF delivery using CMAF-constrained fMP4 segments |
| `cmaf` | Alias for `hls_dash_cmaf` |
| `progressive_mp4` | Single non-segmented MP4 file; not CMAF/fMP4 segmented ABR |

The report includes both `packaging` and `segment_format`. For example, `hls_fmp4` reports `segment_format: fmp4`, while `progressive_mp4` reports `segment_format: none`.

`--devices`

One or more target device labels. Common values:

| Value | Meaning |
| --- | --- |
| `ios` | iPhone/iPad playback |
| `android` | Android phones/tablets/TV variants |
| `web` | Browser playback |
| `roku` | Roku devices |
| `fire_tv` | Amazon Fire TV devices |
| `apple_tv` | Apple TV |
| `smart_tv` | Smart TV apps |
| `legacy_stb` | Legacy set-top boxes; favors conservative H.264 compatibility |

Example:

```bash
--devices ios web roku apple_tv smart_tv
```

`--priority`

Allowed values:

| Value | Meaning |
| --- | --- |
| `compatibility` | Favor broad playback support; usually H.264 |
| `quality` | General quality-oriented default |
| `lowest_latency` | Favor latency-sensitive choices |
| `smallest_file_size` | Favor aggressive compression; can select AV1 for VOD |
| `mobile_efficiency` | Favor mobile-friendly codec and bitrate choices |
| `bandwidth_savings` | Favor HEVC for bandwidth reduction when legacy playback is not required |
| `4k_hdr_quality` | Favor HEVC/Main10-style UHD/HDR decisions |

`--segment-duration`

Target segment duration in seconds. Common values:

| Value | Typical use |
| --- | --- |
| `1` or `2` | Low latency or CMAF chunk-oriented workflows |
| `4` | Mobile or lower-latency live |
| `6` | Common live OTT and VOD default |
| `8` | VOD/cache efficiency when latency is less important |

The advisor tries to choose a GOP duration that divides evenly into the segment duration.

`--content-complexity`

Allowed values:

| Value | Bitrate effect |
| --- | ---: |
| `low` | Reduce ladder bitrates |
| `medium` | Use preset bitrates |
| `high` | Increase ladder bitrates |
| `very_high` | Increase more aggressively |
| `sports` | Increase for motion/detail |
| `gaming` | Increase for motion/detail |
| `animation` | Slight reduction from default |
| `screen` | Reduction for screen/text content |

## Example Without ffprobe Input

You can test recommendation logic before you have a source file ready:

```bash
./eqlab advise \
  --use-case vod \
  --protocol hls \
  --devices ios web roku apple_tv smart_tv \
  --priority quality \
  --segment-duration 6 \
  --content-complexity sports
```

If `--input` is provided, the CLI treats source probing as required. If `ffprobe` cannot read the path or stream, the command exits without generating a recommendation. To intentionally continue with generic defaults after a probe failure, add:

```bash
./eqlab advise --input source/indy_race_20s.mov --allow-probe-failure
```


## Output

Reports are written to `reports/` by default:

- `encoding_profile_<timestamp>.json`
- `encoding_profile_<timestamp>.md`

You can override the output directory:

```bash
./eqlab advise --input source/indy_race_20s.mov --output-dir /tmp/encoding-report
```

Use `--report-name` when you want stable filenames for later experiment runs:

```bash
./eqlab advise \
  --input source/indy_race_20s.mov \
  --use-case vod \
  --protocol hls_fmp4 \
  --priority 4k_hdr_quality \
  --report-name indy_race_20s
```

This writes:

```text
reports/indy_race_20s.json
reports/indy_race_20s.md
```

That JSON report can be passed to the encoder, validator, or experiment planner:

```bash
./eqlab experiment \
  --input source/indy_race_20s.mov \
  --profile reports/indy_race_20s.json
```

## Experiment Phase

The experiment phase can be run through `./eqlab experiment`:

```bash
./eqlab experiment \
  --input source/indy_race_20s.mov \
  --profile reports/indy_race_20s.json \
  --matrix config/experiment_matrix.yaml \
  --metrics vmaf ssimwave \
  --output-dir experiments/test_001
```

For now, the experiment phase is a dry-run planner scaffold. The intended flow is:

1. Load an advisor JSON profile and experiment matrix
2. Validate encode candidates from the matrix
3. Generate candidate FFmpeg commands
4. Encode candidate outputs
5. Run objective metrics such as VMAF and SSIMWave against the source
6. Rank candidates by quality, bitrate, file size, and encode speed
7. Write JSON, Markdown, and later CSV/HTML reports

The current dry-run planner already loads the profile and matrix, validates the three starter candidates, and prints FFmpeg commands. It does not execute encodes yet.

Run encodes:

```bash
./eqlab experiment \
  --input source/indy_race_20s.mov \
  --profile reports/indy_race_20s.json \
  --matrix config/experiment_matrix.yaml \
  --metrics vmaf ssim \
  --output-dir experiments/test_001 \
  --run
```

Run metrics against existing encodes without re-encoding:

```bash
./eqlab experiment \
  --input source/indy_race_20s.mov \
  --profile reports/indy_race_20s.json \
  --matrix config/experiment_matrix.yaml \
  --metrics vmaf ssim ssimwave \
  --output-dir experiments/test_001 \
  --metrics-only
```

Metric support in this phase:

- `vmaf`: uses FFmpeg `libvmaf` when available
- `ssim`: uses FFmpeg `ssim`
- `psnr`: uses FFmpeg `psnr`

`./eqlab` is the local umbrella CLI. The phase scripts and `main.py` remain available for development and compatibility, but README examples use `./eqlab`.

## Encoder Phase

The encode phase executes the advisor profile ladder. It is separate from experiment planning: the encoder creates the recommended renditions, while the experiment runner tests alternative candidates.

Encoder rate-control behavior is driven by:

```text
config/encoder_profiles.yaml
```

List available encoder profiles:

```bash
./eqlab encode --list-encoder-profiles
```

By default, the encode phase auto-selects a profile from the advisor recommendation. You can also force one:

```bash
./eqlab encode \
  --input source/indy_race_20s.mov \
  --profile reports/indy_race_20s.json \
  --output-dir encodes/indy_race_20s_crf_quality \
  --encoder-profile crf_quality
```

Current encoder profiles:

| Profile | Intended use |
| --- | --- |
| `crf_capped` | Default VOD/OTT CRF encode with ABR caps |
| `crf_quality` | Higher-quality CRF encode with ABR caps |
| `live_capped_vbr` | Live-safe capped VBR with faster presets |
| `cbr_live` | CBR-style live/broadcast encode |
| `mobile_capped` | Conservative mobile ABR with controlled peaks |
| `low_latency` | Low-latency encode with shorter VBV |
| `file_size` | Smaller-file VOD encode |
| `uhd_hdr_quality` | Higher-quality UHD/HDR encode |

Dry run:

```bash
./eqlab encode \
  --input source/indy_race_20s.mov \
  --profile reports/indy_race_20s.json \
  --output-dir encodes/indy_race_20s_crf_capped
```

Run encodes:

```bash
./eqlab encode \
  --input source/indy_race_20s.mov \
  --profile reports/indy_race_20s.json \
  --output-dir encodes/indy_race_20s_crf_capped \
  --run
```

For now, encoder outputs MP4 rendition files plus:

```text
encodes/<name>/encode_plan.json
encodes/<name>/encode_results.json
encodes/<name>/logs/
encodes/<name>/renditions/
```

## Validation Phase

The validate phase compares the expected encoded ladder renditions against the original source. It reads the advisor profile, derives the expected filenames from the ladder, and validates those exact files from the encoded renditions directory.

Run validation:

```bash
./eqlab validate \
  --source source/indy_race_20s.mov \
  --profile reports/indy_race_20s.json \
  --encoded-dir encodes/indy_race_20s_crf_capped/renditions \
  --output-dir validation/indy_race_20s_crf_capped
```

Current validation checks:

- Source integrity classification: `PASS`, `WARN`, or `POOR`
- Source summary in reports: container, codec, resolution, frame rate, bitrate, duration, scan type, pixel format, color, bit depth, and audio tracks
- Source decode scan for corrupt frames, concealment, packet corruption, timestamp discontinuities, and related warnings
- Captured/distribution-source risk notes for MPEG-TS and MPEG-2 references
- Source/encoded duration and frame-rate prechecks
- Expected rendition resolution matching
- Native VMAF using FFmpeg `libvmaf`
- PSNR using FFmpeg `psnr`
- SSIM using FFmpeg `ssim`
- Metric context fields: reference, mode, scale method, reference resolution, distorted resolution, and metric-space resolution
- VMAF percentile context: P1, P5, low-frame counts below 70 and 80
- Bitrate accuracy: target bitrate, actual bitrate, delta percent, and bitrate status
- Worst-frame thumbnail extraction
- VMAF-over-time SVG charts
- PASS/WARN/FAIL quality classification

Validation output:

```text
validation/<name>/validation_report.json
validation/<name>/validation_summary.csv
validation/<name>/validation_report.md
validation/<name>/validation_report.html
validation/<name>/metrics/
validation/<name>/charts/
validation/<name>/thumbnails/
validation/<name>/logs/
```

Validation thresholds and source-integrity policy live in:

```text
config/validation_rules.yaml
```

Use a different policy file when you want stricter mezzanine QC, looser captured-source review, or workflow-specific thresholds:

```bash
./eqlab validate \
  --source source/indy_race_20s.mov \
  --profile reports/indy_race_20s.json \
  --encoded-dir encodes/indy_race_20s_crf_capped/renditions \
  --output-dir validation/indy_race_20s_crf_capped \
  --rules config/validation_rules.yaml
```

The validation phase is where pass/fail belongs. The experiment phase can stay focused on exploring encoder settings and ranking tradeoffs; validation answers whether a final encoded ladder meets the current quality and precheck rules.

When source integrity is `PASS`, strict VMAF thresholds are reasonable. When source integrity is `WARN` or `POOR`, the validator treats raw minimum VMAF as diagnostic instead of automatically failing the encode. This is important for captured MPEG-TS, MPEG-2, UDP/SRT dumps, and other sources that may already contain compression artifacts, corrupt frames, timestamp offsets, or decode concealment.

### Metric Context

Current validation uses source-referenced native rendition metrics:

```text
metric_reference: original_source
metric_mode: native
scale_method: source_to_rendition
```

That means each encoded rendition is compared against the original source after the source is scaled to the rendition resolution:

```text
encoded 1280x720 rendition
vs
original source scaled to 1280x720
```

This is different from comparing an encoded rendition directly against another encoded rendition. It is also different from an upscaled/display-space test where the encoded rendition is scaled back up to a target display resolution before scoring. Upscaled mode is planned for a later validation option.

### Reading VMAF Columns

The validator reports more than one VMAF number because a single average or single worst frame can be misleading.

| Column | Plain meaning | Why it matters |
| --- | --- | --- |
| `Mean VMAF` | Overall average quality | Good for the big picture, but can hide short bad sections |
| `Min VMAF` | The single worst frame | Useful for finding defects, but can overreact to one corrupt or unusual frame |
| `P1 VMAF` | The quality threshold at the edge of the worst 1% of frames | Shows whether the worst few frames are isolated or part of a wider problem |
| `P5 VMAF` | The quality threshold at the edge of the worst 5% of frames | Shows whether a noticeable chunk of the video is struggling |
| `Frames <70` | Count of frames below a critical threshold | Helps quantify severe quality drops |
| `Frames <80` | Count of frames below a warning threshold | Helps quantify concerning quality drops |

Think of all frames sorted from worst to best:

```text
worst ------------------------------------------------ best
  |        |                         |
 min      P1                        P5
```

In practical terms:

```text
P1 means 99% of frames scored better than this.
P5 means 95% of frames scored better than this.
```

Example of an isolated bad-frame problem:

```text
Mean VMAF: 92
Min VMAF: 64
P1 VMAF: 81
P5 VMAF: 85
Frames <70: 2
Frames <80: 21
```

Interpretation:

```text
The overall video is good.
One or a few frames dipped badly.
Most of the video is still solid.
Review the worst-frame thumbnails before calling the encode bad.
```

Example of broader quality trouble:

```text
Mean VMAF: 89
Min VMAF: 31
P1 VMAF: 52
P5 VMAF: 73
Frames <70: 108
Frames <80: 254
```

Interpretation:

```text
The worst frame is very poor.
The worst 1% and 5% are also low.
Many frames are below warning or critical thresholds.
This is more likely to be a real encode-quality problem.
```

Use the columns together:

```text
Mean tells you the overall quality.
Min finds the worst single frame.
P1/P5 tell you whether the bad frames are isolated or widespread.
Frames <70/<80 tell you how many frames crossed fixed quality thresholds.
```


## Comparison Phase

The compare phase reads completed validation reports. It does not rerun VMAF, SSIM, PSNR, thumbnails, or charts. The intended workflow is:

```text
advisor profile -> encode run A -> validate run A
advisor profile -> encode run B -> validate run B
validation reports -> compare
```

This keeps the expensive measurement work in validation and makes comparison fast and repeatable.

Example:

```bash
./eqlab compare \
  --baseline validation/indy_race_20s_crf_capped/validation_report.json \
  --candidate validation/indy_race_20s_crf_quality/validation_report.json \
  --candidate validation/indy_race_20s_file_size/validation_report.json \
  --candidate-name crf_quality \
  --candidate-name file_size \
  --output-dir comparisons/indy_race_20s
```

Comparison output:

```text
comparisons/<name>/comparison_report.json
comparisons/<name>/comparison_summary.csv
comparisons/<name>/comparison_runs.csv
comparisons/<name>/comparison_report.md
comparisons/<name>/comparison_report.html
```

The comparison report includes two complementary views:

- Baseline source summary plus source consistency warnings across compared validation reports
- Run metrics matrix: one row per run/rendition, similar to a batch report
- Candidate deltas: candidate-vs-baseline bitrate savings, VMAF mean deltas, P1/P5 deltas, SSIM deltas, and notes

The metric values themselves are still source-referenced validation scores. In other words:

```text
baseline encode vs original source
candidate encode vs original source
then compare those measured scores
```

The comparison phase does not calculate direct candidate-vs-baseline VMAF.

It also includes simple best-run picks for quality and efficiency. A candidate with lower bitrate and similar VMAF is a good efficiency win. A candidate with higher VMAF but higher bitrate may still be useful when quality is the priority.


## Recommendation Sources

Every major recommendation includes provenance in the JSON report under `recommendation.decisions`. This keeps the advisor explainable as the rule set grows.

Example decision:

```json
{
  "name": "video_codec",
  "value": "h264",
  "reason": "H.264 High is the safest first choice for broad HLS/DASH device compatibility.",
  "basis": "advisor_policy",
  "rule_id": "codec.compatibility.h264.high",
  "confidence": "high"
}
```

Current basis types include:

- `user_input`: direct CLI input, such as protocol or segment duration
- `source_probe`: observed source properties from ffprobe
- `calculation`: values derived from inputs or source properties, such as frame rate based keyframe interval
- `advisor_policy`: built-in advisor logic and workflow heuristics
- `preset`: built-in bitrate ladder presets

## Configurable Advisor Policy

Most recommendation policy lives in YAML config rather than being hard-coded into Python:

- `config/advisor_rules.yaml`: codec, audio, rate-control, packaging, GOP, and ladder-selection policy
- `config/ladder_presets.yaml`: bitrate ladder presets
- `config/device_profiles.yaml`: starter device capability notes

The Python modules still do the procedural work:

- Load and match policy rules
- Probe source media with `ffprobe`
- Calculate keyframe intervals from source frame rate and GOP duration
- Filter ladders to avoid renditions above the source resolution
- Generate reports and validation warnings

This keeps the relationship clean:

```text
YAML config defines policy.
Python applies policy, performs calculations, and reports the result.
```

For example, this rule in `config/advisor_rules.yaml` explains why H.264 High is selected for compatibility-first workflows:

```yaml
- id: codec.compatibility.h264.high
  priority: compatibility
  recommend:
    video_codec: h264
    profile: high
  reason: H.264 High is the safest first choice for broad HLS/DASH device compatibility.
  confidence: high
```

And this rule explains 1080p HEVC bandwidth-saving workflows:

```yaml
- id: codec.bandwidth_savings.hevc.modern_devices
  priority: bandwidth_savings
  none_device: [legacy_stb]
  recommend:
    video_codec: hevc
    profile: main
  reason: HEVC is recommended for bandwidth savings when legacy playback is not required.
  confidence: medium
```

## Design Notes

The tool keeps each decision area in its own module:

- `recommendation.py`: shared decision/provenance shape
- `config_loader.py`: YAML configuration loading
- `rule_matcher.py`: simple rule matching for advisor policy
- `probe_source.py`: source inspection using `ffprobe`
- `recommend_codec.py`: codec, profile, audio codec, and rate control choices
- `recommend_gop.py`: GOP duration, keyframe interval, and segment compatibility
- `recommend_ladder.py`: starter ABR ladder generation and complexity adjustment
- `recommend_packaging.py`: packaging and segment strategy
- `validate_profile.py`: first-pass warnings and compatibility checks
- `report_generator.py`: JSON and Markdown output

The goal is to make the recommendations transparent, explainable, and easy to extend into deeper validation workflows.
