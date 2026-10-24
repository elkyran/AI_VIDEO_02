# PROVIDERS-01: два worker, только offline

## Проверка официального маршрута — 2026-10-24

По текущему [официальному FAQ Google](https://www.antigravity.google/docs/faq/#can-i-use-antigravity-cli-headless-mode-in-third-party-coding-agents-or-automate-account-switching),
headless CLI допускает прямые скрипты/CI, но wrapping agy внутри сторонних coding
agents/applications и account rotation не поддерживаются consumer plans.
Для сторонних coding agents Google рекомендует Gemini Enterprise или AI Studio API key.
Обычный вход в Google и наличие подписки не доказывают допустимость данного adapter.
Существующая offline реализация не активируется автоматически. Владелец подтвердил:
Story/Visual пока выключены; работа только в GitHub/облаке, без локальных файлов;
облачного Windows-host пока нет. [Результаты проверки Drive](../reports/multi_ai_v2/cloud_storage_20261024/REPORT.md).
Исторические срезы ниже сохранены как история, а не live approval.

SYSTEM-CLOSURE-02: тот же fixed two-account transport теперь принимает CanonBible,
StoryOutline, Scene и SegmentVisuals wrapper; реальные accepted dependencies
попадают в context. Исполнение/границы описаны в SYSTEM_CLOSURE_02.md. Codex
director подключается отдельной factory; он не заменяет Story/Visual. Live доступ
по-прежнему BLOCKED_ELIGIBILITY; новые offline тесты не проверяют аккаунты.

RELEASE-01 реализует ранее запрошенную host routing границу в существующем
Coordinator: immutable role→worker→provider→profile→identity/auth_generation.
Конфигурация `worker_routes` явная; без неё legacy поведение сохраняется.
Разные личности одного provider допускают две активные задачи при общем cloud=3;
одна личность под разными профилями/generations делит reservation. Local-heavy=1.
Неоднозначный/исчезнувший pinned route блокируется без selected-profile fallback.
Requests/completions, poll/manual/adapter/file recovery и replay используют одну
проверку host binding и V2ResultGate. Исторические accepted artifacts сохраняются
при новой generation, незавершённый старый ответ отклоняется.

Quota/auth pause scoped к identity/account; выбранный профиль/модель не меняются.
Сохраняются причина и bounded resume_after. Явный resume(worker_id) не обходит
неистёкший deadline и не будит другой аккаунт. Outbox чужого аккаунта не блокирует
независимый worker. Повреждённый reservation request удерживает lock fail-closed.

Опциональные SYSTEM simulation stages используют exact SyntheticTransport для
Story Scene и отдельного metadata-only Visual Review, затем существующие host
handlers. Scene должна точно соответствовать host-approved input (это не rewrite);
canonical blocked Review останавливает продолжение. Новые interfaces не включают
live Antigravity и не снимают запрет настоящих клиентов.

Ниже сохранено описание PROVIDERS-01. Его пункт NEEDS_ARCHITECT_REVIEW описывал
тогдашний разрыв; данный пакет реализует/тестирует ограниченный diff и ожидает
единый независимый review. Live Canon/outline/SegmentVisuals acquisition всё ещё
UNVERIFIED/не реализованы текущим transport; frames/MP4 capability не предполагается.

Цель владельца: Antigravity Story/account binding A и Antigravity Visual/account
binding B, Codex Production, существующий движок и один Python Coordinator.
Текущий срез: `DUAL_WORKER_OFFLINE_READY_LIVE_BLOCKED_ELIGIBILITY`.
Это не подключение двух аккаунтов. Actual identity/auth/quota/media UNVERIFIED,
accounts_connected=0, live requests NOT_RUN, autonomous readiness=false.

Google указывает возрастное требование 18+ для Antigravity:
[официальные требования](https://support.google.com/googleone/answer/16105039).
Сохраняется AGE-GATED / DISABLED. Этот документ не содержит разрешения или способа
включить доступ. Никаких clients/login/auth migration/model requests/paid credits.

| Slot | Роль и данные | Отдельные границы |
|---|---|---|
| antigravity_story | Sources/version, facts vs assumptions/Canon, causal outline, canonical Scene/narration; ограниченная правка своего текста | Host binding A, generation, auth namespace, session namespace |
| antigravity_visual | Разрешённые media/source ranges и назначение кадров, повторы/соответствие/ритм, canonical Review только в пределах доказательств | Host binding B, generation, auth namespace, session namespace |
| Codex Production / engine | Существующие EditPlan/Piper/WAV/MLT/render/technical QA | Без смены Ruslan, музыки, стиля; не запускались в пакете |
| Python Coordinator | Единственная очередь, бюджет, dependencies, gate/CAS/checkpoints | ИИ не получают код, credentials или production Registry |

## Реализованные контракты

`DualWorkerConfig` расширяет подходящие provider структуры: два fixed worker ID,
role, synthetic profile_id, host-owned identity_binding_ref, identity_hash и
auth_generation (форма существующего account binding), auth/session namespaces.
Все профили здесь синтетические. Настоящий AccountManager и пользовательские
профили не вызываются. Разные profile_id без разных identity hashes отклоняются;
auth boundary и session boundary проверяются отдельно. Это проверка fixture
изоляции, а не доказательство реальных разных личностей/credentials/квот.
quota_remaining=null; quota evidence UNVERIFIED.

`SessionBinding` включает worker/binding generation, весь canonical Task, run,
task, attempt и SHA-256 ограниченного creative context. Session/conversation key
выводится из полного binding. Нет общего latest conversation или общей истории.
Requested model закреплён host Task; returned_model=null. `init.model` не повышает
его до подтверждённого фактически возвращённого model.

`DualWorkerBridge` принимает только exact data-only `SyntheticTransport`, не
произвольный клиент с is_simulated=True. Сохраняется live gate; старый wrapper
также блокирует Story/Visual при provider=codex или synthetic flag. Продуктовый
autonomous pipeline остаётся заблокирован. Provider bridge не заменяет workers
Codex/FakeAdapter и не претендует на выполнение live требования.

## Протокол и доказательства

Pure parser реализует строгий поднабор опубликованного
[headless JSON/stream-JSON](https://www.antigravity.google/docs/cli/headless/).
Structured output, точная canonical schema и сериализованный response должны
совпадать. JSON duplicates/nonfinite/truncation, unknown fields, failed/nonterminal
status, несколько terminals, чужая conversation и expired/oversized output
отклоняются. NDJSON допускает один init, безопасные text/checkpoint steps и один
terminal; любые tools/subagents/permission changes отклоняются. Это намеренно
консервативный offline subset, не заявление о всех возможностях клиента.
Клиент не запускается. Документация не доказывает установленную версию/допуск.

stdout не смешивается со stderr. Любые stderr diagnostics блокируют completion;
при наличии attempt directory сохраняется отдельный redacted bounded log с
truncated. В ошибках validation не печатаются исходный model payload/секреты.
Host создаёт canonical Result, ArtifactStore hashes и per-attempt receipt.
Модель не может присвоить себе identity/eligibility/VERIFIED/delivery.
Конечные output/context/deadline/attempt budgets; model/paid budget=0.
Timeout/cancel/error не SUCCESS, не сбрасывают ранее accepted artifacts.

Text, image/frame, sequence, full-video — отдельные actual UNVERIFIED capabilities.
Документированный stream text input не доказывает отсутствие иных интерфейсов или
поддержку нашего MP4 review. MP4 path не delivery; кадры не просмотр полного видео.
Canonical Review и существующие VideoEvidence/HostReviewDelivery не изменены;
нет нового receipt формата для media и нет реального media evidence. Offline
bridge принимает только metadata_only, не выдаёт full-video receipt или readiness.

## Существующая очередь, пауза, recovery

Один legacy `gemini` queue profile — только synthetic transport reservation,
не аккаунт Story или Visual. Роутер выбирает строго fixed slot из host Task,
никогда из model text. Это последовательная очередь, не параллельные live клиенты.
На synthetic quota/auth/eligibility возвращается существующая task-level
`waiting_access` через failure kind=permissions; исходная причина сохраняется в
provider_failure_kind и code=WAITING_QUOTA/WAITING_AUTH/BLOCKED_ELIGIBILITY.
Это позволяет не менять provider-wide fake profile state и не активировать
rotation. Ядро требует явный resume и сохраняет попытки; квота не replenished.
Несвязанный Visual проходит, зависимый от непринятого Story не проходит.

Нет второго scheduler/Registry/acceptance table. Recovery читает request.json и
host receipt внутри существующей attempt directory; снова проверяет binding,
generation, canonical payload/artifacts. Если receipt отсутствует — interrupted,
без повторной отправки. Неверный receipt — finite failed, не вечная reservation.
Идемпотентность acceptance и immutable completion проверяет прежний V2ResultGate.

Fixture: оригинальная короткая Scene, отдельный Visual continuity Review, одна
адресная правка line_one, Scene v2. Меняются только её текст и версия. Revision
обязана ссылаться на accepted Review/open issue/точный prior Scene hash; второй
цикл и изменение других полей отклоняются. Контексты — отдельные ограниченные
accepted dependencies, без Story self-score или инструкции Visual согласиться.
Это не обещание статистической независимости моделей или улучшения качества.
Полный accepted fixture proof печатается в external contracts.log тестом.

## Оставшиеся review gates

NEEDS_ARCHITECT_REVIEW: live два профиля одного провайдера не поддерживаются
нынешними selected_profiles/provider reservations. Предложение для отдельного
review: host-owned fixed role→worker→profile mapping в Coordinator.enqueue/tick/
_dispatch/_failure; account generation в request/host gate, task-scoped quota,
без rotation и без Codex alias. Не менять core под видом offline адаптера.

NEEDS_CHATGPT_CHANGE: CI immutable evidence policy — v2/contracts=420s, others=180s;
generated_schemas должен включать прежние семь canonical моделей, HostRunConfig
и новый DualWorkerConfig (`dual_worker_config.schema.json`), всего девять.
Strict schemas и drift checks сохранить. Local standalone CI validator ранее
блокировался отсутствующим PyYAML; зависимости здесь не устанавливались.
Независимое одобрение SYSTEM-01 или этого пакета пока не заявлено.

Read-only doctor: `python -B -m app.v2_provider_bridge`; config plan/doctor/status
включают `providers`. OFFLINE_TESTED обозначает regression suite, не текущий
probe. Нет auto-enable; eligibility/account/auth/media/live implementation и
независимый review остаются blockers. После merge также потребуется отдельное
разрешение владельца на каждый допустимый live шаг, если условия допуска выполнены.
