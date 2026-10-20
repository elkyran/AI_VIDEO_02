# Several visual shots per narration block

`ProductionBlock.visual_shots` is optional. Existing scripts with a single
`visual_asset_id` still work. Each measured narration WAV stays intact on A1;
the shots become consecutive entries on the Shotcut V1 track. The portable MLT
is the project used for export.

```json
{
  "id": "reunion",
  "text": "The narration for this block.",
  "title": "The reunion",
  "interpretation": false,
  "source_asset_ids": ["selected_source_notes"],
  "visual_shots": [
    {"asset_id": "anime_wide", "weight": 1.0, "label": "Location", "motion": "zoom_in"},
    {"asset_id": "anime_close", "weight": 1.0, "label": "Reaction", "motion": "pan_left", "focal_x": 0.65},
    {"asset_id": "manga_panel", "weight": 1.0, "label": "Comparison", "fit": "contain", "motion": "static"}
  ]
}
```

Weights allocate the measured WAV length plus the normal 0.24-second block
pause. Integer frame allocation preserves the exact block duration; cuts do
not divide or repeat narration. Plan approximately one shot per 3–6 seconds,
then check the resulting `visual_pacing` measurements. The builder reports
cadence without declaring that fast cuts alone establish editorial quality.

Shot options:

| Field | Values / default |
| --- | --- |
| `asset_id` | Registered image or video identifier; required |
| `weight` | Positive finite relative weight; `1.0` |
| `label` | Editor caption describing the purpose; empty |
| `motion` | `auto`, `static`, `zoom_in`, `zoom_out`, `pan_left`, `pan_right`; `auto` |
| `fit` | `cover` or `contain`; `cover` |
| `focal_x`, `focal_y` | Crop/focus location from `0.0` to `1.0`; `0.5` |
| `in_ms` | Video source start in milliseconds; `0` |

Camera motion is applied to still images. Video shots preserve their source
motion and use the specified crop or fit. `contain` with `static` keeps an
entire portrait manga panel visible. All modes preserve image proportions.
Original source clips must cover the selected range; the builder does not
loop short clips to conceal missing footage.

`ProductionScript.title_style = "lower_third"` uses a 36-pixel lower caption
at 1080p. The default `legacy` preserves the existing title layout. Empty block
titles show no title text. `heading` and `footer` remain optional.

## Private source review

Unknown visual rights remain `unknown` in the registry. They are accepted for
a local review only when the script explicitly sets `local_review: true` and
lists a source-page URL and attribution for every such visual:

```json
{
  "local_review": true,
  "review_sources": [
    {
      "asset_id": "anime_wide",
      "source_url": "https://example.org/official/episode-page",
      "attribution": "Original credited rights holder; episode reference"
    }
  ]
}
```

This does not establish permission or assert a legal exception. The build
and package contain `review_sources.json`, credit records, and the explicit
restriction to local private review without publication or distribution.
Unknown assets remain blocked when the switch or source record is missing.

The separate `editorial_review` milestone accepts a selected-source project
with `source_status` of `partial` or `analyzed`, requires `local_review`, and
has a minimum actual duration of 45 seconds. Factual blocks still require
registered source notes. QA states that selected-source review does not
complete whole-book analysis or complete edition reconciliation. The
`full_episode` coverage, claims, and minimum-duration gates remain in force.

## Runtime and validation

`runtime.json` may set `video_encoder` to `h264_nvenc` after an actual local
encoder test. The default remains `libx264`; unsupported values fail rather
than falling back silently. Image preparation caches include the encoder.
Both CPU and GPU exports consume the same MLT timeline.

`render_threads` defaults to `1` and accepts integers from `1` to `16`.
An explicit value of `4` uses MLT `real_time=-4`, without enabling frame
dropping. Four exports of the same ten-second production fragment retained
all 300 frames and identical decoded video and audio. Warm runs improved
by roughly 8–14% in this local measurement; speedup depends on the project
and machine load, and the cold first run must not be used to promise 2×.

ASR has separate explicit settings: `asr_device` is `cpu` by default or
`cuda`; `asr_compute_type` defaults to `int8`; `asr_dll_directories` defaults
to `[]` and may list existing absolute DLL directories on Windows. The
production preflight was tested with CUDA and `int8_float16`. The worker
verifies its actual device and compute type, retains DLL handles throughout
inference, and neither downloads models nor silently falls back to CPU.
These settings participate in the speech-cache identity. Setup details
and supported values are in [INSTALL.md](INSTALL.md).

Speech review can start from a completed build while video rendering continues:

```powershell
.venv-core\Scripts\python.exe scripts/preflight_speech.py --workspace workspace/studio --build-job-id COMPLETED_BUILD_JOB_ID
```

The final QA may reuse that result only after verifying the manifest, text,
timing, terms, WAV hashes, actual Whisper model files, runtime and worker
identity, plus the report and both subtitle files. Warnings remain intact;
an OS lock coordinates overlapping requests. This does not skip video QA or
establish that a particular production run had a cache hit. Actual run and
test evidence are recorded in [current_state.md](current_state.md) and
[TESTING.md](TESTING.md).

QA extracts a frame from every shot and checks all referenced media. The
editable package includes every prepared shot, original registered images,
one WAV per narration block, source references, and hashes. Automated QA
still leaves listening, editorial relevance, and user acceptance for review.
The portable `speech_qa.json` omits local DLL directories; the original
ASR report and its hash remain preserved as evidence. Removing machine
paths from the package copy does not remove recognition warnings.

Relevant checks: `tests.test_multishot`, `tests.test_media`,
`tests.test_timeline`, and `tests.test_jobs`. The new integration tests render
real cuts through MLT, verify their frame colors and exact duration, compare
portable rerenders, and build/package a measured 45-second narration with ten
shots while retaining unknown rights and partial source status.
