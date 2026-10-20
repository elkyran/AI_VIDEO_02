# MULTI-AI-V2: архитектура и границы первого среза

Новый режим — `python -m app.multi_ai_v2`. Старые studio.cmd и multi_ai.cmd
сохранены. Production TTS, Timeline, Ruslan и рабочие базы не мигрируются.

Поток: brief → RunCoordinator → существующий app.multi_ai.Coordinator →
ai_tasks/ai_attempts → FakeAdapter/существующие адаптеры → строгий editorial
validator → ai_artifacts + ai_events → ArtifactStore → result manifest.
Новая таблица ai_v2_runs хранит только brief и ключ run, а не вторую очередь.
Run связан с ai_tasks по project_id=run_id. Сбой при создании run исправляется
повтором того же create: недостающие задачи добавляются идемпотентно.

Существующий Registry и CAS acceptance — источник состояний. V2 нормализует
pending/completed/waiting_access/interrupted/stale в PENDING/READY/SUCCEEDED/
WAITING_AUTH/WAITING_QUOTA/FAILED_RETRYABLE/STALE. Это представление, не копия БД.
Полная approval/cancellation/cache state machine пока PARTIAL: отдельные
WAITING_APPROVAL, CANCELLED и SKIPPED_CACHED не объявлены реализованными.

Фиксированные роли: Gemini Story — writer, Gemini Visual — critic, Codex —
director. В симуляции локальные этапы также возвращают FakeAdapter note;
никакие TTS, рендер, QA или upload от этих note не выполняются. Симулируются
19 зависимых этапов, включая Scene/EditPlan/Review через существующие контракты.
Fake Story и Fake Visual не являются доказательством двух живых аккаунтов.

Context Pack содержит восемь ограниченных файлов; копии репозитория и истории
нет. Задача и brief ограничены схемой, данные не интерполируются в shell.
Live production намеренно возвращает BLOCKED_UNSUPPORTED без attempts:
нужны Story, Visual, принятие Scene в существующий script, runner и его доступ к Drive.
Connector upload/readback проверен отдельно из Codex; это не авторизация runner.
Один успешный Codex smoke доказывает только конкретный JSON worker.

Расширять по порядку: подтвердить официальный agy → реализовать/проверить
Antigravity adapter без credential hacks → доказать Visual isolation либо manual
→ принять Scene через существующий реестр → подключить существующие production
jobs с исходной revision/key → реальный QA → Drive → 60–90-секундный пилот.
ProductionBridge.compile и prepare_production_request уже существуют;
descriptor не выдаётся за запущенный synthesize/build/render/qa/package.

Новые схемы: config/multi_ai_v2_schemas; Scene и EditPlan экспортированы из
app.editorial, дополнительные run/task/result/artifact/review — app.v2_contracts.
Результат и изменения: reports/multi_ai_v2/REPORT.md.
