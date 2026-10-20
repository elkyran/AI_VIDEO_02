# GitHub CI и безопасный self-hosted runner

Multi-AI v2 использует два разных контура с разным уровнем доверия.

## 1. Независимый GitHub-hosted CI

Workflow `.github/workflows/ci-multi-ai-v2.yml` работает на GitHub-hosted
`windows-latest`. Эта машина не имеет доступа к локальным профилям Codex/Gemini,
Google Drive, рабочим SQLite, моделям, WAV или исходным видео.

CI проверяет:

- точный checkout commit SHA;
- pinned GitHub Actions;
- синтаксис и политику workflow;
- синтаксис PowerShell;
- отсутствие drift у JSON Schema;
- Python compileall;
- Multi-AI v2 unit tests;
- старые regression-группы;
- Git/secret export scan;
- чистоту checkout после тестов.

CI разрешён для Pull Request и рабочих веток, потому что выполняется на изолированной
машине GitHub без пользовательских provider credentials.

Зелёный CI не доказывает вход моделей, просмотр MP4, TTS, рендер, удалённую
синхронизацию Drive или художественное качество.

## 2. Credentialed self-hosted runner

Workflow `.github/workflows/new-video.yml` предназначен для официального GitHub
runner на Windows-ноутбуке владельца. В будущем этот компьютер сможет видеть
локальные авторизации Codex, Antigravity и Drive, поэтому его правила строже.

Обязательные ограничения:

1. Репозиторий приватный.
2. Запуск только вручную через `workflow_dispatch`.
3. Исполнение только из `refs/heads/main`.
4. Feature-ветки и Pull Request не исполняются на credentialed runner.
5. Checkout использует точный `${{ github.sha }}`.
6. Checkout выполняется с `clean: true` и `persist-credentials: false`.
7. После checkout проверяются `HEAD` и `git status --porcelain`.
8. Runner использует отдельный рабочий каталог, не рабочую папку студии.
9. Не добавлять PR/fork/schedule trigger к credentialed workflow.
10. Production option пока отсутствует: workflow выполняет только fake-run.

Workflow должен находиться в default branch, прежде чем GitHub покажет кнопку
ручного запуска. До независимого review и merge регистрировать runner нельзя.

## 3. Что запрещено хранить в GitHub

- `auth.json`, OAuth tokens, cookies и API keys;
- `.env` с реальными путями или секретами;
- `.codex`, `.gemini`, `.claude`;
- активные SQLite/WAL;
- модели, WAV, MP4 и исходные сезоны;
- полные необработанные client logs.

## 4. Ветки и ответственность

- `main` — только проверенный код.
- `feature/multi-ai-coordinator-v2` — Python-ядро Codex.
- `assistant/review-fix-ci-runner-safety` — CI и runner security ChatGPT.

ChatGPT владеет:

- `.github/workflows/ci-multi-ai-v2.yml`;
- `.github/workflows/new-video.yml`;
- `scripts/ci_validate_multi_ai_v2.py`;
- `docs/GITHUB_CI_AND_RUNNER.md`;
- `development/multi_ai_v2/COLLABORATION.md`.

Codex не редактирует эти файлы. ChatGPT не редактирует Python-ядро Codex.

## 5. Порядок интеграции

1. Codex завершает core fix pack в feature-ветке.
2. GitHub-hosted CI проверяет Python-ветку.
3. Draft PR ChatGPT проверяет merge-состояние двух дорожек.
4. Красные проверки исправляются в ветке владельца соответствующего файла.
5. После зелёного CI выполняется повторный независимый review.
6. ChatGPT-дорожка сливается только в feature-ветку.
7. Финальный PR из feature в `main` создаётся отдельно.
8. Только после merge в `main` регистрируется self-hosted runner.
9. Первый self-hosted запуск — только fake.
10. Live providers и production включаются отдельными пакетами.

## 6. Текущая известная CI-находка

Первый GitHub-hosted запуск обнаружил Windows portability defect в старом тесте
`ProductionBridgeTests.test_prepared_synthesis_uses_existing_script_and_profile_without_enqueuing`:
mock ожидает короткий путь `RUNNER~1`, а production code передаёт эквивалентный
длинный путь `runneradmin`. Исправление принадлежит Codex: сравнивать канонические
`Path.resolve()` значения, не пропускать и не ослаблять тест.

## 7. Реакция на ошибки

- Красный GitHub-hosted CI блокирует merge.
- Неизвестный или грязный checkout блокирует self-hosted run.
- Запуск не из `main` блокируется до checkout.
- Отсутствие `STUDIO_PYTHON` или `LOCAL_RUN_ROOT` блокирует run.
- Production остаётся заблокированным до реальных Story, Visual, video-review,
  Drive и Scene-to-production probes.

Нельзя превращать ошибку в warning, отключать regression test или ослаблять assertion
без доказанной причины.
