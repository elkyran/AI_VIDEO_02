# GitHub CI и безопасный self-hosted runner

Multi-AI v2 использует два разных контура с разным уровнем доверия.

## 1. Независимый GitHub-hosted CI

Workflow `.github/workflows/ci-multi-ai-v2.yml` работает на GitHub-hosted
`windows-latest`. Эта машина не имеет доступа к локальным профилям Codex/Gemini,
Google Drive, рабочим SQLite, моделям, WAV или исходным видео.

CI проверяет:

- checkout точного event/temporary-merge commit SHA;
- pinned GitHub Actions;
- синтаксис и политику workflow;
- синтаксис PowerShell;
- отсутствие drift у JSON Schema;
- canonical `scripts/check_multi_ai_v2.py`;
- Multi-AI v2 unit tests и старые regression-группы;
- точный список обязательных групп, минимум 411 тестов текущего baseline,
  не более двух известных окруженческих skips, сохранённые лимиты v2=420
  секунд / остальные=180 и отсутствие группы с неуспешным exit code;
- Git/secret export scan;
- `git diff --check` и отсутствие tracked/untracked изменений после тестов;
- immutable evidence на чистом commit;
- совпадение evidence `git_commit_sha` и `git_tree_sha` с checkout;
- `dirty=false` и `source_unchanged_during_checks=true`;
- SHA-256 и размер каждого файла из evidence manifest;
- отсутствие model requests, paid API и production operations.

Evidence загружается отдельным GitHub Artifact даже при красном regression run.
Отсутствие evidence directory, непрошедший `manifest.status`/`checks.status`
или отсутствие загружаемых evidence-файлов теперь являются ошибками CI,
а не предупреждением. Наличие `passed` проверяется отдельно от exit code checker.
`upload-artifact` закреплён полным commit SHA; срок хранения — 14 дней. Полные
логи, `checks.json` и `manifest.json` сохраняются вне Git-репозитория, поэтому
проверка не делает checkout dirty. Job Summary показывает проверенный commit/tree,
статус evidence, число тестов и SHA-256 manifest.

CI выполняется для Pull Request в `main` и `feature/multi-ai-coordinator-v2`,
а также для push уже объединённого кода в `main`/feature. Изолированная ChatGPT
safety-ветка сама по себе намеренно не запускает полный core checker: в ней нет
новых коммитов Codex, поэтому такой push-run тестирует заведомо устаревший core.
Полная совместимость проверяется PR temporary-merge, а не status отдельной lane.
Это не отключение регрессионных тестов: PR checker продолжает запускать их полностью
и блокирует интеграцию при любой ошибке.

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
   Policy validator запрещает дополнительные jobs/actions, непроверенные
   шаги и любые ослабления точного `private && main` условия.
   Теперь проверяются **точные PowerShell-команды**, последовательность и
   параметры каждого шага: одной проверки имени недостаточно. Добавленные
   команды, step env, hooks, изменение checkout/ref или таймаута отвергаются.
   Негативные тесты валидатора проверяют эти отказы без доступа к runner.
   Также заблокированы новые top-level `env`, `defaults` и прочие ключи
   workflow: они иначе могли бы изменить исполнение даже при неизменных
   названиях и телах шагов.
5. Checkout использует точный `${{ github.sha }}`.
6. Checkout выполняется с `clean: true` и `persist-credentials: false`.
7. После checkout проверяются `HEAD` и все tracked/untracked изменения.
8. `STUDIO_PYTHON` должен указывать на существующий локальный Python.
9. `LOCAL_RUN_ROOT` обязателен и должен быть отдельным локальным каталогом вне
   репозитория, production workspace и sync-папок.
10. Перед dispatch запускается именно
    `healthcheck_multi_ai_v2.py --mode fake --root $env:LOCAL_RUN_ROOT`.
11. Runner использует отдельный checkout, а состояние run хранится в
    `LOCAL_RUN_ROOT`, не в рабочей папке студии.
12. После fake-run повторно проверяется, что checkout остался чистым.
13. Не добавлять PR/fork/schedule trigger к credentialed workflow.
14. Production option пока отсутствует: workflow выполняет только fake-run.
15. Workflow не читает GitHub Secrets и не загружает auth, SQLite, media или raw logs.

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

1. Codex завершает Review Fix Pack 2 в feature-ветке.
2. Draft PR ChatGPT проверяет temporary merge обеих дорожек.
3. Красные проверки исправляются владельцем соответствующего файла.
4. После зелёного CI выполняется повторный независимый review.
5. ChatGPT-дорожка сливается только в feature-ветку.
6. Финальный PR из feature в `main` создаётся отдельно.
7. Финальный PR повторно проходит clean GitHub-hosted CI.
8. Только после merge в `main` регистрируется self-hosted runner.
9. Первый self-hosted запуск — только fake.
10. Live providers и production включаются отдельными пакетами.

## 6. Исторические CI-находки и текущий gate

На предыдущем Core Fix Pack четыре Windows-portability regression блокировали merge.
Review Fix Pack 2 исправил их, и GitHub-hosted PR run `39460359638` прошёл
на temporary merge `370ff860960dce96e632a3f2d010760fdb84bda4`.
Это доказательство только Fix Pack 2; текущий Fix Pack 3 на feature HEAD
`c4e69d0556169cdc394c97b5f75560d00947df61` ещё требует нового
merge-state CI и независимой проверки до любого merge.

Исторические Windows regressions:

1. `tests/test_production_bridge.py` сравнивает короткий `RUNNER~1` и длинный
   `runneradmin` путь как разные значения mock-вызова.
2. `tests/test_ai_accounts.py` сравнивает `GEMINI_CLI_HOME` строкой вместо
   канонического пути.
3. `tests/test_gemini_free_route.py` сравнивает API-home без `Path.resolve()`.
4. Mock worker в `tests/test_speech_review.py` записывает исходный DLL path, тогда
   как настоящий `transcribe_audio.validate_runtime_options()` сохраняет resolved path.

Исправления выполнены Codex в Review Fix Pack 2 и подтверждены предыдущим
merge-state CI. Не возвращать эти регрессии и не ослаблять assertions.

Изменение base branch PR само по себе не гарантирует новый PR-run: при каждом
следующем integration gate требуется новый фактический temporary merge commit,
зелёный GitHub-hosted CI и проверенная immutable evidence на exact merge SHA.

## 7. Реакция на ошибки

- Красный GitHub-hosted CI блокирует merge.
- Невалидный evidence envelope блокирует merge даже при зелёных unit tests.
- Неизвестный или грязный checkout блокирует self-hosted run.
- Запуск не из `main` блокируется до checkout.
- Отсутствие `STUDIO_PYTHON` или `LOCAL_RUN_ROOT` блокирует run.
- Ошибка fake healthcheck блокирует dispatch.
- Production остаётся заблокированным до реальных Story, Visual, video-review,
  Drive и Scene-to-production probes.

Нельзя превращать ошибку в warning, отключать regression test или ослаблять assertion
без доказанной причины.
