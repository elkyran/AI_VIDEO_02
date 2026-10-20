# GitHub CI и безопасный self-hosted runner

Этот документ разделяет два принципиально разных контура.

## 1. Независимый CI

Workflow `.github/workflows/ci-multi-ai-v2.yml` работает на GitHub-hosted
`windows-latest`. Он не имеет доступа к локальным профилям Codex/Gemini,
Google Drive, рабочим SQLite, моделям, WAV или исходным видео.

CI проверяет:

- точный checkout commit SHA;
- синтаксис PowerShell;
- синтаксис и защитные свойства workflow;
- отсутствие drift у JSON Schema;
- Python compileall;
- новые тесты Multi-AI v2;
- старые regression-группы;
- эвристический Git/secret export scan;
- отсутствие изменений checkout после тестов.

CI разрешён на push рабочих веток и Pull Request в `main`, поскольку выполняется
на изолированной машине GitHub без пользовательских credentials.

Зелёный CI означает только, что проверенный код и unit/regression tests прошли.
Он не доказывает вход моделей, просмотр MP4, TTS, рендер, синхронизацию Drive или
художественное качество.

## 2. Credentialed self-hosted runner

Workflow `.github/workflows/new-video.yml` рассчитан на отдельный официальный
GitHub runner на Windows-ноутбуке владельца. Этот компьютер в будущем может видеть
локальные авторизации Codex, Antigravity и Drive, поэтому его модель доверия строже.

Обязательные правила:

1. Репозиторий остаётся приватным.
2. Workflow запускается только вручную через `workflow_dispatch`.
3. Код исполняется только из `refs/heads/main`.
4. Feature-ветки и Pull Request никогда не исполняются на credentialed runner.
5. Checkout использует точный `${{ github.sha }}`.
6. Checkout выполняется с `clean: true` и `persist-credentials: false`.
7. После checkout проверяются `HEAD` и `git status --porcelain`.
8. Runner использует отдельный рабочий каталог, не рабочую папку студии.
9. На runner не регистрируются workflow от fork или внешних PR.
10. Production пока отсутствует в inputs; workflow выполняет только fake-run.

Файл workflow должен находиться в default branch, прежде чем GitHub покажет
кнопку ручного запуска. До независимого review и merge регистрировать runner не
нужно.

## 3. Что запрещено хранить в GitHub

- `auth.json`, OAuth tokens, cookies и API keys;
- `.env` с реальными путями/секретами;
- локальные credential homes `.codex`, `.gemini`, `.claude`;
- активные SQLite/WAL;
- модели;
- WAV, MP4 и исходные сезоны;
- полные необработанные client logs.

## 4. Рабочая схема веток

- `main` — только проверенный код.
- `feature/multi-ai-coordinator-v2` — Python-ядро Codex.
- `assistant/multi-ai-v2-ci-safety` — CI и runner security ChatGPT.

Исполнители не редактируют файлы друг друга. После завершения обеих веток:

1. проверить commits Codex;
2. дождаться зелёного GitHub-hosted CI;
3. перенести commits CI/security в интеграционную ветку;
4. открыть Draft Pull Request в `main`;
5. повторно проверить полный diff;
6. только после review выполнить merge;
7. только после merge зарегистрировать self-hosted runner;
8. сначала проверить fake workflow;
9. live providers и production включать отдельными пакетами.

## 5. Реакция на ошибки

- Красный GitHub-hosted CI блокирует merge.
- Неизвестный или грязный checkout блокирует self-hosted run.
- Попытка запуска не из `main` блокируется до checkout.
- Отсутствие `STUDIO_PYTHON` или `LOCAL_RUN_ROOT` блокирует run.
- Production остаётся заблокированным, пока Story, Visual, video review, Drive и
  Scene-to-production bridge не подтверждены реальными probes.

Не исправлять красный CI отключением теста, ослаблением assertion или переводом
ошибки в предупреждение без доказанной причины.

## 6. Текущий статус

На момент добавления этого документа:

- Python-ядро находится в отдельной feature-ветке;
- live Gemini Story и Visual не подтверждены;
- self-hosted runner не зарегистрирован;
- workflow не тестировался на ноутбуке;
- production и видеопилот не запускались;
- GitHub-hosted CI является первой независимой проверкой.
