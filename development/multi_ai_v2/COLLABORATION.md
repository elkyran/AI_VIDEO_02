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
- `scripts/ci_validate_multi_ai_v2.py`;
- `docs/GITHUB_CI_AND_RUNNER.md`;
- `development/multi_ai_v2/COLLABORATION.md`.

Задачи:

1. Независимый GitHub-hosted offline CI.
2. Проверка Python, PowerShell, workflow policy, JSON Schema и regression-групп.
3. Защита self-hosted workflow от запуска кода из feature-веток.
4. Чистый точный checkout без сохранения GitHub credentials.
5. Диагностические CI-логи и immutable evidence artifact для точной передачи дефектов Codex.
6. Проверка clean evidence: `dirty=false`, exact `GITHUB_SHA`, zero model/API/production operations.
7. Никаких изменений Coordinator, contracts, evidence implementation, storage или production.

## Дорожка B — Codex / core correctness

Codex владеет функциональными Python-границами Multi-AI v2, связанными тестами и
документацией, кроме файлов дорожки A.

Review Fix Pack 2 отправлен в `feature/multi-ai-coordinator-v2`; ожидаемый HEAD:
`68c1d2a3953bdfabd509e71ec004d1cb50cafc30`.

Он включает:

1. Исправление четырёх Windows portability regressions без ослабления assertions.
2. Trusted Result acceptance с проверкой task/run/input/schema/provenance/artifact/CAS.
3. Immutable, expiring и revocable TrustedVideoProbe с host-owned receipt.
4. Единые exit codes для remote dispatcher.
5. Обновлённый `docs/VIDEO_REVIEW_PROTOCOL.md`.

До результата merge-state CI Codex не начинает новый пакет и не изменяет файлы
дорожки A.

## Правила синхронизации

1. Перед каждым push проверять remote head своей ветки.
2. Не force-push, не reset --hard, не clean.
3. Не создавать два решения одной и той же проблемы в разных модулях.
4. Не менять существующий Coordinator и Registry без адресного доказательства.
5. После каждого пакета — отдельный commit и короткий отчёт.
6. ChatGPT PR направляется в `feature/multi-ai-coordinator-v2`, не в `main`.
7. PR-CI проверяет временный merge обеих дорожек; красный статус блокирует merge.
8. Владелец файла исправляет найденный дефект в своей ветке.
9. CI evidence хранится GitHub Artifact, а не коммитится как самоссылочный release-proof.
10. Финальный merge в `main` возможен только после зелёного CI и повторного review.

## Текущий review gate

Review Fix Pack 2 опубликован пятью последовательными коммитами поверх
`89cbc7ef3b9c35de34b8c99c9ea1ba353630ee78`. Draft PR №1 должен проверить новый
временный merge commit с базой `68c1d2a3953bdfabd509e71ec004d1cb50cafc30`.

Пока этот merge-state CI не завершён успешно:

- PR остаётся draft;
- merge запрещён;
- новый функциональный пакет Codex не начинается;
- provider, runner и production остаются выключенными.

## Текущие запреты

До следующего разрешения владельца нельзя:

- устанавливать или подключать `agy`;
- подключать второй Gemini;
- регистрировать self-hosted runner;
- включать production option удалённого workflow;
- запускать TTS, рендер или video QA;
- сливать feature-ветку в `main`.
