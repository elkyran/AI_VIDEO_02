# Multi-AI V2: разделение работы и release gate

## Цель и сохранность

Сохраняем существующий AI_VIDEO_02: Ruslan, отдельные WAV/cache, Timeline/MLT,
JobManager, Registry, исходные материалы и ручные правки. Не создаём второй
scheduler, AccountManager, production Registry или renderer.

Порядок владельца: завершить функциональные соединения, затем отдельным
заданием один содержательный выпуск, затем улучшение голоса/музыки/стиля.
Тестовые данные не доказывают живую работу исполнителей или качество видео.

## Дорожка A — ChatGPT / независимый CI и review

Текущая ветка: `assistant/providers-01-ci-sync` (PR #9 в feature).
Предыдущая `assistant/review-fix-ci-runner-safety` исторически интегрирована
через PR #1; её старый HEAD не использовать как базу новых core изменений.

ChatGPT владеет:

- `.github/workflows/ci-multi-ai-v2.yml`;
- `.github/workflows/new-video.yml`;
- `scripts/ci_validate_multi_ai_v2.py`;
- `docs/GITHUB_CI_AND_RUNNER.md`;
- `development/multi_ai_v2/COLLABORATION.md`.

Обязанности: читать настоящий diff; согласовать девять canonical schemas и
ограничения времени; проверять полный GitHub-hosted Windows CI на точном
временном merge SHA; проверять manifest/file hashes, tree SHA, dirty=false и
source_unchanged_during_checks=true. Не ослаблять ошибки, test coverage,
ограничения self-hosted workflow или offline-происхождение результатов.

## Дорожка B — Codex / функциональный Python

Ветка: `feature/multi-ai-coordinator-v2`.
Codex меняет только файлы, разрешённые конкретным заданием владельца.
Пять файлов дорожки A не меняет. Требования к ним сообщает как
`NEEDS_CHATGPT_CHANGE`, не дублирует патч и не выполняет merge/cherry-pick.

RELEASE-01 опубликован на `64905d00016c406d437d9f5b2c0eb2c1c60f7752`.
Его авторский отчёт: 596 тестов, 595 passed, 1 прежний skip.
Это локальное свидетельство, не независимое одобрение.
Функциональное приращение: host-owned worker routes, reservations/recovery,
Windows process-tree cleanup, operator CLI, bounded Drive host interface и
source-only поставка. Реальная автономность не заявляется.

## Текущий gate — RELEASE-01

Срез перед review (после каждого push проверять заново):

- feature: `64905d00016c406d437d9f5b2c0eb2c1c60f7752`;
- main: `7a1e4058df09dbb66448a3624e0250129eaf7aa0`;
- PR #9: CI-sync -> feature; PR #6: feature -> main; оба отдельные gates.

Следующий независимый запуск должен включать текущий release core и изменения
CI-sync вместе. Старый CI на PROVIDERS-01 не является доказательством RELEASE-01.
Ожидаемый текущий тестовый состав: v2=109, contracts=174, queue=50, adapters=16,
mai05=108, panel=12, legacy=127; всего 596 до последующих адресных дополнений.
Проверить фактический набор/числа и причины skips в скачанном evidence, а не
ограничиваться минимальным порогом workflow. Новые пропуски без причины запрещены.
Бюджеты v2/contracts=420s, остальные группы=180s; genuine timeout остаётся failure.
Девять schemas включают HostRunConfig и DualWorkerConfig; полный drift-check строгий.

CI выполняется на GitHub-hosted Windows, без provider credentials, реального
TTS/render, Drive или пользовательского self-hosted runner. Существующие fake19
и offline Scene->MLT->release smokes должны пройти без ложного production claim.

Прежние неуспешные CI и их artifacts сохраняются. Их нельзя объявлять зелёными
задним числом. После любого нового core push актуальность approval проверяется
по commit/tree и полному набору изменений.

## Правила синхронизации и интеграции

1. Перед изменением и push проверить актуальные refs и рабочее дерево.
2. Никаких force-push, reset --hard, git clean, rebase опубликованной истории,
   слепого git add -A, изменения baseline tags или удаления старых evidence.
3. Независимые файлы не переписывать и не заменять параллельной реализацией.
4. Красный CI запрещает merge; не повторять запуск до случайного успеха.
5. PR #9 интегрируется только после review и зелёного точного merge-state CI;
   feature -> main проверяется отдельно через PR #6.
6. Ранее данное разрешение владельца на интеграцию не отменяет CI/security gates.
   Codex не выполняет merge самостоятельно; main не менять прямым commit.
7. Self-hosted workflow остаётся private/main-only/manual/fake-only с exact SHA,
   clean checkout и persist-credentials=false. Его protected команды не меняются.
8. Сведения для review хранятся в небольших отчётах; большие evidence и медиа
   не коммитятся. Активные SQLite/WAL остаются вне Git/Drive/OneDrive.

## Доступы и readiness

Два Antigravity работника Story/Visual с раздельными привязками остаются целевым
требованием. Тестовые identities и раздельные reservations не доказывают
реальные аккаунты, квоты, медиа-возможности или право использования сервиса.
Текущий live доступ остаётся BLOCKED_ELIGIBILITY / DISABLED. Не выполнять
login, перенос credentials, ротацию аккаунтов, обход ограничений или молчаливый
fallback Story/Visual на Codex. Не выводить autonomous_ready=true по mock-тестам.

Drive host interface и локальная копия не доказывают cloud upload; до реального
разрешённого remote readback uploaded=false, remote_sync=UNVERIFIED.
Runner registration, Windows services/users, внешние расходы и публикация не
включаются этим review. Нельзя начинать новый выпуск или менять качество голоса.
Исторический реальный PILOT-01 остаётся доказательством только своего scope.
