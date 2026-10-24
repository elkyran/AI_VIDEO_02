# RELEASE-01 — локальная поставка и единый release checkpoint

Дата владельца: 2026-10-22, Asia/Qyzylorda.
Operation: `MULTI-AI-V2-RELEASE-01`.
Итог: **RELEASE_01_PARTIAL_WITH_EXACT_BLOCKERS**.

Разрешённые локальные соединения реализованы и проверены: общий launcher/CLI,
host-owned worker routes в существующей очереди, независимые reservations,
quota/auth pause, единый acceptance/recovery, Brief plan/explicit synthetic
handoff, private Drive host resolver и source-only поставка. Полную программную
цепочку от одной темы до автономного выпуска завершённой не объявляем:
реальные Story/Visual недоступны, а текущий transport ещё не реализует acquisition
Canon/outline/visual selection и фактическую media delivery. Approved-input режим
не заменяет эту цель. Нового содержательного выпуска нет.

## Git и изменённая область

Repository `elkyran/AI_VIDEO_02`, только `feature/multi-ai-coordinator-v2`.
Initial local/fetched remote: `1a65eb29bccab7af9953159b0e7fadc0b9916d48`.
Main: `7a1e4058df09dbb66448a3624e0250129eaf7aa0`, не изменён.
Отдельная CI-ветка при preflight: `c6d48c858b72cd56586858ad0ed2ff4b99981165`.
PR9 и PR6 не merging/cherry-pick. Старые четыре untracked evidence trees сохранены.

| Commit | Результат |
|---|---|
| `45000164bfac0e255eff5d185780a189cc5bf5ca` | Windows owned-process-tree / reader / pipe / handle lifecycle |
| `9db4f03db3df05fa5f6ed7714ef3689e8e4ea90d` | Host routes, identity reservations, shared result gate, scoped quota pause |
| `094daa919d7f17685d8bc20168938a46a86ba143` | Explicit Drive authorization/private sessions и v2 launcher |
| `d8dce0029b0da52b92e29c79f27cfe53078b1ec8` | Brief plan, SYSTEM worker stages, typed handoff, observation/control, schema/tests/examples |
| `c881e4cb5f07c11508326dc42cf657f485a50d63` | Runbook/readiness/independent release requests; версия полного прогона и локального ZIP |

Report/STATUS добавляются отдельным publication commit. Его exact SHA, actual
remote SHA после push и hashes отчёта записываются во внешний publication manifest
и итоговый ответ, без самоссылочного hash внутри commit. Источник полной проверки:
commit `c881e4cb5f07c11508326dc42cf657f485a50d63`, tree
`90f8be76efa63ef357f9e9612a6e4a70d860fe54`.

Адресные изменения: Coordinator/RunCoordinator/V2ResultGate, diagnostics lifecycle;
v2 pipeline/run config/provider bridge и новые v2 routes/plan/flow/observation/
delivery-host helpers; focused tests; checker summary parser/registration;
healthcheck; новый launcher; host_run_config schema; разрешённые docs/examples/report.
Все прежние 9 schemas сохранены, изменено только содержимое HostRunConfig.
Новый frontend, server, scheduler, AccountManager, Registry, renderer не создавались.

`git diff --quiet INITIAL HEAD --` подтвердил неизменность пяти ChatGPT-owned
CI/security файлов, production-core operations/jobs/registry/tts/timeline,
production_profile.json, studio.cmd, ai_accounts.py и ai_cli_runtime.py.
Рабочая студия, SQLite/WAL, пользовательские media/cache, Ruslan, музыка,
произношения и настройки стиля не изменялись. Миграции production DB не было.

## Исправление Windows CI failure

Известное независимое failure: run `39656666964`, attempt1,
temporary merge `957f0fb25de6ff22dd0eaa29ff410415cdb08ef6`.
Его ZIP `730c2dee4e0a77cef5214474ede5e8c489142fb3a426cd35265a49730153c743`,
manifest `c56000c0d8a4036b993142c13f9ee7c4c89efd6bbb99db50be372438086fd8f7`
остаются историческим failed evidence, не переименовываются в pass.

Причина в runtime подтверждена адресным исследованием: прежний `_stop` закрывал
Job Object, не дожидаясь нулевого числа дочерних процессов, особенно если direct
parent уже вышел. Это оставляло кратковременно занятые cwd/stdout handles и
TemporaryDirectory.cleanup завершался WinError32/5. Теперь Job Object сохраняется
до TerminateJobObject + проверенного ActiveProcesses=0; единый deadline 3s, затем
parent wait, reader joins, закрытие pipes и native process handle. Истечение
deadline — cleanup_unverified failure, не retry удаления или ignore_errors.
Нет убийств по имени/чужому PID; taskkill fallback удалён.

Orphan test требует actual False для child liveness, отсутствие reader threads,
закрытые pipes/native handle и немедленную очистку своего temp directory.
Focused: diagnostics module 9/9, затем два orphan + deadline negative 3/3;
не более трёх адресных orphan executions. Дополнительно тест вошёл в единственный
полный regression. Реальная account диагностика/запуск клиента не выполнялись.

Checker теперь учитывает skipped в FAILED(errors=1, skipped=1), сохраняя nonzero
exit как failure. Старые evidence не переписываются. Есть адресные parser tests.

## Что проверено в общей цепочке

Host config закрепляет role→worker→provider→profile→identity/auth_generation.
Разные synthetic accounts A/B одного provider имеют два одновременных handles;
одинаковая identity под разными профилями/generations делит reservation.
Cloud limit=3, account/worker/profile=1, local-heavy=1. Legacy без routes сохраняет
прежнюю selected_profiles/per-provider семантику. Missing/stale route — hard block.

Task/attempt/run/inputs/route записываются до старта. Poll, manual result,
persisted completion, adapter recovery и replay используют тот же V2ResultGate.
Swapped/stale/generation-mismatched replies и conflicting replay отклоняются;
исторические accepted artifacts после переавторизации сохраняются.
Active reservation читает pinned task/request/attempt вместе, corrupted request
консервативно удерживает lock. Quota/auth pause scoped к account, сохраняет причину
и deadline; explicit selective resume не обходит deadline. Нет rotation/fallback.
Outbox другого account не блокирует независимый Visual; зависимый ждёт Story.

BriefInputs разрешает тему + sources/media без заранее написанного сценария.
План имеет sources→Canon→outline→N scenes→visual mappings→production expansion.
Неподдерживаемые capabilities явно блокируют execution до provider/model/jobs.
Prepare сохраняет immutable blocked plan без Registry и production permit.

Для simulation имеется **explicit host fixture handoff**, а не модельный Canon:
строгие inputs/Story/Visual fixtures передаются явно, сверяются с исходными
allowlists и переходят в существующие SYSTEM handlers. Проверены разные Brief,
1/2/5 сцен, Unicode/IDs, новые roots, exact replay/restart, targeted revision
без изменения чужих jobs/artifacts. Provider Scene точно соответствует отдельно
утверждённым inputs; Visual canonical Review metadata_only не становится
full-video review. Реальная Canon/outline/visual/media интеграция не объявляется.

Status/inspect/report читают mode=ro без recovery и чтения credentials, доступны
даже при исчезнувшем original source. Recorded results явно NOT_REVALIDATED.
Stop/Pause используют прежние control/stopping состояния под тем же OS lock;
Stop не утверждает фактическую смерть неизвестного процесса. Синхронная операция
ограничена deadline, второй Stop при занятом lock сообщает конкретный blocker.

DriveHostContext использует явный credential_ref/folder/run/version callback
из host, без поиска credentials/cookies/OAuth/login. Private resumable sessions
зашифрованы current-user DPAPI, находятся вне repo/sync/run/input. Проверены
scope, wrong-folder/run/version, corruption, auth expiry и existing delivery
replay/checksum behavior на synthetic transports. Actual callback/owner folder
не угадывались. Uploaded=false, remote_sync=UNVERIFIED, cloud receipt отсутствует.

## Полная проверка

Один canonical full checker после source freeze; **596 = 595 passed + 1 прежний
skip, failed=0**. Все прежние 541 тест сохранены; добавлено 55. Новых skips нет.

| Group | Tests | Skipped | Seconds | Limit |
|---|---:|---:|---:|---:|
| v2 | 109 | 0 | 221.828 | 420 |
| contracts | 174 | 0 | 160.797 | 420 |
| queue | 50 | 0 | 10.281 | 180 |
| adapters | 16 | 0 | 0.344 | 180 |
| mai05 | 108 | 0 | 27.781 | 180 |
| panel | 12 | 0 | 2.844 | 180 |
| legacy | 127 | 1 | 10.469 | 180 |

Compileall, diff_check и read-only export/history audit passed. До report commit:
1640 exportable files, 1865 history blobs, findings=[]. Final pre-push audit
повторно включает report/status, его точные counts — во внешнем publication proof.
Dirty=true только из-за сохранённых historical untracked evidence;
tracked changes отсутствовали, source_unchanged_during_checks=true.

```powershell
.venv-core/Scripts/python.exe -X utf8 scripts/check_multi_ai_v2.py --output C:/Users/diffe/.codex/visualizations/2026/10/20/01a158d2-a617-7862-8ff9-5159342bcc27/release_01/full_final --task-id RELEASE-01
.venv-core/Scripts/python.exe -m compileall -q app scripts tests
git diff --check
git status --short
git diff --stat
```

Полные команды/exit/durations и logs — checks.json/manifest; focused checkpoints
— внешний release_01/checkpoints. Сохранены также сведения о промежуточном
permission-blocked тестовом запуске и schema drift до exporter; они не являются
финальным code failure/pass. Полный regression не повторялся до случайного успеха.

Full evidence directory:
`C:/Users/diffe/.codex/visualizations/2026/10/20/01a158d2-a617-7862-8ff9-5159342bcc27/release_01/full_final/20261022T142730495935Z_1f4f9925e1f84b7db6b99e3366de0deb`.
Manifest SHA-256: `35bbbf5e9fd9f7ae83a059e2efbe4639e31cd9beaf3701d612aa219643c81d7d`.
Source SHA-256: `7c822f465dd06cb940339dd4cc14a99de94dfd219df0f9b76c8746d7f9c20885`.
Schema manifest SHA-256: `6efbc356717e07be84e9cfe0b3c87b7ae34393566d9989e7007525009d86b387`.

## Реальная локальная поставка

Source-only ZIP, 82 explicit source/config/docs/example files, без DB/WAL, OAuth,
credentials, моделей, шрифтов, private logs, больших caches и пользовательских
media. External dependencies перечислены в inventory/RUNTIME_DEPENDENCIES.md;
это не self-contained exe. Doctor из копии поставки passed без execution.

Acceptance root:
`C:/Users/diffe/.codex/visualizations/2026/10/20/01a158d2-a617-7862-8ff9-5159342bcc27/release_01/local_acceptance/20261022T142845136649Z_dd3aa9ec18fb46d68721e52048ee2bd7`.

- ZIP: `AI_VIDEO_02_RELEASE_01_source.zip` внутри acceptance root;
  SHA-256 `78abb19e38968195417384a077e45020b1a3a75a61eddfde9a9af44e44040ea7`.
- Inventory: `software/release_inventory.json`;
  SHA-256 `91878866198c1b11b5961168008c7f9da3f68de68706db8392e0431aaca75583`.
- Acceptance manifest SHA-256:
  `6948e5eaea1fc7a4a1a05037b9afc1e0b50140868b8f62579fcab5a4f3f40bad`.
- Выбранный isolated local root: `selected_local_run` внутри acceptance root;
  вне repo/OneDrive/Drive/studio; bounded write probe passed.
  Free=11,852,664,832 bytes при seal; output=9,068,276 bytes перед manifest,
  budget=262,144,000, reserve=4,294,967,296. Ничего на диске не очищалось.

Read-only копия прежнего PILOT, **не новое видео**:

- MP4: `C:/Users/diffe/.codex/visualizations/2026/10/20/01a158d2-a617-7862-8ff9-5159342bcc27/release_01/local_acceptance/20261022T142845136649Z_dd3aa9ec18fb46d68721e52048ee2bd7/pilot_preservation/final.mp4`.
- MLT: `C:/Users/diffe/.codex/visualizations/2026/10/20/01a158d2-a617-7862-8ff9-5159342bcc27/release_01/local_acceptance/20261022T142845136649Z_dd3aa9ec18fb46d68721e52048ee2bd7/pilot_preservation/project.mlt`.
- Все 14 manifest files byte-identical; 6 WAV открыты/измерены, samples nonzero,
  относительные MLT ссылки прошли verify_portable. Никакого нового decode/render.
  Manifest `bb9ab2315e92011c28c5563707fcf8b6033fdfbcdc6721216d46440f7bee35a7`;
  MP4 `4f43e8a13452e37674f0ba6d69c681138720beabe9196fde67555354284c5447`;
  MLT `f53e8d678490d1c528ad02ebca841bd12c6e016f80fd60062cc83d17d485790d`.

Обычный вход: `studio-v2.cmd --config OWNER_CONFIG doctor`;
plan/Start/Status/Stop/Resume/Review/Deliver описаны в `docs/START_HERE.md` (12 строк)
и SYSTEM_RUNBOOK. Реальное исполнение требует явных --execute-system/--authorize,
approved inputs/runtime и отдельного разрешения; этот пакет его не выполнял.

## Один список оставшихся действий / blockers

1. **Independent exact-SHA CI/review**: ChatGPT отдельно проверяет/интегрирует PR9
   (9 schemas, 420/420/180, minimum541), затем hosted Windows CI на точном итоговом
   integration SHA и единый review RELEASE-01. Локальные tests не independent CI.
   Точные запросы: `NEEDS_CHATGPT_CHANGE.md`. Merge PR9/PR6/main здесь запрещён.
2. **Story/Visual live + недостающая acquisition integration**: eligibility
   остаётся BLOCKED_ELIGIBILITY, accounts_connected=0. Два аккаунта не снимают
   AGE-GATED. Нужны допустимый доступ, отдельный review/реализация строгих Canon /
   outline / visual-selection/media capabilities и реальные host identity/auth/
   quota/delivery evidence. Текущий synthetic handoff этого не доказывает;
   Codex/API не заменяют эти роли. Обходы и автоматическое включение отсутствуют.
3. **Real approved-input execution**: владелец выбирает отдельный root, inputs и
   полный profile-consistent runtime; новая TTS/render приёмка/содержательный
   выпуск требуют отдельного задания. Сейчас доказаны handlers/simulation и
   сохранность прежнего физического PILOT, не новая реальная общая production цепь.
4. **Drive**: нужен разрешённый host callback, точный owner folder и private
   session root, затем отдельная remote write/readback приёмка. Без неё
   drive_remote_verified=false, uploaded=false; локальная копия не cloud proof.
5. **Self-hosted runner**: отдельное owner authorization после принятого main и
   доказанной изоляции. Регистрация/service/Task Scheduler здесь не выполнялись.

`model_requests=0; paid_api_requests=0; production_started=false; accounts_connected=0;`
`live_story_verified=false; live_visual_verified=false; eligibility=BLOCKED_ELIGIBILITY;`
`drive_remote_verified=false; self_hosted_runner_verified=false;`
`system_ready_for_autonomous_episode=false`.

Нули относятся к runtime acceptance проекта; они не утверждают отсутствие
расходов самой разработки Codex. Сетевые операции ограничены Git fetch/push;
provider/model/Drive calls не выполнялись. После feature push и чтения remote SHA — STOP.
