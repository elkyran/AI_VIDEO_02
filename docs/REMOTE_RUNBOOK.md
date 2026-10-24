# Локальный и удалённый запуск v2: текущие границы

Целевая схема — [архитектура владельца](MULTI_AI_V2_ARCHITECTURE.md).
Сейчас разрешены offline проверки и fake на временных данных. Сквозной production
остаётся BLOCKED_UNSUPPORTED. Регистрация self-hosted runner запрещена до
отдельного решения владельца; этим runbook запуск credentialed workflow не разрешается.

## Локальная симуляция

Выберите отдельный локальный каталог вне OneDrive/Google Drive. .env.example —
только шаблон; он не загружается автоматически. Секреты в brief не передаются.

```powershell
$env:STUDIO_PYTHON = (Resolve-Path .venv-core/Scripts/python.exe).Path
$env:LOCAL_RUN_ROOT = 'C:/AI_VIDEO_02_LOCAL/v2'
& $env:STUDIO_PYTHON -X utf8 scripts/healthcheck_multi_ai_v2.py --mode fake --root $env:LOCAL_RUN_ROOT
& $env:STUDIO_PYTHON -m app.multi_ai_v2 create --brief examples/multi_ai_v2_brief.json --key first_fixture
& $env:STUDIO_PYTHON -m app.multi_ai_v2 status --run-id RUN_ID_FROM_CREATE
& $env:STUDIO_PYTHON -m app.multi_ai_v2 advance --run-id RUN_ID_FROM_CREATE --ticks 60
& $env:STUDIO_PYTHON -m app.multi_ai_v2 pause --run-id RUN_ID_FROM_CREATE
& $env:STUDIO_PYTHON -m app.multi_ai_v2 stop --run-id RUN_ID_FROM_CREATE
& $env:STUDIO_PYTHON -m app.multi_ai_v2 manifest --run-id RUN_ID_FROM_CREATE
```

Использовать только brief с production_mode=fake. advance явно продолжает очередь
после остановки/перезапуска. Pause/stop действуют на выделенный v2 workspace,
не на существующую production студию. Несколько run используют одну existing
очередь. Manifest симуляции содержит реальные JSON fixture artifacts,
video_qa=NOT_RUN и не подтверждает TTS/render/upload.

## GitHub workflow и security lane

Проверенный credentialed `.github/workflows/new-video.yml` в security lane
`origin/assistant/review-fix-ci-runner-safety` на SHA
`964bdd7b5a0e22ba081a8440031e29c8fc2fc048` имеет следующие фактические ограничения:

- Только private repository и `refs/heads/main`; feature ref не исполняется.
- Checkout точного github.sha: `clean: true`, `persist-credentials: false`;
  проверяются точный HEAD и чистое рабочее дерево.
- Только fake: production option в workflow inputs отсутствует, Brief использует
  безопасный fake default; healthcheck вызывается с `--mode fake`.
- Self-hosted labels: Windows, ai-video-02; job timeout — 30 минут.
- Credentials, SQLite, медиа и сырые логи клиентов не публикуются.

Это описание проверенного security workflow, а не утверждение о его merge.
В feature/multi-ai-coordinator-v2 ещё сохранён старый шаблон new-video.yml;
он не соответствует этим ограничениям и не должен запускаться на credentialed
машине. Phase A не меняет и не переносит ChatGPT-owned workflows. Интеграция
security lane и независимый review должны предшествовать любому отдельному
решению владельца о runner. Не выбирать feature-ветку в Run workflow.

Runner не регистрировать; install-runner-task.ps1 с -Install и uninstall-runner.ps1
с изменением регистрации/задач не запускать. Existing opt-in scripts не являются
доказательством готового runner. Windows restart/current-user auth/Drive access
не проверены сквозным запуском. Рабочую студию нельзя использовать как checkout:
clean: true предназначен для отдельного disposable runner checkout.

## Offline CI gate

Проверенный security CI workflow использует GitHub-hosted windows-latest,
не credentialed локальный runner; общий job timeout — 30 минут. Полный checker:

```powershell
.venv-core/Scripts/python.exe -X utf8 scripts/check_multi_ai_v2.py --output C:/AI_VIDEO_02_EVIDENCE/NEW_RUN
.venv-core/Scripts/python.exe -m compileall -q app scripts tests
git diff --check
```

Output — новый каталог за пределами репозитория. Checker создаёт эксклюзивный
UTC+UUID child, logs/checks.json/manifest.json с SHA-256, commit/tree/source binding,
elapsed time и точными статусами. V2 имеет bounded timeout 420 секунд; остальные
группы — 180. Timeout остаётся exit 124 / failed, тесты не пропускаются автоматически.
Локальный dirty run не считается независимым clean-final CI proof. Нормальная
продолжительность полного прогона оценивается по evidence; сумма всех предельных
лимитов при патологических зависаниях не гарантирует завершение до job deadline.

## Нереализованные соединения и сохранность

Antigravity AGE-GATED / DISABLED: до подтверждения официальных возрастных и других
требований не выполнять login, model smoke, auth migration или обходные маршруты.
Story/Visual — логические роли; автоматической ротации нет. Платные API требуют
отдельного решения. Новые production/remote manifest подсистемы не создаются.

Drive — хранилище; unattended runner auth/root и сквозной cloud upload/readback
NOT_IMPLEMENTED. Проверенная локальная sync-копия не доказывает cloud upload.
Доставка задания до выключенной машины ограничена существующим GitHub workflow;
отдельное долговечное хранение ещё не полученного runner задания NOT_IMPLEMENTED.

Старый MVP остаётся через studio.cmd. Ruslan, WAV/cache, Timeline/MLT, originals,
ручные правки и registry сохраняются. Не применять reset --hard/clean; не
восстанавливать SQLite поверх активного WAL. Текущий этап заканчивается push
feature-ветки и независимым review, без merge, runner registration или production.
