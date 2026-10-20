# Multi-AI V2: параллельная работа ChatGPT и Codex

Этот файл фиксирует непересекающиеся зоны работы, чтобы два исполнителя могли
работать параллельно без потери изменений и конкурирующих источников истины.

## Общая база

- Базовая ветка: `feature/multi-ai-coordinator-v2`.
- Защищённый baseline: `baseline-before-multi-ai-v2`.
- `main` не изменяется до независимого review и зелёного GitHub-hosted CI.
- Production, TTS, рендер, live Gemini и self-hosted runner пока не запускаются.

## Дорожка A — ChatGPT / GitHub safety

Ветка: `assistant/review-fix-ci-runner-safety`.

ChatGPT владеет только следующими файлами в этом пакете:

- `.github/workflows/ci-multi-ai-v2.yml`;
- `.github/workflows/new-video.yml`;
- `development/multi_ai_v2/COLLABORATION.md`.

Задачи:

1. Независимый GitHub-hosted offline CI.
2. Проверка Python, PowerShell, YAML, JSON Schema и существующих regression-групп.
3. Защита self-hosted workflow от запуска кода из feature-веток.
4. Чистый точный checkout без сохранения GitHub credentials.
5. Никаких изменений Coordinator, contracts, evidence, storage или production.

## Дорожка B — Codex / core correctness

Codex продолжает только функциональный review-fix пакет:

- `app/multi_ai_v2.py`;
- `app/v2_contracts.py`;
- `app/v2_storage.py`;
- `scripts/check_multi_ai_v2.py`;
- `scripts/probe_multi_ai_v2.py`;
- `scripts/healthcheck_multi_ai_v2.py`;
- связанные unit tests и документация, кроме файлов дорожки A.

Задачи Codex:

1. Неизменяемые evidence runs с commit/tree/dirty binding.
2. Один активный run на `LOCAL_RUN_ROOT` либо run-scoped управление.
3. Trusted boundary для full-video review.
4. Связь artifact path с `run_id`.
5. Строгие CLI exit codes.
6. Режимный healthcheck: fake/production.
7. Переносимый безопасный Drive-copy fallback.

Codex не редактирует файлы дорожки A. Если изменение там необходимо, оно
описывается в отчёте как предложение, но не применяется.

## Правила синхронизации

1. Перед каждым push проверять remote head своей ветки.
2. Не force-push, не reset --hard, не clean.
3. Не создавать два решения одной и той же проблемы в разных модулях.
4. Не менять существующий Coordinator и Registry без адресного доказательства.
5. После каждого пакета — отдельный commit и короткий отчёт.
6. ChatGPT PR направляется в `feature/multi-ai-coordinator-v2`, не в `main`.
7. Codex сначала подтягивает merged дорожку A, затем публикует свои исправления.
8. Финальный merge в `main` возможен только после зелёного GitHub-hosted CI и
   повторного независимого review.

## Текущие запреты

До следующего разрешения владельца нельзя:

- устанавливать или подключать `agy`;
- подключать второй Gemini;
- регистрировать self-hosted runner;
- включать production option удалённого workflow;
- запускать TTS, рендер или video QA;
- сливать feature-ветку в `main`.
