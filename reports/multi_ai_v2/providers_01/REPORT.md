# PROVIDERS-01 — итоговый авторский offline-отчёт

Operation: `MULTI-AI-V2-PROVIDERS-01`; 2026-10-21.
Result: `DUAL_WORKER_OFFLINE_READY_LIVE_BLOCKED_ELIGIBILITY`.
Status: `PROVIDERS_01_OFFLINE_WAITING_FOR_INDEPENDENT_REVIEW`.
Независимое одобрение этого пакета и SYSTEM-01 не заявляется.

## Версия и область

Repository `elkyran/AI_VIDEO_02`, только `feature/multi-ai-coordinator-v2`.
Preflight: local/fetched remote совпали с `34859cc0bf94c3949f92beea1dbaaabe2eb5ad1c`;
tracked tree чистый. Четыре прежних untracked evidence-каталога сохранены.
Main snapshot `7a1e4058df09dbb66448a3624e0250129eaf7aa0` не изменён.
Security snapshot из поручения: `04351bfd49b11b08d693d0de0d7c39cff0261f6f`;
его независимое одобрение/продвижение здесь не проверялось.

| Commit | Содержание |
|---|---|
| `963757dd24565189cbbe9e6829a933efe53d4f56` | Fixed Story/Visual contracts, synthetic identity boundaries, strict parser, schema |
| `3031da8279124b14526853512cbd77c47e8027c3` | Offline routing, sessions, host receipts/recovery, read-only readiness |
| `eac0313df7805a1d6df0c57541c80cc867787f59` | Regressions, checker registration, dual-worker documentation |
| `314a920057b6fec0c0e619f5c1b5801f4482b42d` | Final code: recovery receipt binds exact immutable artifact payload |

Финальный publication commit содержит только REPORT/STATUS; его SHA и результат
push фиксируются во внешнем publication manifest и итоговом ответе. Проверенная
версия кода — `314a920057b6fec0c0e619f5c1b5801f4482b42d`.

Изменены только разрешённые: `app/v2_provider_bridge.py`, новый
`app/v2_provider_protocol.py`, readiness поля `app/v2_pipeline.py`,
`tests/test_v2_provider_protocol.py`, регистрация группы в checker, exporter,
новая `dual_worker_config.schema.json`, четыре разрешённых архитектурных/system
документа, новый `docs/DUAL_PROVIDER_INTEGRATION.md`, эти REPORT/STATUS.
HostRunConfig/runtime и прежние восемь schemas не изменялись.

Production-core, V2ResultGate, account/CLI interfaces, CI/security файлы, Ruslan,
WAV/cache/MLT, пользовательские media и production Registry не изменялись.
Не выполнялись merge/rebase/reset/clean/force-push, установки, клиенты/login,
модели, TTS, FFmpeg, render, Drive upload, Windows service/runner registration.

## Подтверждённое offline-поведение

Два fixed worker: antigravity_story/account binding A и antigravity_visual/B.
Разные synthetic identities, auth namespaces и session namespaces проверяются
отдельно; одинаковый identity hash под разными profile names отклоняется.
Квота null/UNVERIFIED; различие hashes не доказывает независимых квот.
Реальные account profiles/credentials/OAuth не читались и не менялись.

Data-only exact SyntheticTransport не принимает настоящий клиент через fake flag.
Host session binds worker/generation/run/task/attempt/input hashes/context hash;
unknown fields, stale/mixed replies, malformed/oversized/nonterminal JSON/NDJSON,
unverified tools/media/soft denials, timeout и cancel отклоняются. Host создаёт
Result/artifacts/receipt; requested_model не копируется в returned_model=null.
Бюджеты конечны, source injection не меняет routing/permissions/budget.

Существующие Coordinator/V2ResultGate принимают Scene → отдельный Visual Review →
одну адресную правку line_one → Scene v2. Меняются только текст этой строки и
версия. Revision требует accepted Review/open issue и точный prior Scene hash.
Restart/replay не удваивает acceptance; исходный artifact сохранён. Full proof
с payloads, acceptance/attempt IDs и hashes находится в external `contracts.log`.
OS temporary fixture directories очищены штатным cleanup; это не production media.

Synthetic quota/auth/eligibility ставят конкретную задачу в waiting_access;
kind=permissions, исходная причина в provider_failure_kind, code=WAITING_QUOTA /
WAITING_AUTH / BLOCKED_ELIGIBILITY. Это task-level offline access pause, не live
quota integration. Другой явно поставленный независимый Visual проходит;
зависимый от непринятого Story — нет. Никакой смены аккаунта/модели/подписки.
Новый scheduler/AccountManager/Registry/acceptance table не создавался.

Read-only doctor показывает две обязательные роли, offline IMPLEMENTED /
OFFLINE_TESTED, actual identity/auth/quota/text/image/frame_sequence/full_video
UNVERIFIED, live NOT_RUN, BLOCKED_ELIGIBILITY / AGE-GATED / DISABLED.
Нет реального video evidence: MP4 path/frames не delivery/full-video review.

## Проверки

Адресно: `python -X utf8 -m unittest -q tests.test_v2_provider_protocol tests.test_v2_system_delivery`
из `.venv-core/Scripts/python.exe`: **44 passed** (30 новых + 14 прежних).

| Полная группа | Tests | Passed | Existing skip | Limit seconds |
|---|---:|---:|---:|---:|
| v2 | 109 | 109 | 0 | 420 |
| contracts | 120 | 120 | 0 | 420 |
| queue | 50 | 50 | 0 | 180 |
| adapters | 16 | 16 | 0 | 180 |
| mai05 | 107 | 107 | 0 | 180 |
| panel | 12 | 12 | 0 | 180 |
| legacy | 127 | 126 | 1 | 180 |
| Total | **541** | **540** | **1** | 0 failed |

Все прежние 511 тестов сохранены; новых skips/отключений нет. Compileall,
git diff --check, schema drift (9 моделей) passed. Export/history scan: 1619
files, 1827 history blobs, findings=[]; historical reports не изменены.

```powershell
.venv-core/Scripts/python.exe -X utf8 scripts/check_multi_ai_v2.py --output C:/Users/diffe/.codex/visualizations/2026/10/20/01a158d2-a617-7862-8ff9-5159342bcc27/providers_01/full_final --task-id PROVIDERS-01
.venv-core/Scripts/python.exe -m compileall -q app scripts tests
git diff --check
git status --short
git diff --stat
# Read-only обычный статус без аккаунтов/config/Registry:
.venv-core/Scripts/python.exe -B -X utf8 -m app.v2_provider_bridge
```

Существующий exporter `python scripts/export_multi_ai_v2_schemas.py` выполнен;
drift всех 9 schemas дополнительно проверен без изменения protected validator.

## Внешние evidence

Общий внешний root:
`C:/Users/diffe/.codex/visualizations/2026/10/20/01a158d2-a617-7862-8ff9-5159342bcc27/providers_01/`.

- Основной: `full_final/20261021T175008208134Z_c661a3602af94ad289fca34377d7e3ae/manifest.json`.
  SHA-256 `f606529753f26b06a072148e12c8ec56aae5c51db61ccf246dddbf28087f8468`.
  status=passed, source_unchanged_during_checks=true, commit=314a920… .
- Read-only doctor/schema/protected files:
  `read_only_audit/20261021T174618971461Z_42bbff4d30e3442fa6466493694c969e/manifest.json`;
  SHA-256 `35a2087e4427b3b198646b85307e7b037a27ade12384ab4f881bad1699f1ce51`.
- Предварительный прогон сохранён:
  `full/20261021T174233941424Z_b85d417a619648739d3530b3ebb543ae/manifest.json`;
  SHA-256 `bb6db58398bcaa0d0d33139b5f78f8ae882c49805633d095f8a098e8f91d2b33`.
  Все группы passed, но итог failed из-за source_unchanged=false: во время
  прогона добавлена последняя recovery-проверка. Это НЕ final passing evidence.

Final source SHA-256: `7f1c90246a39837251cb004253a91a30b9f78b523a165ee346000b45e1125064`.
Schema manifest SHA-256: `0d9aeb8dbaab97d905e03676688e5d2acb9d3fdebbe3d7bd65e333903dc25d66`.

## Оставшиеся blockers / действия владельца

1. NEEDS_CHATGPT_CHANGE: CI immutable evidence policy v2/contracts=420s,
   others=180s. В protected generated_schemas добавить HostRunConfig и
   DualWorkerConfig (`dual_worker_config`), сохранив семь прежних моделей и все
   strict/drift проверки. Всего 9. Известный CI 39640298298 не подтверждал полный
   SYSTEM-01; новая зелёная независимая проверка здесь не заявляется. Ранее
   local validator требовал отсутствующий PyYAML; установки не выполнялись.
2. NEEDS_ARCHITECT_REVIEW до live: нынешняя очередь выбирает один profile на
   provider, запрещает creative requested_profile_id, резервирует один cloud
   attempt/provider, quota меняет общий profile. Минимальный diff proposal:

   | Existing function | Только для отдельного review |
   |---|---|
   | Coordinator.enqueue | Host-owned fixed role→worker→profile binding; validate against canonical assigned_worker/profile, запрет model overrides |
   | Coordinator.tick/_dispatch | Resolve exact host route and account generation; reservation by verified account/worker, immutable request binding; без Codex alias |
   | Coordinator._failure | Task/account-scoped quota pause без смены selected profile/rotation; сохранение accepted dependency gates |
   | V2ResultGate | Проверять ту же host worker/account generation при poll/recovery/replay; сохранить CAS/provenance/media receipts |

   Эти изменения ядра НЕ выполнялись; generic legacy quota UI не объявляется
   live поддержкой двух аккаунтов. Offline contract реализует безопасный subset.
3. Реальные workers остаются выключенными из-за eligibility. Отдельное законное
   подтверждение допуска, auth isolation, quotas/media capability и live
   implementation/review нужны до любого отдельного разрешённого live шага.
   Ни второй аккаунт, ни другой сервис не снимают AGE-GATED. Обходов не предлагается.
4. Independent review пакета/SYSTEM-01 и CI sync; merge PR6/main остаётся решением
   владельца, не действием этого пакета. Runner/Drive/production требуют прежних
   отдельных gates; новый выпуск этим пакетом не разрешён.

`model_requests=0; paid_api_requests=0; production_started=false; accounts_connected=0;`
`live_story_verified=false; live_visual_verified=false; eligibility=BLOCKED;`
`system_ready_for_autonomous_episode=false`.

Сетевые операции пакета ограничены Git fetch/push и чтением официальных документов;
provider/model/API/media external calls отсутствуют. После feature push — STOP.
