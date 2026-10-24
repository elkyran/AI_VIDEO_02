# PHASE A FINAL CI GATE

Status: PHASE_A_CI_FIX_WAITING_FOR_INDEPENDENT_REVIEW

## Revisions and commits

- Repository: https://github.com/elkyran/AI_VIDEO_02 (private).
- Branch: feature/multi-ai-coordinator-v2.
- Initial HEAD: `7cd820d7188a23d1c462c14f5a19534803b8cb72`, matched local and fetched origin before changes.
- Final tested implementation/documentation HEAD: `510cd168044e82c780900899eb775aebf9fb8b99`.
- Tested tree: `0303d6e7ce683dd56fc8176eb8f3776ee78b6f23`.
- Final delivery HEAD is the subsequent report-only commit, resolvable with
  `git log -1 --format=%H -- reports/multi_ai_v2/phase_a_ci_gate/REPORT.md`.
  Its exact SHA is supplied in the final delivery message; the report cannot
  embed its own future commit hash. No source changes follow the tested HEAD.

Completed thematic commits:

```text
5728deeb0f09fc56255a658ac8fa0b69894029f0 Make v2 CI timeout bounded and Windows-safe
510cd168044e82c780900899eb775aebf9fb8b99 Align architecture and runbook with owner-approved pipeline
```

Final report-only commit: `Document Phase A CI gate evidence`.

## Changes

1. Bounded per-group checker timeout: v2=420 seconds; all other test and service
   groups retain 180 seconds. The selected limit is written to checks.json.
   Timeout still records exit 124 and failed check/manifest; no skip or warning
   substitutes for failure. Four quick mock-based tests cover full group/command
   preservation, exact limits, v2/other-group TimeoutExpired, misleading partial
   success output, nonzero test failure, evidence status and hashes/binding.
   The four tests are part of the full v2 group; original modules remain intact.
2. One owner-approved target pipeline in architecture, capability matrix and
   plan. Coordinator remains the sole controller; existing AI_VIDEO_02 remains
   the production engine. One brief starts a run; GitHub holds code/metadata and
   small manifests; Drive is storage; Windows runner executes heavy work.
   Missing connections are NOT_IMPLEMENTED/PARTIAL/BLOCKED rather than completed.
3. Story and Visual are logical roles of one future official Antigravity
   integration. Antigravity remains AGE-GATED / DISABLED, with no login, smoke,
   auth migration, credential copying or bypass routes. No account rotation or
   paid API authorization is added. Runner registration remains prohibited.
4. Runbook describes the reviewed security-lane workflow: exact checkout with
   clean=true, credentialed main-only execution, fake-only, no production input.
   It explicitly distinguishes this from the older feature-branch template;
   workflow files are not changed or merged by this package.

All changed paths (explicit staging only):

- `scripts/check_multi_ai_v2.py`
- `tests/test_multi_ai_v2_checker.py`
- `docs/REMOTE_RUNBOOK.md`
- `docs/MULTI_AI_V2_ARCHITECTURE.md`
- `docs/PROVIDER_CAPABILITY_MATRIX.md`
- `development/multi_ai_v2/PLAN.md`
- `reports/multi_ai_v2/phase_a_ci_gate/REPORT.md`

## Exact verification and outcomes

Existing `.venv-core/Scripts/python.exe` used throughout. No global installations.

```powershell
.venv-core/Scripts/python.exe -X utf8 -m unittest -v tests.test_multi_ai_v2_checker
.venv-core/Scripts/python.exe -X utf8 scripts/check_multi_ai_v2.py --output C:\Users\diffe\.codex\visualizations\2026\10\20\01a158d2-a617-7862-8ff9-5159342bcc27\phase_a_ci_gate\full
.venv-core/Scripts/python.exe -m compileall -q app scripts tests
git diff --check
git status --short
git diff --stat
```

- Addressed mock-based tests: **4 passed, 0 failed, 0 skipped** (0.227s unittest).
- Full checker: **411 total, 410 passed, 0 failed, 1 skipped**. Original 407 are
  retained; four new checker tests are included, with no automatic slow-test skip.
- Expected local skip remains
  `tests.test_fit_narration.RealPiperTests.test_native_real_wav_fit_preserves_spoken_request_and_source`
  (`Requires installed local Piper`). No tests disabled.
- Negative timeout simulations pass by asserting failed nested evidence/exit 124;
  they do not create a failed actual validation run. All actual Phase A runs passed.
- Compileall, read-only working-files/history export scan and diff check passed
  inside the full checker. Exact nested command arrays and elapsed times are
  recorded in checks.json and manifest.json; full logs are retained externally.
- Full export scan: 1,565 working candidates, 1,699 reachable history blobs,
  zero findings; historical export reports were not overwritten.

| Group | Test count | Skipped | Status | Elapsed seconds | Timeout seconds |
|---|---:|---:|---|---:|---:|
| v2 | 85 | 0 | passed | 126.5 | 420 |
| contracts | 14 | 0 | passed | 1.093 | 180 |
| queue | 50 | 0 | passed | 13.219 | 180 |
| adapters | 16 | 0 | passed | 0.469 | 180 |
| mai05 | 107 | 0 | passed | 37.235 | 180 |
| panel | 12 | 0 | passed | 3.297 | 180 |
| legacy | 127 | 1 | passed | 12.5 | 180 |
| compileall | n/a | 0 | passed | 0.282 | 180 |
| check_git_export | n/a | 0 | passed | 3.484 | 180 |
| diff_check | n/a | 0 | passed | 0.078 | 180 |

## Thirty-minute Windows job budget

Reviewed workflow at security-lane SHA
`964bdd7b5a0e22ba081a8440031e29c8fc2fc048` keeps job timeout-minutes=30;
no workflow modification was needed or performed.

GitHub REST read-only metadata for
[run 39552603012](https://github.com/elkyran/AI_VIDEO_02/actions/runs/39552603012)
confirmed both historical attempts ended in failure. Attempt 1 took 462 seconds
and attempt 2 took 353 seconds for the whole offline-regression job. Given the
owner-confirmed v2 180-second timeouts, replacing only that exhausted allowance
with 420 gives estimated job durations 702s (11:42) and 593s (9:53), assuming
other step durations stay unchanged. These are budget estimates, not new CI passes.

Current local full-run group elapsed sum is 198.157s; checked_at-to-manifest span
is 198.575s (~3:19), below 1,800s. This supports the normal-run budget. It does
not guarantee that every future Windows run completes under 420s. Summing all
pathological group maxima gives 420+9*180=2,040s; the existing 30-minute job limit
can still stop an abnormal run with multiple hangs. Failure is not hidden.
A new independent Windows CI run remains required after lane integration/review.

## External immutable evidence

Evidence root, outside repository:

`C:\Users\diffe\.codex\visualizations\2026\10\20\01a158d2-a617-7862-8ff9-5159342bcc27\phase_a_ci_gate`

- `full/20261021T063918789362Z_a28e7f9cbfe44c4ebafc5e0302987fd9`
  Status: passed; manifest SHA-256: `0a9f680a0fe52f442b810d5c9d528fc70134d0ce328f605efd7d7eedb02c2040`.
- `historical_ci/20261021T064042053467Z_4ea7ca3107c74d2e808093a796f0f2b3`
  Status: passed; manifest SHA-256: `4920284f121944be41dbe499ffb433f2241550a5142dfb447bd59e7f4423fd5c`.
- `targeted/20261021T063702359221Z_60127d8f6251477a86f8330e2bd69ba6`
  Status: passed; manifest SHA-256: `a7963c35c06bbb007ca638698720bbcc1dcc4a9fe246a70423ccb13747cdab91`.

Historical CI evidence records successful read-only metadata retrieval and budget
calculation; its passed envelope does not reclassify the failed historical CI.
Every listed evidence file hash and byte count was verified against its manifest.

Full source binding: `7b7ccf36b987a43c2514cb0f37a21d90bd03385dcb38cee0c83ef00739650054`.
Schema binding: `3ad69d06f5470852a31d4fe03f462c5621d26aeb06987bd3d5b591e79a4a2e4e`.
`source_unchanged_during_checks=true`; current source/test hashes match the full
run. Evidence has `dirty=true` solely because known untracked Pack 2/3 local
evidence directories remain. Tracked source/docs were committed before the full
run. This local evidence is not an independent clean-final CI proof. The final
report-only commit does not change checker/application/test source binding.

## Boundaries, blockers and next action

All five ChatGPT-owned files are unchanged from initial HEAD:

- `.github/workflows/ci-multi-ai-v2.yml`
- `.github/workflows/new-video.yml`
- `scripts/ci_validate_multi_ai_v2.py`
- `docs/GITHUB_CI_AND_RUNNER.md`
- `development/multi_ai_v2/COLLABORATION.md`

Production-engine files, Pydantic contracts/schemas, STATUS.json and development
journal, production SQLite, credentials, original media, WAV/cache, models and
manual edits remain unchanged. No new subsystems, scheduler or queue were added.
No real models, TTS, FFmpeg, render, Drive operations, auth migration or runner
registration occurred. Existing untracked local evidence was preserved, unstaged.

`model_requests=0`, `paid_api_requests=0`, `production_started=false`.

Local implementation blockers: none. Remaining gate: independent Windows CI/review
of the integrated lanes. Feature-branch workflow remains the older template until
separate security-lane integration; credentialed execution and runner registration
are not authorized. No claim of production readiness or owner acceptance.

Final actions: review the allowed file list, scan candidates/history and whitespace,
commit only this report, push only feature/multi-ai-coordinator-v2, then STOP.
No merge of PR #1 or main; no force-push.

Final pre-push checks passed: explicit seven-path allowlist and tested source
binding, compileall, working/cached whitespace checks and read-only export scan
(1,566 candidates, 1,699 history blobs, zero findings). External evidence:
`prepush/20261021T064426746495Z_8070e5c95df249be84cee4b7fdfa63c7`;
manifest SHA-256:
`3d15a12f6c614b984dda36b000737bef3fb44f9607f9c95ad41f92fab22655e2`.
