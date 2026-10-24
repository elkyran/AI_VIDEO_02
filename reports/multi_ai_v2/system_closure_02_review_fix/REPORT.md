# CLOSURE-02-REVIEW-FIX — итоговая сдача

**CLOSURE_02_SG01_SG03_IMPLEMENTED_WAITING_FOR_INDEPENDENT_REVIEW**

SG01–SG03 реализованы и проверены offline через реальные handlers/gate, с doubles
только на transport/native IO и fault injection. Это не реальная автономная
видеостудия: новая exact-SHA CI и отдельная разрешённая live приёмка ещё нужны.

## Версии и область

- Initial local/remote feature: `531f26f8425f925eb2e7627774b6d23225ddb711`, tree `716242f70c4bcc0f4a8e57d05e1d0279b273cad7`.
- Code-tested/source ZIP: `4cee7ad2db11c87583c7823bbabe1a3f75407a73`, tree `c64a27370d2a542b344b5c6cd452282d93db1074`.
- Main snapshot неизменён: `7a1e4058df09dbb66448a3624e0250129eaf7aa0`.
- CI lane snapshot: `ec9ee23fb08daa9076604888ac2e28a4b384f356`.
- Publication commit содержит только этот новый отчёт/JSON/CI proposal; его точный
  SHA и tree даны в финальном сообщении. Он не может включить свой собственный SHA/tree
  внутрь себя без самоссылки. Code binding после публикации проверяется отдельно.
- Tracked/index перед checker чистые. Dirty в evidence честно включает четыре
  исторических untracked evidence и новую report-директорию; они не маскировались.

## Что закрыто и чем доказано

| Gap / acceptance | Caller → actual handler → receipt/gate → test |
|---|---|
| SG01 / A07 — PASS offline | Обычный launch start/resume → fixed Story/Visual adapter → canonical parser + V2ResultGate + immutable Scene v2/mapping v2; `tests.test_v2_scoped_fixes` |
| SG02 / A08 — PASS offline | SystemPipeline.synthesize → guarded_resume → actual JobManager.resume/_spawn → exact Popen handle birth + durable system_spawn_intent/receipt; `tests.test_v2_spawn_receipts`, preserved GenericRecoveryTests |
| SG03 / A05 — PASS offline | Обычный launch/Coordinator → accepted acquisition/transactional enqueue_batch/publication → прежний acceptance gate; `tests.test_v2_closure_revision_crashes`, preserved acquisition rollback test |

Story и Visual выполняются отдельными runs/tests с одним max_cycles=1. Baseline
auto_fix_authorized=false → новый version/parent + true + scoped_fix сохраняет
Canon и независимую Scene. Story меняет один text и Scene version; affected WAV,
timing/edit superseded, неизменный segment/cache/WAV hash сохранён. Visual меняет
один mapping из inventory, wrapper version2 при Scene version1; exact speech/WAV
task/receipt/cache сохраняется и нового synthesize request нет. Оригиналы immutable;
metadata entity_lineage/invalidation содержит original/current IDs и hashes.
Повторный reserve с новым attempt ID запрещён. Counter-only input-version bump
не покупает новый cycle; удаление/смена permit в той же source lineage блокируется.
Отказы покрывают stale Review/hash/evidence, foreign Scene/edition, extra segment,
media authority, wrong role/schema, old auth_generation, malformed output. Parser
имеет положительный контроль на том же точном creative context.

SG02 использует реальный созданный Popen object/Windows handle: PID/FILETIME birth,
job/workspace/run/input revision/request hash/attempt/token. Raw handle не сохраняется.
Есть реальный bounded no-tools Python child fixture и wrong PID/handle negative.
Исторический SQL PID не повышается до ownership. Atomic admit стоит внутри
существующего engine writer transaction; intent compare/insert — в прежнем event
журнале. Concurrent intent даёт один Popen; competing enqueue/resume откатывается.
SYSTEM использует один активный job в отдельном per-run engine: это покрывает
enqueue/resume и исключает второй pending SYSTEM job для legacy continuation.
Произвольные concurrent legacy writers в этот engine вне этой гарантии. Default
None legacy неизменён. Live exact worker → poll; done → verify/reuse; dead exact
worker → максимум один resume. Unknown/reused/early-exited/observer failure и
неоднозначные checkpoints → hold. Deadline повторно проверяется после intent.
Нет утверждения atomicity двух SQLite; чужие процессы не завершаются.

## Crash/revision evidence

SNAPSHOT_SUMMARY.json содержит восемь snapshots заключительного canonical run и
отдельный capture сохранённого rollback regression на том же code SHA: task,
attempt/job IDs/counts, completion hashes, реальные accepted bytes/hashes и package
hashes до сбоя, после восстановления и replay. В mid-batch capture before_0 —
начальное состояние6 tasks/0 attempts, rollback — acquisition checkpoint6/6 с
шестью accepted artifacts; before_1 уже содержит committed expansion21/6.
Полные snapshots запечатаны external manifest; source-only cases имеют
jobs=0 честно, publication/Story/Visual проходят actual handlers с native doubles.

| Точка | Проверенное восстановление |
|---|---|
| Response до Canon/receipt | AMBIGUOUS_PROVIDER_HOLD, reservation сохранён, повторной отправки нет |
| Completion/receipt до Registry acceptance | Тот же attempt/completion принят один раз, второй Canon turn отсутствует |
| Accepted acquisition до expansion | Принятые outputs reused, expansion продолжается |
| Mid-batch insertion | Прежний exact rollback test сохранён и выполнен; дополнительный read-only wrapper записал точные before/rollback/after hashes и counts без замены решения |
| Expansion commit до local flags | Exact inserted task IDs/attempts сохранены; flags восстановлены, дубля marker нет |
| До/после portable manifest publication | Тот же пакет/manifest проверен; дополнительных native commands/jobs/turns нет |

Изменение source bytes при прежнем hash отвергается до transport. Новые явно
разрешённые edition/hash/input version инвалидируют общий Canon/outline/Scenes и
зависимый production graph. Crash после revision expansion и второй replay
сохраняют точные IDs. Отдельный независимый run byte-identical; зависимые от нового
Canon результаты намеренно пересобраны. Story/Visual имеют собственный restart
после expansion commit. Stale/cross-run Result отвергается. Ручная порча accepted
файла вызывает hash conflict и остаётся на диске.

A01–A04/A06/A09–A14: прежние exact tests сохранены и выполнены с прежними явными skips в том же offline
scope; 1/2/5 scenes сохранены. Никакой live/owner acceptance из этого не следует.

## Проверки

Все **642 test ID**, включая все прежние **623**, сохранены; добавлены19,
missing=0, disjoint=true, old_groups_unchanged=true (TEST_INVENTORY.json).
Canonical status `passed`, skipped=1; skips
сохранены в полных logs, тесты не отключались. Source unchanged=true.
Итого: **passed=641, failed=0,
skipped=1** из 642.

Точные пропуски заключительного прогона:

- `test_native_real_wav_fit_preserves_spoken_request_and_source (tests.test_fit_narration.RealPiperTests.test_native_real_wav_fit_preserves_spoken_request_and_source) ... skipped 'Requires installed local Piper'`

| Group | Tests | Skips | Seconds | Result |
|---|---:|---:|---:|---|
| v2 | 111 | 0 | 234.734 | passed |
| contracts | 174 | 0 | 223.000 | passed |
| queue | 50 | 0 | 10.235 | passed |
| adapters | 16 | 0 | 0.360 | passed |
| mai05 | 108 | 0 | 29.953 | passed |
| panel | 12 | 0 | 3.047 | passed |
| legacy | 127 | 1 | 10.141 | passed |
| acquisition | 13 | 0 | 107.938 | passed |
| closure | 12 | 0 | 84.516 | passed |
| review_recovery | 11 | 0 | 2.500 | passed |
| review_story | 1 | 0 | 80.421 | passed |
| review_visual | 1 | 0 | 63.954 | passed |
| review_source_revision | 1 | 0 | 40.906 | passed |
| review_crash | 5 | 0 | 67.094 | passed |
| compileall | — | 0 | 2.281 | passed |
| check_git_export | — | 0 | 4.844 | passed |
| diff_check | — | 0 | 0.063 | passed |

```powershell
.venv-core/Scripts/python.exe -B -X utf8 scripts/check_multi_ai_v2.py --output <NEW_EXTERNAL_PARENT> --task-id CLOSURE-02-REVIEW-FIX
.venv-core/Scripts/python.exe -X pycache_prefix=<EXTERNAL_BYTECODE_ROOT> -m compileall -q app scripts tests
git diff --check
git status --short
git diff --stat 531f26f8425f925eb2e7627774b6d23225ddb711 HEAD
```

Полный checker выполнен после source freeze. Первый run сохранён как failed: sandbox AppContainer Temp запрещал atomic request.json rename (WinError5). Точный minimal probe в явном writable root прошёл. Второй sandbox run выявил отдельное ограничение mklink/junction и acquisition timeout180s (timing/stack сохранены; последний test не объявлен причиной); точный regression прошёл вне sandbox. Итоговый checker использует новый короткий явно writable root вне AppContainer; исходники приложения и таймауты между прогонами не менялись, failed evidence сохранены; test-only fixture correction затем зафиксирован отдельно перед заключительным полным прогоном. source SHA и exact commands
в EVIDENCE_SUMMARY.json. Старые nine groups не перемещались; five new bounded
shards сохраняют180s, v2/contracts420s. Длинные disposable paths дали333.234s для
двух scoped tests вместе и233.110s для crash matrix при concurrent focused load;
поэтому source revision отдельно и Story/Visual раздельно. Для canonical использован
новый короткий root под явно разрешённым visualizations base, вне repo/OneDrive/Drive и AppContainer, без изменения Windows
настроек. Фокусные неудачи сохранены, не стёрты: red missing-interface tests;
PCM-collision fixture; mapping v2/Scene v1 context defect; точные exception/field
expectations в новых tests. Исправления не ослабляют assertions/validators.

Independent CI [39831668255](https://github.com/elkyran/AI_VIDEO_02/actions/runs/39831668255)
фактически **failure**, checkout `eb3ef726afac9079fe346120ee9b082905e5821e`
подтверждён job log. Это не timeout: closure3 failures/12 errors. Остальные core
groups прошли; schema/policy/PowerShell/clean-checkout checks passed. Исправлены
два concrete fixture blockers: short/long Windows temp aliases и чтение отсутствующих
private profile proofs. Новые disposable proofs проходят настоящий load_profile,
voice/video/pronunciation не меняются; corrupted proof отвергается. Исторический
timeout не объясняется последним записанным test. CI change остаётся у владельца
защищённой lane: NEEDS_CHATGPT_CHANGE.md + exact CI_SYNC_PROPOSAL.patch. Новый SHA
ещё не объявлен зелёным, workflow/self-hosted run здесь не запускался.

## Поставка, hashes и сохранность

- External manifest SHA-256: `4c46e531f39f48ccfe671237e6ba8ea4d4baba10a3d0b8c22b3f403bf3911dde`.
- Canonical manifest SHA-256: `bc4aacb685689e85496cc7f8734a7b5dbe0a12738758f593e30ec3a9b24292ef`.
- Source binding SHA-256: `cc0f64d779c1e8c1ff7e4b2f5b40b877204a3b98efda71c587e62c9c99a51872`.
- Source ZIP: `AI_VIDEO_02_CLOSURE_02_REVIEW_FIX_sources_4cee7ad2.zip`, 399640 bytes.
- ZIP SHA-256: `4b1b343d14529310c23424a65f705df715aff1b3123f3e013cbb359502e8db2b`.
- Source inventory SHA-256: `48885222a0b2b5bbf749481bd4e917918342f2a5b836fef3e313443ec96bbe47`.
- Relocated `python -I -B`: passed, 1.500s;
  новые helpers импортируются из unpacked root; native_tools_started=0.
- New outputs до seal: 72138775 bytes <250MiB; free=76026179584
  bytes, reserve4GiB сохранён. Старые artifacts не удалялись.
- Source ZIP исключает models/media/cache/DB/credentials и требует установленные
  Python/dependencies/tools + точные внешние Ruslan/profile proofs; это не portable exe.

PROTECTED_FILES.json подтверждает нулевой diff12 защищённых файлов. Единственное
заранее разрешённое production-core исключение — узкие21 changed lines jobs.py:
optional observer, exact Popen object, admit внутри существующей транзакции.
operations/registry/tts/timeline/worker/profile/studio.cmd, пять ChatGPT-owned файлов,
аккаунты/production studio/media/SQLite/WAL не изменялись. Старые AUDIT/REPORT/hashes
не переписаны. Не было model requests/paid API requests, agy/login, TTS/FFmpeg/MLT native tools,
нового видео, Drive upload/real publication, installs/runner/services/tasks,
reset/rebase/cherry-pick/merge/force-push. Network scope: Git fetch/push и read-only
GitHub CI evidence. Fake WAV/MP4 boundary fixtures не являются новым выпуском.

После source freeze8265f0b полный набор выявил один дополнительный fixture defect: NativeIO(None) в сохранённом delivery test. Отдельный узкий test-only commit исправляет этот вызов с generated disposable proof, после чего заключительный полный checker выполняется на новом чистом code SHA. Старый SHA/ZIP/failed manifest сохранены. Чтобы не переписывать уже созданные commits/evidence, поставка содержит четыре development commits и отдельный report commit (пять вместо первоначального плана3–4).

## Commits и следующие действия

```text
b574e5d151bd19837b30042ee9b787fc3512ac97 fix(recovery): bind exact spawned process receipts to durable SYSTEM intents
455f06bb84d357c60de6725c62b87f81d12c3116 fix(recovery): serialize SYSTEM admission and bound exact spawn intents
8265f0b67fc6ef17e372452c9725ffa14d2da97e fix(system): execute scoped revisions and cover crash-safe closure boundaries
4cee7ad2db11c87583c7823bbabe1a3f75407a73 test(closure): preserve nullable native boundary fixture compatibility
```

Software gaps SG01–SG03 закрыты в описанном bounded offline scope. External gates
остаются: новый independent exact-SHA CI; допустимые официальные Story/Visual
исполнители (live Antigravity BLOCKED_ELIGIBILITY), trusted director/native runtime
и Drive host binding, затем отдельно разрешённая реальная сквозная приёмка.

1. Независимо review этого feature SHA; CI owner применяет только согласованный
   group sync в своей lane и получает новый Windows exact-SHA CI. Ничего не merge автоматически.
2. Владелец отдельно решает external access/runtime/delivery gates и разрешает
   реальную end-to-end приёмку. Новый выпуск и улучшения качества здесь не начинать.
3. После feature push — STOP и ожидание независимого review.
