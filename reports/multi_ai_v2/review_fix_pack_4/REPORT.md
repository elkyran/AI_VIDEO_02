# REVIEW FIX PACK 4

Status: REVIEW_FIX_PACK_4_WAITING_FOR_INDEPENDENT_REVIEW

## Revision and scope

- Repository: https://github.com/elkyran/AI_VIDEO_02 (private).
- Branch: `feature/multi-ai-coordinator-v2`; no merge, force-push or change to main.
- Initial SHA: `c4e69d0556169cdc394c97b5f75560d00947df61` (matched local and fetched origin before edits).
- Final implementation SHA: `d44bcb4bdac74b6df004a180d19faa9d879bf19e`; all application and test changes are committed at this revision.
- The following documentation/report commit contains no implementation changes. Its exact SHA is supplied in the final delivery message and Git history; a report cannot include its own eventual commit hash.
- Security lane observed at `73fb69de60a8a0cb90f2b589ebe124786fc12302`; no cherry-pick or merge.
- Only the seven owner-allowed paths changed. Existing untracked Pack 2/3 evidence directories were preserved and excluded from staging.

## Changes

### A: canonical dependencies

Before fixture generation or adapter dispatch, validate cached SQLite payload against immutable completion payload and host metadata payload. Validate task/run/current-attempt identity, contract and attempt inputs, simulation provenance, accepted fingerprint and physical artifacts. Build dependency results from verified completion host payload. Reject mismatch with failed status and `dependency_rejected`; do not repair the cache silently. Recheck the same dependency binding at acceptance. The default Coordinator without a gate retains its original dependency path.

### B: request login generation

Live full-video acceptance requires the exact immutable `request.json.account_binding` object with a lowercase SHA-256 identity and positive integer auth generation. Derive fingerprint with SHA-256 of existing `canonical_json(binding)`. Require the delivery fingerprint to match; existing probe/delivery binding then requires the same probe fingerprint. Missing/malformed binding and null fingerprint fail closed. No current AccountManager lookup substitutes for the saved launch request. Only necessary host test fixtures changed.

### C: immutable delivery ledger

Initialize host-owned `ai_v2_deliveries(delivery_id PRIMARY KEY, content)` before Coordinator recovery. Canonical full delivery content is insert-only: exact replay is idempotent; any changed binding for the same ID is rejected. Bind only after all payload, evidence and replay checks. Completion savepoints roll back ledger and queue/artifact SQL together on gate, output-write, CAS or post-CAS failure, including automatic poll's caught-error path. Nested acceptance savepoint also protects persisted completion recovery. No historical rows are deleted or replaced; this is neither another queue nor a results table.

### D: malformed recovery

Validate persisted completion/request object shape before field access. Reject JSON array/null/string, missing host metadata, and non-object adapter recovery without crashing construction. Preserve evidence bytes and paths, accept no result/artifact/delivery, and record failed state plus `recovery_rejected` event. Adapter exceptions retain the existing reservation-preserving behavior.

## Changed files

- `app/multi_ai.py`
- `app/multi_ai_v2.py`
- `app/v2_acceptance.py`
- `app/v2_video.py`
- `tests/test_multi_ai_v2.py`
- `docs/VIDEO_REVIEW_PROTOCOL.md`
- `reports/multi_ai_v2/review_fix_pack_4/REPORT.md`

## Verification

Environment: existing `.venv-core/Scripts/python.exe`, no dependency updates. All tests use temporary data and fixtures/mocks. No real models, TTS, FFmpeg, render or Google Drive operations were started.

Required commands (run from repository root):

```powershell
.venv-core/Scripts/python.exe -X utf8 -m unittest discover -s tests -p test_multi_ai_v2.py -v
.venv-core/Scripts/python.exe -X utf8 scripts/check_multi_ai_v2.py --output C:\Users\diffe\.codex\visualizations\2026\10\20\01a158d2-a617-7862-8ff9-5159342bcc27\review_fix_pack_4\full
.venv-core/Scripts/python.exe -m compileall -q app scripts tests
git diff --check
git status --short
git diff --stat
```

- Required discovery: **81 passed, 0 failed, 0 skipped**.
- Full checker: **407 total, 406 passed, 0 failed, 1 skipped**; every group and service check passed.
- Expected local skip: `tests.test_fit_narration.RealPiperTests.test_native_real_wav_fit_preserves_spoken_request_and_source` — `Requires installed local Piper`. No test was disabled for this pack.
- Full groups: v2 81; contracts 14; queue 50; adapters 16; mai05 107; panel 12; legacy 127 (one skip).
- Compileall and whitespace checks passed, including explicit final compileall invocation.
- Read-only export scanner checks working candidates and reachable Git history; zero findings. Historical export reports are not rewritten.
- Covered paths include manual, poll, recovery, exact replay/restart, stale identity/input, simulation separation, expired/revoked evidence, changed artifacts and default legacy behavior.
- New negatives include SQLite-only dependency payload tamper before fixture/start; all request account variants; delivery-ID canonical field conflicts; rollback on output/CAS/post-CAS faults; ledger initialization before recovery; malformed persisted JSON and adapter return values.

### Iterations and immutable evidence

One preliminary FIX D run failed: invalid numeric fixture identifiers caused four subtest errors before recovery assertions. Fixed fixture IDs to valid `recovery_<n>` identifiers; the second run passed all 49 tests. This failed run remains immutable and is not counted as passed. No defect had two unsuccessful repair cycles. Subsequent mandatory discovery and full checker both passed.

Evidence root (outside repository):

`C:\Users\diffe\.codex\visualizations\2026\10\20\01a158d2-a617-7862-8ff9-5159342bcc27\review_fix_pack_4`

Every manifest and every listed file SHA-256/byte count was revalidated. Exact per-iteration commands and results follow; paths are relative to the external evidence root. The executable is abbreviated as `.venv-core/Scripts/python.exe` below; manifests retain its exact absolute path.

- Directory: `20261021T055516377741Z_ace4fbed2ac1463683f503e2582b15ed`
  Status: `passed`; tests: 40; skipped: 0.
  Manifest SHA-256: `272a00100f825416141c365769de33e88dc3ec15959a1037795bdb55bdf713f0`.
  Command: `.venv-core/Scripts/python.exe -X utf8 -m unittest -v tests.test_multi_ai_v2.Pack4Tests tests.test_multi_ai_v2.UnifiedAcceptanceTests.test_context_pack_uses_only_accepted_artifact_metadata_after_restart tests.test_multi_ai`; exit=0.
- Directory: `20261021T055641124832Z_d0dfbe86d8734acfb1ccbab00e342bb7`
  Status: `passed`; tests: 7; skipped: 0.
  Manifest SHA-256: `b77f0b0a14e2ac87ef3e75109e62425bab016df121662e856c7801df5cfa086f`.
  Command: `.venv-core/Scripts/python.exe -X utf8 -m unittest -v tests.test_multi_ai_v2.Pack4Tests tests.test_multi_ai_v2.UnifiedAcceptanceTests.test_valid_automatic_review_is_accepted_once_and_replays_after_restart tests.test_multi_ai_v2.UnifiedAcceptanceTests.test_manual_review_uses_identical_delivery_and_editorial_gate`; exit=0.
- Directory: `20261021T055911255945Z_05a8d529710a4acdadde71e776c7482f`
  Status: `passed`; tests: 10; skipped: 0.
  Manifest SHA-256: `a55ebc5b42405a8dcbcce6fd72b69bbfa584eb9840d6796f934f2f6202072b05`.
  Command: `.venv-core/Scripts/python.exe -X utf8 -m unittest -v tests.test_multi_ai_v2.Pack4Tests tests.test_multi_ai_v2.UnifiedAcceptanceTests.test_valid_automatic_review_is_accepted_once_and_replays_after_restart`; exit=0.
- Directory: `20261021T060119985539Z_75eeaeb4d3874198bcf6dabdad10bdbe`
  Status: `failed`; tests: 49; skipped: 0.
  Manifest SHA-256: `89ebb943ec588cf0073cc0f65d7dc791ac541b2e6c3edfbc030f5d26f5addf93`.
  Command: `.venv-core/Scripts/python.exe -X utf8 -m unittest -v tests.test_multi_ai_v2.Pack4Tests tests.test_multi_ai`; exit=1.
- Directory: `20261021T060223958984Z_7335d8eb0586483bbd620d51681baea5`
  Status: `passed`; tests: 49; skipped: 0.
  Manifest SHA-256: `2fcc9fefcd255bdcbd1316ff20987f1713660a05b9a7d27907e2cb5dc1879468`.
  Command: `.venv-core/Scripts/python.exe -X utf8 -m unittest -v tests.test_multi_ai_v2.Pack4Tests tests.test_multi_ai`; exit=0.
- Directory: `full/20261021T060718873490Z_122387c12632443e97f07be0ad86b0ce`
  Status: `passed`; tests: 407; skipped: 1.
  Manifest SHA-256: `2c3093fcda081bbab853e6866efd2666b2848ab87887edad6b33ad1722e115c2`.
  Command: `.venv-core/Scripts/python.exe -X utf8 -m unittest -v tests.test_multi_ai_v2`; exit=0.
  Command: `.venv-core/Scripts/python.exe -X utf8 -m unittest -v tests.test_editorial tests.test_production_bridge`; exit=0.
  Command: `.venv-core/Scripts/python.exe -X utf8 -m unittest -v tests.test_multi_ai tests.test_multi_ai_configuration`; exit=0.
  Command: `.venv-core/Scripts/python.exe -X utf8 -m unittest -v tests.test_ai_adapters`; exit=0.
  Command: `.venv-core/Scripts/python.exe -X utf8 -m unittest -v tests.test_ai_accounts tests.test_ai_cli_runtime tests.test_multi_ai_accounts_ui tests.test_ai_codex_diagnostics tests.test_gemini_free_route`; exit=0.
  Command: `.venv-core/Scripts/python.exe -X utf8 -m unittest -v tests.test_multi_ai_ui tests.test_multi_ai_ui_review tests.test_multi_ai_preview`; exit=0.
  Command: `.venv-core/Scripts/python.exe -X utf8 -m unittest -v tests.test_core tests.test_cli tests.test_library tests.test_exchange tests.test_production_profile tests.test_quality tests.test_asr tests.test_asr_runtime tests.test_speech_review tests.test_fit_narration tests.test_multishot.MultiShotContractTests tests.test_multishot.ReviewRightsTests tests.test_tts.TTSInputTests tests.test_tts.SileroContinuityTests tests.test_replace_narration.NarrationValidationTests tests.test_timeline.TimelineTests.test_traversal_and_preserving_existing_builds`; exit=0.
  Command: `.venv-core/Scripts/python.exe -m compileall -q app scripts tests`; exit=0.
  Command: `.venv-core/Scripts/python.exe -c "from scripts.check_multi_ai_v2 import audit_export; raise SystemExit(audit_export())"`; exit=0.
  Command: `git diff --check`; exit=0.
- Directory: `targeted/20261021T060512403945Z_fd453a4d52b74f6d81f87b19dc94c619`
  Status: `passed`; tests: 81; skipped: 0.
  Manifest SHA-256: `c4b1e342630d2bb67b9e9d3ebef7f0b0dd31367f4fca7d15fcab02b7c601889d`.
  Command: `.venv-core/Scripts/python.exe -X utf8 -m unittest discover -s tests -p test_multi_ai_v2.py -v`; exit=0.

Additional focused account/ledger tests passed (3 tests); these are also included in both final 81-test runs.

### Proof limits

Full checker source binding: `93f4fc4b83d03db187fa4743b5e024f3bbfb37eef777998a1e32f46f85ac7f2f`.
Schema binding: `3ad69d06f5470852a31d4fe03f462c5621d26aeb06987bd3d5b591e79a4a2e4e`.
`source_unchanged_during_checks=true`; current application/test hashes still match that manifest after the run. Git HEAD was the final implementation SHA above, with `dirty=true` due to protocol documentation and previously retained untracked evidence. This is local dirty evidence, **not an independent clean-final proof**. Report/docs commit and push occur after these runs; independent GitHub review remains pending.

## Boundaries and blockers

All five ChatGPT-owned files remain unchanged against the initial SHA:

- `.github/workflows/ci-multi-ai-v2.yml`
- `.github/workflows/new-video.yml`
- `scripts/ci_validate_multi_ai_v2.py`
- `docs/GITHUB_CI_AND_RUNNER.md`
- `development/multi_ai_v2/COLLABORATION.md`

Pydantic contracts, exported JSON schemas, production-engine files, current credentials, production SQLite, original media and models were not changed. No architect review was required because implementation stayed within allowed files and existing contracts. Development task journal was not modified.

Remaining implementation blockers: none found by the completed local checks. Independent review/CI acceptance remains outstanding; do not infer owner acceptance from this report.

`model_requests=0`, `paid_api_requests=0`, `production_started=false`.

Publish only the feature branch after final explicit-path staging, export scan, whitespace/status review. Do not merge PR #1. Stop for independent review.

Final pre-push review: read-only scanner checked 1,564 working candidates and
1,687 reachable history blobs, with zero findings; whitespace/status/allowlist
checks passed. External evidence directory:
`prepush/20261021T061238279558Z_3725a020fb80486a9df8c1c34ead0114`.
Manifest SHA-256:
`4c1dfa198797c1b2e33f9383c1e27e6b862e2ba27838bc0c7e46675b39958acb`.
