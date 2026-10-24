# SYSTEM-CLOSURE-02

**SYSTEM_CLOSURE_02_PARTIAL_WITH_NAMED_SOFTWARE_GAPS**

Operation key: `MULTI-AI-V2-SYSTEM-CLOSURE-02`.

Реализован новый source-only путь: Brief + разрешённые sources/media → bounded UTF-8 extraction →
queued Canon/outline/Scenes/mappings → прежний canonical gate → atomic production expansion →
существующие handlers → отдельный внедрённый director → один edit-only fix → portable package.
Положительный E2E не получает готовый ApprovedInputs bundle. Python не заменяет Story/Visual.
Синтетические ответы поступают только через transport boundary; native IO перехвачен.
Это software proof, а не новое видео, художественная экспертиза или live model proof.

## Frozen version and results

- Implementation base: 16edb1d4f05d981ca35bac179d072f683a779168; audit commit preserved.
- Code-tested SHA: `010d30f1e0a5277c7e1cd3c854c136e5c549edc1`.
- Code-tested tree: `f757615defb58a18828c797b3971c09f1c1930cf`.
- Canonical result: **passed**, 623 tests; skipped 1.
- Test outcomes: `{"passed": 622, "skipped": 1}`. Existing skip: installed local Piper native fitting test; no new skips.
- All previous596 exact IDs retained in the canonical selection, including the declared existing skip; TEST_INVENTORY.json provides the disjoint inventory.
- Source unchanged during checks. Local dirty state contains the four historical untracked evidence directories;
  tracked/index were clean. A clean GitHub checkout is still required for independent CI.
- No real model/API/TTS/render/Drive/runner operation. All jobs/SQLite/WAV bytes belong to disposable offline fixtures.
- Final publication SHA is identified in the final chat/commit containing this report, avoiding a self-referential hash.

| Group | Tests | Skips | Seconds | Result |
|---|---:|---:|---:|---|
| v2 | 111 | 0 | 276.157 | passed |
| contracts | 174 | 0 | 170.61 | passed |
| queue | 50 | 0 | 10.141 | passed |
| adapters | 16 | 0 | 0.343 | passed |
| mai05 | 108 | 0 | 27.938 | passed |
| panel | 12 | 0 | 2.703 | passed |
| legacy | 127 | 1 | 9.984 | passed |
| acquisition | 13 | 0 | 91.75 | passed |
| closure | 12 | 0 | 102.281 | passed |
| compileall | None | 0 | 0.969 | passed |
| check_git_export | None | 0 | 3.078 | passed |
| diff_check | None | 0 | 0.062 | passed |

Первый canonical-прогон сохранён: checker имел прежний строгий список групп; два
legacy offline теста выявили свободные assumption notes, а два launcher mock теста —
старую сигнатуру host injection. Legacy notes сохранены непроверенными, новый acquisition
остаётся строгим. Exact group/budget assertions и mock signatures обновлены; тесты не отключены.
Bytecode prefix теперь задаётся только compileall, сохраняя внешние outputs и обычный import lookup тестов.
Повтор разрешён этими конкретными исправлениями. Второй полный прогон сохранён отдельно.
После него исправлена воспроизведённая ошибка source verifier: он отвергал owner-provided .venv-core.
Теперь исходники проверяются полностью, а exact profile dependencies и fixed installation roots
объявлены отдельно. Они не включены в ZIP и не подтверждаются проверкой целостности исходников.
Последний полный прогон относится к этому исправленному code SHA.
C0 ограничился двумя диагностическими reproductions до stabilization; дальнейшие focused проверки относятся к implementation.
До изменений recovery воспроизвёл ACTIVE_OPERATION, QA принял NaN; failing evidence сохранены.

## B01–B11 / caller → handler → receipt → test

| Item | Result | Implementation and evidence |
|---|---|---|
| B01 | diagnostics implemented; hosted cause UNPROVEN | timed_unittest records actual child test IDs/phases/deadline stacks; checker tests retain honest exit124. No durability/timeout weakening. |
| B02 | offline verified | extract_sources verifies allowlist/rights/hash, bounded read, raw UTF-8 byte passages, BOM/CRLF/Unicode; ExtractionTests. |
| B03 | offline verified | CanonBible, typed StoryOutline, SegmentVisuals wrapper registered in parser/queue/gate/export; CanonicalOutputTests. |
| B04 | offline verified with revision limits | launch→SystemPipeline→accepted_dependency→derive_expansion→enqueue_batch; AcquisitionPipelineTests + ClosureIntegrationTests. |
| B05 | partial SG01 | injected existing adapter interface→structured EditPlan→canonical gate→director_apply→existing compiler; one edit Review fix; Story/Visual automatic fixes remain NEEDS_REVIEW. |
| B06 | partial SG02 | synthesize→existing requests binding→guarded_resume→actual JobManager.resume→durable intent; recovery matrix including async retry and crash hold. |
| B07 | offline verified | ordinary launch→system_main→DriveHostContext→exact resolver→GoogleDriveHTTP with fake request IO; no live uploaded proof. |
| B08 | relocated verified | explicit source inventory, unchanged synthesize/render scripts and app.worker; isolated -I child exercises actual argv/paths and missing/tamper rejection. |
| B09 | handler coverage verified | actual measured_edit/MeasuredEngine.qa; tiny controlled WAV, ffprobe/decode boundary IO; NaN, stream/format/timing/black/silent/subtitle/tool errors. |
| B10 | offline verified | edition-qualified resolver; ambiguous plain refs rejected; host citation sidecar reaches portable acquisition_lineage. |
| B11 | documented partial readiness | START_HERE/SYSTEM_RUNBOOK/SYSTEM_READINESS/provider docs and SYSTEM_CLOSURE_02 updated. Autonomous false. |

ACCEPTANCE_MATRIX.json records A01–A14 exact IDs, commands, measured durations, log hashes and simulation boundaries.
A05/A07/A08 are PARTIAL for the stated limits; all other PASS entries refer to software/offline scope.
Portable acquisition_lineage binds Brief, source manifest, byte passages, citation source SHA/edition and accepted expansion.
Receipt-before-completion recovery reuses the same director attempt with no second delegate call.
Unchanged second Scene remains byte-identical after the addressed edit. No human/full-video review is fabricated.

## Remaining software work

- **SG01 NOT_IMPLEMENTED**: Narration/Story and Visual-selection automatic targeted fixes. Only one edit-only Codex cycle executes. Other issue scopes route to NEEDS_REVIEW; no shell fallback.
- **SG02 BLOCKED_SCOPE**: Strong birth identity of the exact child spawned by protected JobManager. The core does not expose a spawn receipt. Caller records HOST_OBSERVED_WORKER and holds unknown/historical ownership. Minimal protected proposal is supplied, not applied.
- **SG03 UNVERIFIED_ACCEPTANCE**: Complete source-only acceptance/publication crash and source/Scene revision invalidation matrix. Same-version tampering, rollback inside accepted expansion and director receipt-before-completion replay are covered. Before-Canon-acceptance and after-expansion-publication crash injection, automatic narration/media replanning and all cross-version crash cases are not proven.


SG02 minimal protected-core proposal and regression requirements are in NEEDS_CHATGPT_CHANGE.md; no protected file was edited.
C0 hosted failure root cause remains unproven until fresh hosted timing. Local measurement does not explain the old CI failure.
Protected policy validator was not bypassed: local PyYAML is absent, and current feature/CI schema sync is intentionally pending.
Independent exact-SHA CI is NOT_RUN for this publication; old success/failure is not reused as current proof.

## Normal entry and delivery

Owner input no longer needs written Canon, outline, narration Scenes or mappings for source-only acquisition.
It still supplies Brief, exact owned sources/media manifest, profile hash, roots, operations, routes and explicit policy.
Source-only offline uses a trusted in-process HostServices injection; JSON cannot import a client/module or OAuth file.
Missing transport/director binding blocks explicitly. Autonomous live remains BLOCKED_ELIGIBILITY.

```powershell
studio-v2.cmd --config C:/AI_VIDEO_02_inputs/host.json plan
studio-v2.cmd --config C:/AI_VIDEO_02_inputs/host.json doctor
studio-v2.cmd --config C:/AI_VIDEO_02_inputs/host.json --execute-system --authorize start
studio-v2.cmd --config C:/AI_VIDEO_02_inputs/host.json --execute-system resume
studio-v2.cmd --config C:/AI_VIDEO_02_inputs/host.json --execute-system inspect-artifacts
studio-v2.cmd --config C:/AI_VIDEO_02_inputs/host.json --execute-system deliver
```

These are future separately authorized operator commands, not evidence of production execution now.
The existing approved_input path remains compatible. Trusted embedding uses the same launch.main(argv, host_services=...).
No binding → WAITING_AUTH for delivery. The offline integration tests are reproducible via ordinary unittest, with native IO stubbed.
Source deployment and exact models/piper Ruslan placement: docs/SYSTEM_CLOSURE_02.md.

## Immutable evidence and source delivery

- External evidence manifest SHA-256: `a47d9e738c976b7c0e23dbe12a4106bc6489bfd53559ff39e556806ef0476fad`.
- Canonical manifest SHA-256: `455560a38b77e43277c64985bfed661b6300ae46a2cf691ebcee7b7adb6ea329`.
- Source ZIP: `AI_VIDEO_02_SYSTEM_CLOSURE_02_sources_010d30f.zip`, 390894 bytes.
- ZIP SHA-256: `e82952eeb35c6b985ea8b22a09fb817822d2746536d1d2bbf7d8d1de60d29c9d`.
- Source inventory SHA-256: `e266e6516c836f25f1e9f29d1e1e39f09e22b3de94cc226a375fea213f88e9cc`.
- New output bytes at seal: 21027294; free bytes: 85711065088; reserve4GiB preserved.
- EVIDENCE_SUMMARY.json and SOURCE_CLOSURE.json make counts, timings and shipped hashes available without C: access.
  Large logs/stacks/ZIP stay external. The final chat links their actual local paths.

No main/protected CI/production-core/profile/accounts/studio/media/production SQLite changes.
No reset/rebase/cherry-pick/merge/force-push; audit and historical untracked evidence preserved.
OWNER_ACTIONS.md is the single next-action list. No PR9/PR6 merge was performed.

## Implementation commits

```text
010d30f1e0a5277c7e1cd3c854c136e5c549edc1 fix(package): fail closed on directory enumeration errors
e50ae6ad119ab1517175da80858ff3e0bc1c8d0e fix(package): separate source integrity from declared local dependencies
773586e01a362e0f19e6b04c1b6ac4a6ebba58cc Preserve legacy assumption notes and synchronize strict checker coverage
ff759d53abe7da00906f889a268fcb4fae28764e Harden receipt recovery, accepted director scope and source lineage
fa9f4f09b59c9646af3186ebabcd58128071b608 Hold measured duration outside the explicit Brief tolerance
9c05b7935860b0063001ee8d6dcb4f35a81e53be Close source ZIP dependencies and document offline launch boundaries
62e381c604a9213819a843ee541330eff3b15a24 Connect guarded director, scoped edit fixes and existing job recovery
078e7daaf8c685c64432cc5ea1558f2496d8ee1e Execute source-only acquisition and atomically expand the authorized graph
cd53e9e1c7ecd79c4400cac81897448b6e39c828 acquisition: extract bounded source passages and register canonical outputs
a37749c6b62701709f5201d277af256e60ad8460 diagnostics: record bounded per-test phases and child timeout stacks
```
