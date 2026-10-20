# Локальный и удалённый запуск v2

Режим fake работает на временных данных без модели. Live production заблокирован.
Выберите отдельный локальный каталог вне OneDrive/Google Drive; .env.example —
только шаблон, он не загружается автоматически. Не передавайте секреты в brief.

```powershell
$env:STUDIO_PYTHON = (Resolve-Path .venv-core/Scripts/python.exe).Path
$env:LOCAL_RUN_ROOT = 'C:/AI_VIDEO_02_LOCAL/v2'
./scripts/healthcheck.ps1
& $env:STUDIO_PYTHON -m app.multi_ai_v2 create --brief examples/multi_ai_v2_brief.json --key first_fixture
& $env:STUDIO_PYTHON -m app.multi_ai_v2 status --run-id RUN_ID_FROM_CREATE
& $env:STUDIO_PYTHON -m app.multi_ai_v2 advance --run-id RUN_ID_FROM_CREATE --ticks 60
& $env:STUDIO_PYTHON -m app.multi_ai_v2 pause --run-id RUN_ID_FROM_CREATE
& $env:STUDIO_PYTHON -m app.multi_ai_v2 stop --run-id RUN_ID_FROM_CREATE
& $env:STUDIO_PYTHON -m app.multi_ai_v2 manifest --run-id RUN_ID_FROM_CREATE
```

`advance` — явное продолжение после остановки/перезапуска. Pause/stop управляют
очередью выделенного v2 workspace, а не существующим production. Несколько run
в одном workspace разделяют одну очередь. Отдельного живого worker disable v2 пока
нет: остановите очередь; Gemini live в этом срезе всегда выключены. Manifest
содержит результат симуляции, ссылки на реальные файлы JSON и video_qa=NOT_RUN.

Workflow `.github/workflows/new-video.yml` запускается только workflow_dispatch
в приватном репозитории на labels self-hosted, Windows, ai-video-02.
Он передаёт inputs как JSON через env; нет shell-интерполяции темы, clean=false,
credentials checkout не сохраняются. Все production заявки пока fail-closed.
Сначала после ревью включить workflow в default branch: GitHub требует наличия
workflow_dispatch файла на default branch. До этого workflow в feature-ветке —
подготовленный код, кнопка Run workflow может отсутствовать.
Затем Actions → AI_VIDEO_02 request → Run workflow → выбрать ветку и fake.
Concurrency queue:max сохраняет до 100 ожидающих runs вместо замены одного pending.
Ключ зависит от GitHub run ID; rerun не создаёт дубликаты принятых задач.
В summary — run ID, simulation flag, status, hash; Drive URL пока null.

Runner count на момент проверки — 0, dispatch не выполнен. Для реального запуска
владелец регистрирует официальный Windows runner приватного репозитория вне
рабочей студии, label ai-video-02, от своего Windows-пользователя. Не принимать
PR/fork задания на машине с авторизацией. Не использовать checkout рабочей студии.

Предпочтителен Task Scheduler при входе пользователя: интерактивный пользователь
видит свои официальные credential stores. Windows service под LocalSystem может
получить другой профиль/Drive; этот вариант не выбран. Исследовано по модели
доступа, живой тест перезапуска Windows не выполнен. Подготовлены opt-in scripts:
install-runner-task.ps1 -RunnerDirectory PATH (dry-run), затем -Install;
uninstall-runner.ps1 -RemoveLogonTask. Скрипты не скачивают runner и не меняют
регистрацию GitHub. Они не запускались с Install.

Пока ноутбук выключен, self-hosted job ожидает доступный runner, но GitHub отменяет
её после 24 часов в очереди. После обрыва уже запущенной job нужно rerun с
тем же ключом: локальный SQLite сохраняет принятые результаты.
Сохранение ещё не полученного ноутбуком задания сверх лимита GitHub пока
NOT_IMPLEMENTED: для этого понадобится отдельный долговечный remote manifest.

Откат к MVP: `studio.cmd --help`; новая ветка не меняла движок. Сравнение с
baseline-before-multi-ai-v2 через git diff; никогда reset --hard/clean и никогда
не восстанавливать БД поверх работающего WAL. Runner и новый режим можно
не запускать; исходный тег фиксирует код, локальные backups сохраняют SQLite.

Источники: [ручной запуск](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow),
[лимиты очереди](https://docs.github.com/en/actions/reference/limits).
