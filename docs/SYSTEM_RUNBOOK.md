# SYSTEM-01: общий запуск

SYSTEM-CLOSURE-02 добавляет executable source-only queue acquisition и host-only
director/Drive wiring. Текущий scope, команды, source ZIP и оставшиеся software
gaps: [SYSTEM_CLOSURE_02.md](SYSTEM_CLOSURE_02.md). Ниже описан совместимый
approved_input/legacy synthetic-handoff путь RELEASE-01.

RELEASE-01: обычный вход Windows — `studio-v2.cmd --config PATH doctor`, затем
явные `--execute-system --authorize start` только для разрешённой конфигурации.
[START_HERE](START_HERE.md) содержит короткую последовательность. Старый studio.cmd
сохранён. Launcher перед запуском проверяет Python/config и plan/doctor; параметры
передаются argv, содержимое источников не исполняется. Нового web/server stack нет.

`examples/release_01/autonomous_brief.json` — Brief + owner source inventory без
готового сценария; live acquisition блокируется до расходов. Разрешённый
`--execute-system --authorize prepare` сохраняет immutable план и точные blockers,
не создавая Registry/производственного разрешения. `approved_input.json` показывает
host-approved входы; локальный runtime владелец указывает отдельно.

`simulation_approved.json` и `synthetic_inputs.json` — явно синтетический пример:

```powershell
studio-v2.cmd --config examples/release_01/simulation_approved.json --synthetic-inputs examples/release_01/synthetic_inputs.json doctor
# После выбора нового root вне repo/sync и отдельного разрешения на simulation:
studio-v2.cmd --config examples/release_01/simulation_approved.json --synthetic-inputs examples/release_01/synthetic_inputs.json --execute-system --authorize start
```

Без explicit fixtures worker stages не используют FakeAdapter как fallback.
Brief simulation handoff принимает строгие host fixture inputs/results, сохраняет
исходный план, reconciliation receipt, synthetic bundle и execution_config в
исходном root; исполнение использует его отдельный `execution/`. Resume принимает
тот же bundle и materialized config. Это rehearsal, не реализация live Canon /
story outline / visual selection. Модель не формирует runtime/permissions.

Status/inspect/report читают существующий Registry через mode=ro, не запускают
recovery и доступны при исчезнувших originals/runtime. Они явно возвращают
`NOT_REVALIDATED_READ_ONLY_OBSERVATION`; recorded package не означает новое QA.
Pause/Stop используют прежние control/attempt states под тем же OS lock, сохраняя
accepted. Stop не утверждает завершение процесса; adapter recovery должен
подтвердить cancellation. Синхронная операция держит bounded lock: Stop сообщает
COORDINATOR_RUNNING и конкретно требует дождаться checkpoint/deadline.

Drive: embedding host передаёт exact `DriveHostContext(DriveHostResolver(...),
private_session_root, authorized=True)` в SystemPipeline. Resolver содержит только
явно разрешённые credential_ref/folder/run/version callbacks, не JSON credentials.
Session URI сохраняется current-user Windows DPAPI вне run/input/repo/sync.
CLI сам не обнаруживает credentials; без matching callback — WAITING_AUTH.
Construction/doctor не вызывают supplier/сеть. Remote upload/readback в RELEASE-01
не разрешались; uploaded=false, remote_sync=UNVERIFIED.

Основной вход — `python -m app.multi_ai_v2`. Исторические create/prepare/status/
resume/inspect-artifacts/report и `scripts/dispatch_multi_ai_v2.py` сохранены.
Новый путь выбирается явным `--config`; без него действует прежний протокол.
Ни одна команда ниже не была разрешением создать следующий выпуск в SYSTEM-01.

PROVIDERS-01: два обязательных Antigravity worker с разными account bindings
остаются live DISABLED / BLOCKED_ELIGIBILITY. Общий autonomous запуск блокируется
прежде производства. Этот пакет не разрешает новый выпуск, запуск клиентов,
логин, подключение аккаунтов или иной маршрут доступа.

Read-only статус offline реализации без config, Registry и проверки аккаунтов:

```powershell
.venv-core/Scripts/python.exe -B -X utf8 -m app.v2_provider_bridge
```

Существующие config plan/doctor/status дополнены полем `providers`. Оно отражает
offline regression scope, а не текущую проверку клиента/аккаунта. Подробнее:
[DUAL_PROVIDER_INTEGRATION](DUAL_PROVIDER_INTEGRATION.md). Проверки без клиентов:

```powershell
.venv-core/Scripts/python.exe -X utf8 -m unittest -v tests.test_v2_provider_protocol tests.test_v2_system_delivery
```

## Контракт входов

`HostRunConfig` и `config/multi_ai_v2_schemas/host_run_config.schema.json` описывают
host configuration отдельно от существующего Brief. Brief.production_mode не
переписывается автоматически. Режимы:

| mode | Brief.production_mode | Что исполняется |
|---|---|---|
| simulation | fake | Малые явно simulated входы, настоящие queue/gate/CLI; без движка и внешних вызовов |
| approved_input | production | Утверждённые владельцем canon/story/Scenes/media; существующий локальный движок при scoped permit |
| autonomous | production | Сейчас BLOCKED_UNSUPPORTED до производства: Story/Visual Antigravity AGE-GATED / DISABLED; Codex не заменяет их |

Это не автоматическое исследование источников. CanonBible сохраняет facts,
assumptions/divergences, unconfirmed/owner_verified и байтовые citations.
Данные источников не исполняются. Разрешены только перечисленные text/JSON и
image/video файлы с hash, size, version, rights. Нужны все visual mappings;
произвольных/random картинок и новой музыки нет. Русский язык и прежний профиль
Ruslan обязательны. Identifier остаётся canonical ASCII до 96 знаков; Unicode
поддерживается в тексте и именах разрешённых файлов, не расширяет Identifier.

`root` — новый абсолютный локальный каталог вне repo/studio/sync, не содержащий
репозиторий. `input_root` — отдельное дерево оригиналов; относительное значение
разрешается относительно config-файла. Junction/symlink/reparse отклоняются.
Engine находится только в root/engine, Coordinator — root/coordinator.
В активный workspace/studio ничего не копируется и не записывается.

`runtime` принимает только существующие engine paths/settings. Для будущего
approved_input владелец указывает установленные tts_python/tts_model/melt_path/
ffmpeg_path/ffprobe_path и **все эффективные настройки текущего профиля**:
tts_engine=piper, tts_normalization_scope, tts_* voice settings,
pronunciation_lexicon/terms, video_encoder/render_threads. Не копировать studio
runtime автоматически. Перед job вызывается существующий check_runtime;
модель/config, голос и настройки не исправляются молча. Отдельное поле renderer
явно признаёт существующий MLT libx264/CRF20/fast/4 threads; legacy NVENC профиль
не меняется. Недоступные инструменты — blocker, установки нет.

## Команды

Сначала скопировать `examples/system_01/host_config.template.json` в выбранное
место, исправить input_root на каталог примера и root на **новое** место.
Пример — только simulation, не пилот и не содержательный выпуск.

```powershell
$python = '.venv-core/Scripts/python.exe'
$config = 'C:/AI_VIDEO_02_inputs/approved_host_config.json'
& $python -X utf8 -m app.multi_ai_v2 --config $config plan
& $python -X utf8 -m app.multi_ai_v2 --config $config doctor
# Следующие команды — справка для отдельного разрешённого выполнения:
& $python -X utf8 -m app.multi_ai_v2 --config $config --execute-system --authorize prepare
& $python -X utf8 -m app.multi_ai_v2 --config $config --execute-system run
& $python -X utf8 -m app.multi_ai_v2 --config $config --execute-system status
& $python -X utf8 -m app.multi_ai_v2 --config $config --execute-system pause
& $python -X utf8 -m app.multi_ai_v2 --config $config --execute-system stop
& $python -X utf8 -m app.multi_ai_v2 --config $config --execute-system resume
& $python -X utf8 -m app.multi_ai_v2 --config $config --execute-system inspect-artifacts
& $python -X utf8 -m app.multi_ai_v2 --config $config --execute-system report
& $python -X utf8 -m app.multi_ai_v2 --config $config --execute-system approve --review C:/AI_VIDEO_02_inputs/owner_review.json
& $python -X utf8 -m app.multi_ai_v2 --config $config --execute-system deliver
```

Plan/doctor не создают root, Registry, jobs, provider sessions или write probe.
Отдельная проверка записи:
`python scripts/healthcheck_multi_ai_v2.py --root NEW_ROOT --write-probe`.
Она пишет/удаляет только собственный маленький TemporaryDirectory. Обычный
healthcheck теперь read-only; содержимое studio runtime/auth не читается.

Prepare/create с --config сохраняют разрешение и reconciled tasks, без исполнения
этапов. Run/resume исполняют только его операции. `--authorize` нужен для первой
версии/новых входов, `--execute-system` — для каждого исполнения/доступа к host
Coordinator. Model Result не является разрешением. Host разрешение привязано к
root/run/key/config version/inputs/full profile/operations/budgets. Старый ключ с
другим input конфликтует. Разрешения неизменны в authorizations/<sha256>.json.

План использует произвольное количество сцен и сегментов:
sources/canon → approved story → Scene → candidate → WAV → measured EditPlan →
compile → scene render → QA → assembly accepted scene picture + separate WAV →
compile/render/QA → portable. Узлы имеют детерминированные bounded IDs и полные
mapping; строки-префиксы для восстановления исходных ID не разбираются.
Все стадии принимаются через существующие Coordinator/V2ResultGate/immutable
completion/CAS; ai_events хранит provisional action outputs, не вторую очередь.
Файл без принятого completion не становится accepted.

Повтор использует тот же task/attempt/checkpoint или существующий JobManager key.
WAV импортируются только из завершённого exact synthesize job с проверкой текста,
полных settings/cache keys/model/profile/config/hash/duration. MLT строит прежний
ProductionBridge. Измерение WAV допускает только trim доказанных trailing-zero
PCM до прежней 100ms сетки. Один утверждённый still удерживается на измеренную
речь; video trim/multi-shot timing при несовпадении требует нового host approval.
Effects/transforms остаются в capability boundary прежнего compiler.

Синхронные engine steps ограничены существующими tool/job deadlines. Pause/stop
применяются между наблюдаемыми стадиями; отдельная CLI не перехватывает Coordinator
OS lock. При Ctrl+C существующий worker job сохраняется, повтор не создаёт его
заново. Немедленная отмена уже запущенного render здесь не обещается; старый
JobManager остаётся владельцем процессов. Полные ошибки сохраняются в failures/;
краткий blocker содержит log_ref и признак truncated.

Output size/reserve проверяются до и после host action, до acceptance. Deadline
`budget.seconds` ограничивает одну команду run/resume и её tool waits; проверенные
этапы не повторяются при следующей команде. Существующий protected renderer требует
полного свободного 180s slot; меньший оставшийся budget даёт TIME_BUDGET до render.
QA tool timeout и ожидание synthesize ограничены оставшимся временем. Уже
поставленный worker job при client timeout сохраняется для явного recovery.
Drive request reservations сохраняются в ai_events, включая failed calls и
restart; исчерпание бюджета не приводит к бесконечным retries.

Для targeted fix создать config version+1 с точным parent_sha256, новыми версиями
изменённых Scenes/mappings и явным --authorize. Неизменённые узлы сохраняют старое
разрешение и bytes; final assembly инвалидируется по зависимостям. Previous
versions/partial outputs не удаляются. Review использует только canonical Review,
exact render SHA/version. Owner review — explicit human_review, не model video
evidence; technical QA отдельно, automatic creative approval отсутствует. Один
review_id нельзя переиспользовать с другим решением. До 32 явных issues образуют
план правок без автоматического исполнения backlog.

## Delivery и внешние границы

Portable сохраняет относительную структуру MLT, media, отдельные WAV, subtitles,
plans/manifests; DB/WAL/auth/models/fonts/произвольные logs не включаются.
Partial package с другими bytes блокируется и сохраняется. Локальная готовность
не означает uploaded. Для обычной local sync-копии статус остаётся
COPIED_VERIFIED / remote_sync=UNVERIFIED.

`app.v2_drive.GoogleDriveHTTP` реализует официальный Drive v3 resumable REST:
[upload protocol](https://developers.google.com/workspace/drive/api/guides/manage-uploads),
[File identity/checksums](https://developers.google.com/workspace/drive/api/reference/rest/v3/files),
[error categories](https://developers.google.com/workspace/drive/api/guides/handle-errors).
Это не авторизация. Host передаёт token_supplier и защищённый persistent
session_store, отдельно разрешает network write и ровно owner folder. Ни token,
ни resumable session URI не попадают в manifests/receipts/stdout. Нет login,
переключения аккаунтов, credentials reader или внешних URL redirects.

После отдельной проверки host integration может вызвать
`pipeline.deliver(transport)` с существующим официальным authorization binding.
Это ставит delivery в ту же queue/gate. Обычный CLI deliver пока WAITING_AUTH:
защищённый resolver credential_ref не настроен и не выдуман. Это явный оставшийся
blocker unattended cloud execution. `GoogleDriveHTTP` получает max_requests и
timeout из утверждённого host budget. Транспорт подтверждает remote ID/folder/
operation key/size и SHA-256 либо **отдельно MD5**. Возобновление использует server
offset и прежний remote ID; конфликтующие объекты не перезаписываются. Сетевой
сбой не инвалидирует local release или voice/render. Mock receipt никогда не
даёт uploaded=true; реализация IMPLEMENTED_NOT_LIVE_TESTED.

ProviderBridge оборачивает существующий adapter start/poll/cancel; тестовый
transport проверяет structured output/media capabilities, timeout и pause/resume.
Новый launcher/account manager отсутствует. Story/Visual не направляются в Codex,
API или другой аккаунт. Реальный автономный path останавливается до производства.

## Headless protocol

Stdout — один JSON; status включает completed/total/current_stage/blocker,
root/results, safe_repeat, owner_action_required, execution mode и честный review.
Exit codes: 0 planned/completed technical/simulation/recorded owner review;
2 in progress; 3 auth; 4 quota; 5 unsupported; 6 retryable interruption;
7 failed final; 9 invalid config/error; 10 stopped. Exit 0 не означает creative
acceptance. Runner registration, service/task setup и workflow expansion отдельно
разрешаются владельцем. Private/main-only/manual/fake-only workflow не изменён.
