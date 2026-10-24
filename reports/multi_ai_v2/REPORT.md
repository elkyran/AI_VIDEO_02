# MULTI-AI-V2 — итог первого вертикального среза

## 1. Что найдено

Действующий MVP сохранён: Registry/jobs/TTS/WAV/cache/Timeline/MLT/QA.
Существующий Multi-AI уже содержит очередь, attempts, CAS acceptance,
FakeAdapter, subprocess CLI, version/evidence validators, панель и production bridge.
Новая реализация переиспользует их. Аудит: [AUDIT.md](AUDIT.md).
Production registry прочитан mode=ro: 45 done, 2 blocked, 4 stale; активных jobs нет.
Старая live Multi-AI очередь на паузе. Старые T00–T12 и MAI-журнал не изменены.

## 2. Что изменено

Ветка feature/multi-ai-coordinator-v2; baseline-before-multi-ai-v2 указывает на
7a1e4058df09dbb66448a3624e0250129eaf7aa0. Baseline ZIP и snapshots обеих SQLite
созданы через backup API и исключены из Git. Хеши рабочих DB после тестов совпали.
Production profile, исходные app-модули, модели, WAV, MLT и медиа не изменены.
Новая таблица ai_v2_runs хранит только run metadata; очередь/attempts/artifacts/events
по-прежнему в существующих ai_* таблицах той же выбранной локальной Registry.
Нет Redis, отдельного UI или второй реализации scheduler.

Реализованы строгие контракты и JSON Schema, RunCoordinator facade,
19-этапный fake dependency graph, bounded Context Pack, нормализация статусов,
неизменяемые hash-addressed JSON artifacts, проверяемое копирование в отдельный mount,
официальный Codex probe, workflow_dispatch scaffold и безопасные локальные скрипты.
Live production возвращает BLOCKED_UNSUPPORTED, а не запускает FakeAdapter как настоящее производство.
Полная approval/cache/cancellation state machine и Scene-to-production import пока частичны/не реализованы.

## 3. Статус исполнителей

| Исполнитель | Профиль/вход | Идентичность/модель/квота | Статус и доказательство |
|---|---|---|---|
| Codex | chatgpt_primary, официальный ChatGPT auth | hash verified; requested/returned model null; официальный account/rateLimits | VERIFIED для одного schema smoke, не для видео; codex_probe.json |
| Story | google_primary, целевой agy | identity/model/quota null; установлен desktop, native agy не найден | UNAVAILABLE, выключен; gemini_story_probe.json |
| Visual / Cursor | второй независимый аккаунт не подтверждён | editor 3.1.15; Agent CLI/billing/Google identity/media не подтверждены | BLOCKED_UNSUPPORTED локально; gemini_visual_probe.json |
| Visual / второй agy | официальный isolated boundary не подтверждён | INDEPENDENCE_UNVERIFIED; shared sign-in описан документацией | UNVERIFIED, выключен; identity_isolation.json |
| Fake roles | симуляция | ни аккаунтов, ни независимой квоты, model=null | Только управление зависимостями; fake_workflow.json |

Codex CLI 0.163.0-alpha.10.2 дал реальный JSON MULTI-AI-V2-OK, exit=0,
один запрос: input 11408, output 38 tokens. Прочие model calls — 0.
Paid API calls — 0, ротации — 0. Полный email/credential-файлы не сохранялись.
Официальные документы у Cursor описывают headless/JSON; отсутствие возможности
вообще не утверждается. Его установленный маршрут и оплата здесь не доказаны.

## 4. Тесты и доказательства

Команда: `.venv-core/Scripts/python.exe -X utf8 scripts/check_multi_ai_v2.py`.
338 тестов passed: v2 13, contracts 14, queue 50, adapters 16, accounts/runtime 107,
panel 12, legacy 126. Проверялись временные данные, без моделей и production.
Источник и JSON-подтверждение одной ревизии: evidence/20261020T131633Z/checks.json.

Проверены graph, строгие схемы, idempotency, quota pause без rotation,
restart с повторным открытием и отдельный dispatch subprocess rerun,
сохранение accepted результатов без повторных attempts, traversal, redaction,
хеши, missing/corrupt artifact, недоступный Drive и сохранность старой очереди.
Существующие тесты дополнительно покрывают stale/late results, retry, limits,
MLT, технические проверки, аккаунты и UI. Fake workflow принял 19 задач;
после повторного открытия attempts осталось 19. Это не Windows reboot.

Python syntax, PowerShell parser, YAML parser и реальный healthcheck прошли.
Первый тестовый запуск не прошёл из-за Windows sandbox atomic rename/hardlink
и незакрытого read-only соединения в новом защитном коде; соединение исправлено,
второй прогон и итоговая регрессия вне sandbox прошли. Полные логи сохранены локально,
исключены из Git; короткий вывод содержит truncated при ограничении.

## 5. Пилот

60–90-секундное видео не создавалось: BLOCKED.
Тема живого пилота, длительность, принятые секунды, visual match, качество,
повторы и сравнение старого/нового production — null, не выдуманные нули.
Симуляция не синтезирует WAV, не рендерит MP4, не выполняет творческий QA и upload.
Один реальный Codex acknowledgement не заменяет live Story/Visual/монтаж.

## 6. Что не удалось и минимальные следующие шаги

1. Story: нет проверенного native agy. Нужен официальный CLI и вход основного
   Google-аккаунта; затем help/auth/model/JSON smoke и подключение adapter к очереди.
   Внутренний language_server не используется; старый Gemini Free API не подставлялся.
2. Visual: нет доказанной независимости/квоты/оплаты/media. Проверить официальный
   Cursor Agent CLI или документированный второй agy boundary; иначе оставить
   disabled/manual. Не читать токены, не менять первый вход и не автоматизировать GUI.
3. Live production: реализовать принятие утверждённой Scene в существующий script
   и диспетчеризацию существующих jobs с original revision/key. Существующий bridge
   умеет MLT и request descriptors, но не новую полную живую цепочку.
4. GitHub runners=0, registered/online=0. Workflow не запускался. Нужен официальный
   current-user runner с label ai-video-02. Task Scheduler выбран как кандидат по
   доступу к профилю; установка/службы/Windows reboot не выполнялись.
5. Drive подключён в Codex в ходе работы: создана папка AI_VIDEO_02 и загружен
   manifest 373 байта. Raw readback совпал побайтово и по SHA-256:
   96d54c47c55f49f2328779fc249c0ce43e82ec689a011afceffc82ec1783288f.
   [Cloud manifest](https://drive.google.com/file/d/1O9lwbDKuYC-ODrXWiCJc4yrCmGXaxBaj/view).
   Connector upload VERIFIED; доступ unattended runner, local root и 5 ТБ пока
   UNVERIFIED. Настроить официальный доступ runner; медиа не загружались.

## 7. Повседневные команды

Подробно: docs/REMOTE_RUNBOOK.md. После задания LOCAL_RUN_ROOT вне sync и
STUDIO_PYTHON: healthcheck.ps1; `python -m app.multi_ai_v2 create --brief
examples/multi_ai_v2_brief.json --key UNIQUE_KEY`; затем status/advance/pause/stop/
manifest --run-id ID. Advance — явное продолжение. Manifest содержит пути к JSON.

Удалённо: Actions → AI_VIDEO_02 request → Run workflow → fake, только после
регистрации runner и наличия workflow на разрешённой ветке. Production пока
возвращает blocker. Остановка/пауза относятся ко всей выделенной очереди v2.
Повтор одной сцены в удалённом v2 пока NOT_IMPLEMENTED; existing update_scene/
invalidate_scene сохраняются. Возврат к MVP — studio.cmd; новый режим просто
не запускать. Не применять clean/reset --hard и не копировать DB поверх WAL.

## 8. Финальная классификация

| Компонент | Классификация |
|---|---|
| Аудит/baseline/backup | WORKING_AND_VERIFIED |
| Fake Run + existing queue + restart/idempotency | WORKING_AND_VERIFIED |
| Contracts/context/artifact integrity | WORKING_AND_VERIFIED в тестовом scope |
| Полная state machine approvals/cache/cancel | PARTIAL |
| Codex single safe official JSON task | WORKING_AND_VERIFIED |
| Codex в live production v2 | NOT_IMPLEMENTED |
| Gemini Story live | BLOCKED |
| Gemini Visual live/video review | BLOCKED |
| Existing MLT bridge | WORKING_AND_VERIFIED ограниченными контрактными тестами |
| v2 Scene import и jobs dispatch | NOT_IMPLEMENTED |
| Технический QA старого MVP | Переиспользован; не является QA нового видео |
| Editorial live QA/control-video | BLOCKED |
| Workflow и logon scripts | IMPLEMENTED_NOT_LIVE_TESTED |
| Artifact mount-copy | WORKING_AND_VERIFIED на temp, cloud UNVERIFIED |
| Drive connector upload + raw readback | WORKING_AND_VERIFIED, 373-byte manifest |
| Unattended Drive adapter для runner | NOT_IMPLEMENTED / UNVERIFIED |
| Пилот и before/after | BLOCKED |

Общего процента готовности нет. Пакет остановлен на конкретных внешних блокерах;
код и тесты первого среза сохранены, минимальная полная готовность не заявлена.

## Реестр изменённых файлов

Каждая строка описывает назначение и причину включения файла в этот пакет.

| Путь | Назначение / причина |
|---|---|
| `.env.example` | Пустой шаблон локальных путей, без секретов |
| `.github/workflows/new-video.yml` | Private workflow_dispatch scaffold с fixed runner labels, JSON inputs и concurrency |
| `.gitignore` | Разрешить только redacted evidence нового пакета; большие данные остаются excluded |
| `README.md` | Точка входа в новый режим и предупреждение о фактическом partial статусе |
| `app/multi_ai_v2.py` | Run facade над существующей очередью; без дублирующего scheduler |
| `app/v2_contracts.py` | Строгие run/task/result/artifact/review contracts и owner routing |
| `app/v2_storage.py` | Неизменяемые JSON artifacts и проверяемое копирование в mount без ложного uploaded |
| `config/multi_ai_v2_schemas/artifact.schema.json` | Экспорт строгого контракта для проверяемого обмена между исполнителями |
| `config/multi_ai_v2_schemas/brief.schema.json` | Экспорт строгого контракта для проверяемого обмена между исполнителями |
| `config/multi_ai_v2_schemas/edit_plan.schema.json` | Экспорт строгого контракта для проверяемого обмена между исполнителями |
| `config/multi_ai_v2_schemas/result.schema.json` | Экспорт строгого контракта для проверяемого обмена между исполнителями |
| `config/multi_ai_v2_schemas/review.schema.json` | Экспорт строгого контракта для проверяемого обмена между исполнителями |
| `config/multi_ai_v2_schemas/scene.schema.json` | Экспорт строгого контракта для проверяемого обмена между исполнителями |
| `config/multi_ai_v2_schemas/task.schema.json` | Экспорт строгого контракта для проверяемого обмена между исполнителями |
| `development/multi_ai/ROADMAP.md` | Новый план/статус и указатель из старого roadmap; исторические журналы сохранены |
| `development/multi_ai_v2/PLAN.md` | Новый план/статус и указатель из старого roadmap; исторические журналы сохранены |
| `development/multi_ai_v2/STATUS.json` | Новый план/статус и указатель из старого roadmap; исторические журналы сохранены |
| `docs/ACCOUNT_AND_AUTH_MODEL.md` | Инструкция и точные границы v2; не выдавать scaffold за живую интеграцию |
| `docs/GOOGLE_DRIVE_STORAGE.md` | Инструкция и точные границы v2; не выдавать scaffold за живую интеграцию |
| `docs/MULTI_AI_V2_ARCHITECTURE.md` | Инструкция и точные границы v2; не выдавать scaffold за живую интеграцию |
| `docs/PILOT_ACCEPTANCE.md` | Инструкция и точные границы v2; не выдавать scaffold за живую интеграцию |
| `docs/PROVIDER_CAPABILITY_MATRIX.md` | Инструкция и точные границы v2; не выдавать scaffold за живую интеграцию |
| `docs/QUOTA_AND_RECOVERY.md` | Инструкция и точные границы v2; не выдавать scaffold за живую интеграцию |
| `docs/REMOTE_RUNBOOK.md` | Инструкция и точные границы v2; не выдавать scaffold за живую интеграцию |
| `docs/SECURITY_BOUNDARIES.md` | Инструкция и точные границы v2; не выдавать scaffold за живую интеграцию |
| `docs/VIDEO_REVIEW_PROTOCOL.md` | Инструкция и точные границы v2; не выдавать scaffold за живую интеграцию |
| `examples/multi_ai_v2_brief.json` | Безопасный fake brief для воспроизведения управления |
| `reports/multi_ai_v2/AUDIT.md` | Аудит/итоговый отчёт текущего поручения |
| `reports/multi_ai_v2/REPORT.md` | Аудит/итоговый отчёт текущего поручения |
| `reports/multi_ai_v2/evidence/20261020T131633Z/backup_manifest.json` | Свидетельство текущей проверки; отделить факт от документации и сохранить версии/блокеры |
| `reports/multi_ai_v2/evidence/20261020T131633Z/capability_matrix.json` | Свидетельство текущей проверки; отделить факт от документации и сохранить версии/блокеры |
| `reports/multi_ai_v2/evidence/20261020T131633Z/checks.json` | Свидетельство текущей проверки; отделить факт от документации и сохранить версии/блокеры |
| `reports/multi_ai_v2/evidence/20261020T131633Z/codex_probe.json` | Свидетельство текущей проверки; отделить факт от документации и сохранить версии/блокеры |
| `reports/multi_ai_v2/evidence/20261020T131633Z/dependency_versions.json` | Свидетельство текущей проверки; отделить факт от документации и сохранить версии/блокеры |
| `reports/multi_ai_v2/evidence/20261020T131633Z/drive_probe.json` | Свидетельство текущей проверки; отделить факт от документации и сохранить версии/блокеры |
| `reports/multi_ai_v2/evidence/20261020T131633Z/environment.json` | Свидетельство текущей проверки; отделить факт от документации и сохранить версии/блокеры |
| `reports/multi_ai_v2/evidence/20261020T131633Z/export_check.json` | Свидетельство текущей проверки; отделить факт от документации и сохранить версии/блокеры |
| `reports/multi_ai_v2/evidence/20261020T131633Z/fake_workflow.json` | Свидетельство текущей проверки; отделить факт от документации и сохранить версии/блокеры |
| `reports/multi_ai_v2/evidence/20261020T131633Z/gemini_story_probe.json` | Свидетельство текущей проверки; отделить факт от документации и сохранить версии/блокеры |
| `reports/multi_ai_v2/evidence/20261020T131633Z/gemini_visual_probe.json` | Свидетельство текущей проверки; отделить факт от документации и сохранить версии/блокеры |
| `reports/multi_ai_v2/evidence/20261020T131633Z/identity_isolation.json` | Свидетельство текущей проверки; отделить факт от документации и сохранить версии/блокеры |
| `reports/multi_ai_v2/evidence/20261020T131633Z/pilot_manifest.json` | Свидетельство текущей проверки; отделить факт от документации и сохранить версии/блокеры |
| `reports/multi_ai_v2/evidence/20261020T131633Z/preservation.json` | Свидетельство текущей проверки; отделить факт от документации и сохранить версии/блокеры |
| `reports/multi_ai_v2/evidence/20261020T131633Z/quota_pause_test.json` | Свидетельство текущей проверки; отделить факт от документации и сохранить версии/блокеры |
| `reports/multi_ai_v2/evidence/20261020T131633Z/restart_resume_test.json` | Свидетельство текущей проверки; отделить факт от документации и сохранить версии/блокеры |
| `reports/multi_ai_v2/evidence/20261020T131633Z/runner_probe.json` | Свидетельство текущей проверки; отделить факт от документации и сохранить версии/блокеры |
| `reports/multi_ai_v2/evidence/20261020T131633Z/video_review_probe.json` | Свидетельство текущей проверки; отделить факт от документации и сохранить версии/блокеры |
| `scripts/bootstrap.ps1` | Ограниченная команда запуска/проверки/подготовки; без автоматической установки и производства |
| `scripts/check_multi_ai_v2.py` | Ограниченная команда запуска/проверки/подготовки; без автоматической установки и производства |
| `scripts/dispatch_multi_ai_v2.py` | Ограниченная команда запуска/проверки/подготовки; без автоматической установки и производства |
| `scripts/export_multi_ai_v2_schemas.py` | Ограниченная команда запуска/проверки/подготовки; без автоматической установки и производства |
| `scripts/healthcheck.ps1` | Ограниченная команда запуска/проверки/подготовки; без автоматической установки и производства |
| `scripts/healthcheck_multi_ai_v2.py` | Ограниченная команда запуска/проверки/подготовки; без автоматической установки и производства |
| `scripts/install-runner-task.ps1` | Ограниченная команда запуска/проверки/подготовки; без автоматической установки и производства |
| `scripts/probe_multi_ai_v2.py` | Ограниченная команда запуска/проверки/подготовки; без автоматической установки и производства |
| `scripts/start-coordinator.ps1` | Ограниченная команда запуска/проверки/подготовки; без автоматической установки и производства |
| `scripts/uninstall-runner.ps1` | Ограниченная команда запуска/проверки/подготовки; без автоматической установки и производства |
| `tests/test_multi_ai_v2.py` | Воспроизводимые safety/failure/restart/integration tests на временных данных |
| `reports/multi_ai_v2/evidence/20261020T131633Z/drive_test_manifest.json` | Безопасные исходные байты живого connector upload/readback smoke |

Коммиты реализации: 7871c39 (аудит/план), 6460309 (проверенный код).
Drive identity проверена официальным profile endpoint и совпала с указанным владельцем аккаунтом; в evidence сохранён только fingerprint.
