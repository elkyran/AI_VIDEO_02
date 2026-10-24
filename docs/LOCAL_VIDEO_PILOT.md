# PILOT-01: explicit local technical pilot

This entrypoint is limited to the owner's original three-scene technical story
and its one approved revision. It does not enable ordinary v2 production,
providers, accounts, runners, uploads or automatic creative acceptance.

Use installed `.venv-core`, `.venv-tts` Piper 1.8.0, the approved Ruslan model,
FFmpeg/ffprobe on PATH and the existing Shotcut `tools/.../melt.exe`.
Nothing is downloaded. Voice settings and model/config hashes are checked against
`config/production_profile.json`; installed models remain read-only external
references. No runtime or Registry from the user's working studio is copied.

Choose a new unique root outside repo, production and cloud sync:

```powershell
$pilotRoot = "$env:LOCALAPPDATA/AI_VIDEO_02/pilots/pilot_01_UNIQUE"
.venv-core/Scripts/python.exe -X utf8 -m app.v2_local_pilot init --root $pilotRoot --run-id pilot_01_UNIQUE --execute-local-pilot
.venv-core/Scripts/python.exe -X utf8 -m app.v2_local_pilot smoke --root $pilotRoot --execute-local-pilot
.venv-core/Scripts/python.exe -X utf8 -m app.v2_local_pilot prepare --root $pilotRoot --execute-local-pilot
.venv-core/Scripts/python.exe -X utf8 -m app.v2_local_pilot fix --root $pilotRoot --execute-local-pilot
.venv-core/Scripts/python.exe -X utf8 -m app.v2_local_pilot replay --root $pilotRoot --execute-local-pilot
.venv-core/Scripts/python.exe -X utf8 -m app.v2_local_pilot deliver --root $pilotRoot --execute-local-pilot
```

Windows app filesystem virtualization may canonicalize LOCALAPPDATA to the
app's LocalCache directory. `init` reports the physical resolved root; retain it
in the report. Reparse points, repo overlap, cloud paths, existing init roots,
more than 1 GiB persistent outputs and less than 4 GiB reserve are rejected.
The write probe creates only a small file in the new root.

`authorization.json` pins the exact root/run, input bundle, full profile hash and
five allowed operations. Every invocation needs explicit opt-in. The host's
fixed original/manual-assisted Scenes are admitted by the existing Coordinator,
V2ResultGate, immutable completion and CAS with a non-simulated local adapter.
`admit` can exercise that input-only boundary without synthesis. No fake result
is relabelled, no provider identity is invented and no second queue or acceptance
table is added. Gate checks also apply to restart/replay.

`coordinator/` uses RunCoordinator's existing Registry; `engine/` uses the
existing production Registry and JobManager, matching the existing bridge layout.
Separate Piper WAV are produced only by completed `operations.synthesize` jobs.
Import checks the registered exact ProductionScript, job/run, manifest SHA,
individual request settings/cache keys, model/config/version, text, physical WAV
hash and measured duration. Synthetic or caller-asserted live voice bindings
remain blocked by the ordinary offline narration API.

Original graphics are drawn locally with Windows System.Drawing. Fonts are
rasterized into PNG; no fonts, third-party footage, music or models are packaged.
Existing EditPlan/ProductionBridge compile and render the actual MLT. Only
verified zero-valued trailing PCM is trimmed to a 100ms/three-frame grid.
The existing MLT renderer uses libx264, CRF 20, preset fast and four threads;
this explicit technical-pilot renderer differs from the profile's legacy NVENC
operation. The profile itself, Ruslan settings and 1920x1080/30 format are unchanged.

`prepare` renders three scenes and a 60–90s preview. `fix` changes only
`scene_002.voice_001`; the other WAV in that scene must be a cache hit. Scene 1/3
checkpoints, WAV and renders must stay byte-identical. Film reassembly reuses
the accepted scene MP4 picture with original separate narration tracks.
Existing outputs, interrupted builds and manual edits are preserved; corrupted
accepted bytes block replay. Stable keys return existing jobs/outputs, and new
input with an old key conflicts.

QA uses ffprobe, full FFmpeg decoding, blackdetect, silencedetect, volumedetect,
stream-start/timeline checks and the existing subtitle validator. These checks
do not prove phoneme-level lip sync or editorial quality. Silence/level logs
remain available. `editorial_review=PENDING_OWNER`,
`full_video_creative_qa=NOT_RUN`.

`deliver` preserves the MLT's relative media layout in `portable/`, copies it to
`portable_copy/`, verifies every referenced file/hash, decodes copied final.mp4
and executes the relocated MLT to a measured non-silent WAV. No SQLite/WAL,
models, fonts or logs are delivered. Cloud status remains `uploaded=false`,
`remote_sync=UNVERIFIED`.

Offline CI runs only negative/admission tests without Piper/render. Canonical
checker budgets are v2=420s, contracts=420s, other groups=180s. Genuine timeout
remains exit 124 / failed, including partial misleading OK output.
**NEEDS_CHATGPT_CHANGE:** the ChatGPT-owned CI evidence validator must expect
420 for v2/contracts and 180 otherwise, preserving all coverage/status/hash/skip
checks. Do not rerun GitHub CI repeatedly before policy synchronization/review.
