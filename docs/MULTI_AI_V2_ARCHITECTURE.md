# MULTI-AI-V2: целевая архитектура владельца

SYSTEM-01 update: общий config-driven путь описан в [SYSTEM_RUNBOOK](SYSTEM_RUNBOOK.md)
и [SYSTEM_READINESS](SYSTEM_READINESS.md). Он повторно использует Coordinator,
V2ResultGate, JobManager и ProductionBridge; approved_input не является model
research. Прежний fake19 и ограниченный PILOT-01 сохранены. Есть handlers реального
движка, но в SYSTEM-01 новые TTS/render/episode не запускались. Live provider,
unattended Drive host authorization и Windows runner остаются отдельными gates;
`system_ready_for_autonomous_episode=false`. Историческая таблица ниже относится
к своему срезу и не является текущим утверждением о live readiness.

PROVIDERS-01 уточняет цель: два обязательных Antigravity worker, Story с account
binding A и Visual с отдельным binding B. [Offline границы](DUAL_PROVIDER_INTEGRATION.md)
не означают подключение аккаунтов. Оба live worker выключены, BLOCKED_ELIGIBILITY.

```text
USER: one video brief
        |
        v
PRIVATE GITHUB REPOSITORY
  code / manifests / prompts / schemas
  Issues / Actions
        |
        v
SELF-HOSTED WINDOWS RUNNER
  RTX 4050 — local execution
        |
        +-------------------------+
        |                         |
        v                         v
PYTHON COORDINATOR          EXISTING AI_VIDEO_02
  SQLite                     Piper
  task queue                 WAV/cache
  versions                   Timeline
  checkpoints                MLT
  quotas                     FFmpeg
  recovery                   render
  status                     technical QA
        |
        +----------+-------------+
        |                        |
        v                        v
OFFICIAL CODEX          TWO ANTIGRAVITY WORKERS
 Production             Story A / Visual B
                        AGE-GATED / DISABLED
        |                        |
        +-----------+------------+
                    |
              accepted artifacts
                    |
                    v
             GOOGLE DRIVE
   sources / scripts / WAV / previews
   MLT / final MP4 / backups
```

Python Coordinator — единственный управляющий процесс. Официальные клиенты
исполняют назначенные задачи; собственного scheduler для них не создаётся.
Существующий AI_VIDEO_02 остаётся производственным движком. GitHub хранит код,
metadata, prompts, schemas и небольшие manifests; тяжёлые медиа, модели, SQLite
и credentials туда не отправляются. Google Drive — хранилище, не вычислительный
сервер. Тяжёлые операции предназначены локальному Windows runner с RTX 4050;
проверка GPU и запуск runner этим документом не подтверждаются.

Один запуск начинается с одного brief. `antigravity_story` (writer) и
`antigravity_visual` (critic) требуют разных host-owned account bindings,
auth boundaries и session namespaces. Два profile names или окна этого не
доказывают. Codex Production выполняет производственные операции существующим
движком. Python Coordinator остаётся единственным scheduler и acceptance gate.
Автоматической ротации аккаунтов нет; недоступные квоты остаются null.
Платные API требуют отдельного решения владельца.

## Реализованный срез и незавершённые соединения

| Соединение | Состояние и конкретная граница |
|---|---|
| Brief → Python Coordinator | IMPLEMENTED для fake: `app.multi_ai_v2.RunCoordinator`, один Brief и идемпотентный run key |
| Coordinator → queue/SQLite | IMPLEMENTED: существующие `app.multi_ai.Coordinator`, Registry, ai_tasks/ai_attempts, CAS, checkpoints/recovery; ai_v2_runs хранит metadata, не вторую очередь |
| Queue → accepted artifacts | IMPLEMENTED: общий V2ResultGate для manual/poll/recovery, проверенные immutable dependencies, request account binding, insert-only delivery ledger, ArtifactStore |
| Coordinator → официальные live workers | NOT_IMPLEMENTED для сквозного v2 production; исторический Codex JSON smoke подтверждает только конкретный worker |
| Story/Visual → Antigravity | Два fixed offline slot реализованы на data-only synthetic transport; live BLOCKED_ELIGIBILITY / AGE-GATED / DISABLED. Identity/auth/quota/media фактически UNVERIFIED; requests NOT_RUN |
| Coordinator → existing production engine | PARTIAL: ProductionBridge.compile/prepare_production_request существуют; Scene import и запуск production jobs из v2 NOT_IMPLEMENTED |
| Private GitHub → credentialed Windows runner | BLOCKED: регистрация runner запрещена на текущем этапе; безопасный fake-only workflow проверяется в отдельном security lane |
| Accepted artifacts → Google Drive | PARTIAL: ArtifactStore поддерживает проверенную копию в локальную sync-папку; это не cloud upload proof. Unattended runner auth/root, cloud readback и автоматический upload pipeline NOT_IMPLEMENTED |
| Production → final MP4 / technical и creative QA | Existing engine существует; сквозной live v2 pilot BLOCKED, video_qa=NOT_RUN для fake |

Реальный fake-поток: brief → RunCoordinator → существующая очередь → FakeAdapter
→ editorial validator / V2ResultGate → ai_artifacts + ai_events → ArtifactStore
→ manifest. 19 зависимых этапов используют Scene/EditPlan/Review контракты.
Fake notes не запускают Piper, FFmpeg, render, QA или Drive upload и не доказывают
просмотр видео. Production возвращает BLOCKED_UNSUPPORTED без запуска attempts.

Context Pack ограничен восемью файлами; в нём нет полной копии истории/репозитория.
Нормализация состояния является представлением Registry. Approval/cancellation/
cache state machine остаётся PARTIAL; WAITING_APPROVAL, CANCELLED и SKIPPED_CACHED
не объявлены полностью реализованными.

Новые run/task/result/artifact контракты — app/v2_contracts.py; Scene/EditPlan/
Review используют app.editorial; экспорт схем — config/multi_ai_v2_schemas.
Старые studio.cmd и multi_ai.cmd, Ruslan, отдельные WAV/cache, Timeline/MLT,
оригиналы, ручные правки и production registry сохраняются. Рабочие базы не мигрируются.

Следующий разрешённый шаг — независимый CI/review текущего пакета. Реализация
недостающих соединений, регистрация runner и live pilot требуют отдельного
поручения. [Runbook](REMOTE_RUNBOOK.md), [возможности](PROVIDER_CAPABILITY_MATRIX.md),
[план](../development/multi_ai_v2/PLAN.md).

BULK-01 добавляет [offline bridge](OFFLINE_PRODUCTION_BRIDGE.md): accepted Scene
→ validated_not_registered ProductionScript → отдельные narration/cache recipes
→ existing MLT compiler → структурное QA → immutable release. Одна prepare-команда
проходит fixture-этапы; resume использует existing queue/events. Targeted revision
создаёт новую принятую Scene версию, не заменяет старые файлы и не затрагивает
чужие cache keys. Live registration/jobs/TTS/render/creative QA/Drive остаются
BLOCKED или NOT_IMPLEMENTED; это не production-ready pilot.
