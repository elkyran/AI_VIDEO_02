# AI_VIDEO_02 — AUDIT-01

Дата проверки: 22 октября 2026, UTC+5. Repository: elkyran/AI_VIDEO_02.
Аудированный feature commit: **64905d00016c406d437d9f5b2c0eb2c1c60f7752**.
Статус аудита: **AUDIT_01_COMPLETE_READY_FOR_ARCHITECT_PLAN**.
Это завершённый аудит с явно перечисленными неизвестными, а не готовность продукта.

## 1. Фактический результат

**Полного маршрута «новая тема → два Antigravity → автоматический Codex → видео → Drive» сейчас нет.** Первая программная точка обрыва — получение Canon из источников: узел плана есть, исполняемого acquisition-обработчика и принимаемого worker output нет. Сам live-запуск дополнительно останавливается BLOCKED_ELIGIBILITY до запросов и production. [E02–E04, E12]

Практически доступны: read-only plan/doctor; simulation существующей очереди на полностью заданных synthetic inputs; наблюдение ранее подготовленного exact run. Общий approved_input соединён с настоящим движком, но требует заранее утверждённых Canon, story, всех Scenes/narration, media/mappings, profile-consistent runtime и разрешений. Новый общий реальный прогон не проверен; это не режим «только тема». [E02, E05, E12]

Реализованы полезные основания: Coordinator/result gate/CAS, worker routes/reservations/generation, bounded parsers, разрешённый inventory, существующие WAV/cache, measured EditPlan/MLT/QA/portable, HTTP Drive и защищённые sessions. Их не нужно заменять новой студией. Story/Visual transport остаётся exact SyntheticTransport; SYSTEM production — deterministic Python, а не автоматический Codex-worker. [E04–E05]

Независимый CI **красный**: run39798692447 attempt1, v2 timeout420.031s/exit124. Локальный concurrency тест прошёл3/3; завершённый в CI prefix82 тестов прошёл локально за205.375s. Точная причина hosted timeout не доказана. Исторические596 tests не являются новым зелёным CI. [E06–E08]

Два дополнительных существенных findings: source ZIP не содержит необходимых synthesize_script.py/render_project.py; generic synthesize не вызывает существующий JobManager.resume для умершего running/failed job. Первый — проверенный дефект комплектации; второй — сильный статический кандидат дефекта recovery, без реального crash-воспроизведения. [E05, E09]

Старый PILOT сохранён:14 файлов,6 WAV, MP4 и относительный MLT byte-identical оригинальному portable. Новый выпуск, синтез и рендер в аудите не выполнялись. [E10]

## 2. Git/GitHub, ограничения и before/after

| Объект | Проверенное значение |
|---|---|
| Local branch / HEAD до и после | feature/multi-ai-coordinator-v2 / 64905d00016c406d437d9f5b2c0eb2c1c60f7752 |
| Feature tree | 087df53d8ec7e85bdf9e3cf1b8401609e6400bce |
| origin/feature, повторный ls-remote | 64905d00016c406d437d9f5b2c0eb2c1c60f7752 |
| origin/main, повторный ls-remote | 7a1e4058df09dbb66448a3624e0250129eaf7aa0 |
| origin/assistant/providers-01-ci-sync | c8b4a252d064c8a67512a8777c5b8db9d5e54920 |
| PR9 CI→feature | open, Draft, merged=false, mergeable_state=unstable |
| PR6 feature→main | open, Draft, merged=false, mergeable_state=unstable |
| Tracked/index diff до и после | пуст; diff --check и cached --check exit0 |
| Untracked до и после | те же4 старых review_fix_pack_2/3 local/full_local evidence trees; сохранены |

git fetch origin обновил только remote-tracking CI lane c6d48c8→c8b4a25 и FETCH_HEAD. Рабочая ветка/HEAD/index не изменялись. Повторный ls-remote подтвердил все три remote SHA. Один первоначальный read-only ls-remote не смог разрешить github.com в sandbox; разрешённый повтор вне сетевого ограничения завершился exit0. [E01]

GitHub PR9 API возвращает в base.sha старое1a65eb2; это противоречит актуальному ref и **не использовано** для определения проверенного checkout. Git commit endpoint и manifest независимо подтверждают temporary merge:
ecf9302a1d140a4d3b5b60c571c38065bf53ae85,
tree b2e7ee182ae929a1ab4dd2a27a9237d47ef0ab6e,
parents **64905d0 + c8b4a25**. run.head_sha=c8b4a25 — head CI-ветки, не checkout SHA. [E06]

Сравнение feature→CI merge показывает только3 изменённых файла: workflow, CI validator, COLLABORATION.md. Из source manifest только CI validator отличается после учёта точных LF/CRLF вариантов; product code/tests соответствуют feature. Raw source-manifest hashes разных Windows checkout нельзя механически приравнивать. [E06]

Не читались credentials/OAuth/cookies/account storage; не выполнялись agy, auth probes, provider APIs, runner registration, installs, service/task/user changes. Студия, SQLite, cache и пользовательские media не изменялись. Все новые файлы — во внешнем уникальном AUDIT-01 root. Полный checker: **NOT_RUN**. [E14]

## 3. Требования → реальный исполнитель → evidence

Обозначения: implementation I=IMPLEMENTED, P=PARTIAL, N=NOT_IMPLEMENTED.
Доказательства S=STATIC_ONLY, T=TESTED_SYNTHETIC, L=TESTED_LOCAL_REAL, CI=INDEPENDENT_CI.
CI означает проверенные offline assertions конкретной группы, а не живой доступ.
Availability в JSON хранится отдельно; ниже blocker описан предметно.

| ID/этап | Вход → выход | Caller / исполнитель; CLI | Реализация / evidence / пробел |
|---|---|---|---|
| R01 Brief/run | Brief+HostRunConfig → plan/authority/run | launcher→system_main→SystemPermit→RunCoordinator; да | I/T; нужен host JSON, roots/hash/authority; тема одна недостаточна |
| R02 sources | Source inventory → integrity report | verify_inputs→verify_sources; Python; да | P/CI; size/hash/rights/edition проверены; текста в context нет |
| R03 Canon | sources → CanonBible | acquisition_graph только план; helper reconcile_inputs принимает готовый Canon | P/CI для supplied Canon; worker executor/output не зарегистрирован |
| R04 outline | Canon+Brief → outline/story | план schema=story_outline; actual story — строка | P/S; нет typed outline result и исполнителя |
| R05 N Scenes | outline → Scene/narration | approved graph→worker_scene/host scene | P/CI; worker Scene должен совпасть с готовым input, acquisition expansion нет |
| R06 visuals | inventory+Scene → SegmentVisuals | OfflineProductionBridge.candidate→v2_visuals.verify_mapping; Visual worker→Review | P/CI; trims/rights/IDs есть; выбор mapping и media transport отсутствуют |
| R07 narration | Scene/candidate → отдельные WAV/cache receipts | execute_engine→synthesize→JobManager→operations; да | I/CI; реальный caller есть, новая общая TTS проверка NOT_RUN; runtime/authority нужны |
| R08 timing/EditPlan | WAV+mapping → measured EditPlan | measured_edit; Python; да | I/S для общего handler; соседние compiler/cache helpers CI-tested; timing guards не выполнялись в этом аудите |
| R09 MLT/render | EditPlan → editable MLT/preview/final | ProductionBridge.compile/render; да | I/CI; реальный движок; ZIP неполон; новый общий render UNVERIFIED |
| R10 technical QA | render+MLT/timing → QA | MeasuredEngine.qa; да | I/S для общего handler; CI offline_qa тестирует другой metadata helper с null media metrics; actual QA не выполнялась |
| R11 creative Review | media+receipts → canonical Review | worker_review metadata_only; owner_review manual JSON | P/CI; full-video model review NOT_RUN, клиентская media capability неизвестна |
| R12 targeted fix | Review/revision → changed downstream | owner_review→fix plan; новый host config→graph | P/T; automatic_execution=false; автоматический fix-worker не подключён |
| R13 assembly/portable | scene outputs+WAV → MP4/relative MLT/manifest | execute_engine assembly→compile/render/QA→portable; да | I/S для общего assembly; related helpers CI, PILOT bytes L; новый общий пакет UNVERIFIED |
| R14 Drive | accepted package+host auth → remote receipt | deliver→deliver_package/HTTP; CLI без host context | P/CI; HTTP/store/readback есть; launcher glue и actual remote proof отсутствуют |
| R15 recovery/status | checkpoints+exact config → replay/control | unified gate/recover, observe(ro), request_control | P/T; accepted stages сохраняются; dead production worker recovery не соединён |

Основные source anchors: v2_run_config:108–305; v2_pipeline:278–365,383–615,664–812; v2_sources:27–121; v2_release_plan:20–114; v2_visuals:17–79; production_bridge:139–305. [E02–E05]

## 4. Обычный запуск и режимы

Фактическая схема вызовов:

```text
studio-v2.cmd (требует checkout/.venv-core)
 → scripts/launch_multi_ai_v2.py --config HOST.json COMMAND
 → load_config / HostRunConfig
 → для start/resume/review/deliver: plan + doctor
 → explicit --execute-system; --authorize для новых inputs
 → v2_pipeline.system_main (прямой вызов из launcher)
    plan/doctor → plan + readiness (+ storage), без run DB
    status/inspect/report → observe(mode=ro), только recorded facts
    stop/pause → shared-lock queue control (записывает состояние)
    blocked prepare → immutable blocked plan, без production permit
    run/resume → SystemPermit → existing Coordinator/gate
      simulation: StageAdapter Notes, files={}
      approved_input: execute_engine → existing production
    approve → canonical manual owner Review
    deliver → нет injected DriveHostContext → WAITING_AUTH
```

Отдельный вход python -m app.multi_ai_v2 --config ... также dispatch-ит system_main. Без --config у него остаётся legacy fake/pilot route. Его19-stage smoke не является доказательством нового acquisition.

| Режим | Что вводить вручную | Текущий результат |
|---|---|---|
| simulation / ApprovedInputs | Canon/story/Scenes/narration/media/mappings, source hashes, profile, roots | После отдельного opt-in может SIMULATION_SUCCEEDED; записывает queue/receipts, медиа не создаёт |
| simulation / worker routes | всё выше плюс explicit Story/Visual fixture bundle для каждой Scene | Без bundle SYNTHETIC_RESULTS_REQUIRED; с bundle — rehearsal готовых ответов |
| simulation / BriefInputs | Brief+sources/media+scene_count | только план; missing acquisition capabilities и fixtures блокируют исполнение |
| approved_input | все creative inputs simulated=false; runtime/settings/operations/budget | общий настоящий engine route; пример без runtime возвращает BLOCKED_UNSUPPORTED/exit5 |
| autonomous | Brief+inventory+host routes/authority metadata | BLOCKED_UNSUPPORTED/exit5: acquisition, eligibility, media, Codex/live/runtime gates |

Свежие измерения обычного launcher [E12]:
simulation plan/doctor exit0, PLANNED; approved_input plan/doctor exit5, «Explicit isolated engine runtime configuration is required»; autonomous plan/doctor exit5 с CANON_CAPABILITY_UNVERIFIED, STORY_OUTLINE_CAPABILITY_UNVERIFIED, VISUAL_MAPPING_CAPABILITY_UNVERIFIED, BLOCKED_ELIGIBILITY, IMAGE_DELIVERY_UNVERIFIED, HOST_ROUTES_REQUIRED и unverified Codex director. Ни один run root не создан.
status на новом неподготовленном config: exit9, SYSTEM_PERMISSION_REQUIRED. Это ожидаемо: status требует exact прежнюю authority+DB и не создаёт их.

Минимальный Brief как данные, без сценария выпуска:

```json
{
  "topic": "ТЕМА ВЛАДЕЛЬЦА",
  "language": "ru",
  "target_minutes": 1,
  "style": "serious",
  "source_profile": "owner_supplied",
  "production_mode": "production",
  "auto_fix_cycles": 1,
  "output_profile": "ruslan-local-narration"
}
```

Это только часть HostRunConfig. Обязательные дополнительные host поля: run_id/key, mode, отдельные root/input_root, profile_sha256, inputs с version и непустыми sources (edition/path/SHA/size/rights), operations (допустим []). Host version имеет default1, requested_scene_count default1, media default[]. Для сегодняшнего реального исполнения ещё вручную нужны CanonBible, story, все Scenes+segments и SegmentVisuals с необходимым media inventory, точный runtime/settings, разрешённые операции и budgets. Review — отдельный canonical JSON. Drive — host callback, не строка credential_ref.

SYSTEM принимает русский/current Ruslan profile. target_minutes/style/auto_fix_cycles присутствуют в Brief и плане, но общий production не генерирует/подгоняет историю по ним; он исполняет переданные Scenes/timing. Не следует рекламировать произвольный язык или автоматическую длительность. [E02–E03]

## 5. Story A, Visual B и автоматический Codex

| Компонент | Реально реализовано | Неподтверждённое/отсутствующее |
|---|---|---|
| Story A | fixed role/profile/worker/identity generation, request/session binding, bounded parser Scene, gate/recovery | live launcher/transport, настоящий аккаунт/сессия/model/quota, Canon/outline acquisition |
| Visual B | отдельный route/reservation, canonical Review parsing, metadata acceptance | mapping acquisition; получение/понимание image/MP4/audio; live credentials/session/model/quota |
| Codex runtime | legacy LiveCLIAdapter существует отдельно; canonical proposal/gate primitives | ordinary SYSTEM не подключает автоматического Codex-worker; real adapter map содержит local:StageAdapter |
| Python engine | deterministic StageAdapter вызывает существующие operations/JobManager/ProductionBridge | исполнение Python нельзя считать участием runtime Codex |

Решающие excerpts: DualWorkerBridge проверяет «type(transport) is SyntheticTransport»; SessionBinding допускает Story:{scene}, Visual:{review}; SYSTEM worker_scene сравнивается с уже approved node, worker_review требует metadata_only. [E04:W05–W07; E02:ACQ-10]

Очередь действительно выполняет host-owned binding. Reservation=provider:identity_hash; generation/profile alias не создаёт новую reservation. Limits: cloud3, account/worker/profile1, local-heavy1. Разные synthetic identities имеют2 active attempts/handles одного provider; одна identity под разными профилями сериализуется. Corrupt active request удерживает reservation консервативно. Quota/auth pauses account-scoped, deadline сохраняется; rotation/fallback отсутствуют. Historical accepted artifacts переживают новую generation; незавершённые stale ответы отвергаются. [E04:W01–W04]

Это проверяют actual scheduler tests RouteSchedulerTests: two_accounts_one_provider…, same_identity_aliases…, new_generation_alias…, corrupt_active_request…, quota_deadline…, quota_pauses…, auth_pause…, generation_change…, swapped_result…, accepted_history…, crash_after_completion…, tampered_request…. Они вошли в passed contracts174 нового CI. Их HeldSynthetic доказывает программные ветки, а не двух живых клиентов. [E04, E06]

Poll/manual/persisted completion/adapter recovery проходят validate_worker_route и V2ResultGate; dependency replay повторно проверяет immutable completion/artifacts в historical scope. Это нужно сохранять для новых capabilities.

Task.capability — Identifier, не центральный исполняемый allowlist. Реальное ограничение распределено: Coordinator.enqueue/editorial.validate_result/ai_cli_runtime — scene,edit_plan,review,note; protocol — только Scene/Review фиксированных roles; schema exporter —9 top-level schemas; gate/host caller должны понимать результат. Canon и SegmentVisuals вложены в HostRunConfig, но не зарегистрированы как worker results. Outline пока вообще не model. Добавление только имени planned schema ничего не исполняет. [E03:ACQ-08–09, E04:W06]

Нельзя вывести независимость credentials, сессий, моделей или квоты из разных names/hashes. «fixture_...» conversation_id не является настоящей беседой. Live свойства установленного клиента и его официальный поддерживаемый интерфейс этим аудитом не устанавливались; документация протокола в repo не доказывает их. После lawful eligibility потребуются отдельно разрешённые bounded request/receipt: точная версия/интерфейс, distinct host identity/session bindings, returned model, account-scoped ошибки и media hash/coverage. Токены, cookies или документы личности не требуются в отчёте/чате. [E04]

## 6. Acquisition: конкретный отсутствующий код

1. **Source extraction.** verify_sources проверяет структуру/size/hash/rights и возвращает canon_truth=UNVERIFIED. Текущий context задаёт source_text=brief.topic. Нужен bounded extractor разрешённых text/JSON bytes с edition/citation offsets, без исполнения содержимого.
2. **Canon result.** CanonBible уже хранит facts/assumptions/divergences и source+edition+byte citations. Нет enqueue/transport/validator/gate-caller для получения этого Canon как принятого worker artifact.
3. **Outline.** Есть только planned schema='story_outline' и story:string. Нужен небольшой bounded canonical outline contract, связанный с Brief/Canon/scene count/duration.
4. **Scene generation.** Схема Scene и acceptance есть. Нужен caller из accepted outline/Canon, а не требование полного равенства заранее написанному input Scene.
5. **Visual selection.** SegmentVisuals/ShotCandidate и range/rights checks есть. Нужен accepted worker output mapping из строго утверждённого inventory. Metadata Review не выбирает shots.
6. **Production expansion.** accepted(task_id) читает уже принятые production dependencies; он не превращает acquisition artifacts в новые nodes. Единственный application caller reconcile_inputs — synthetic_handoff: читает готовый bundle inputs+worker_results, затем сохраняет execution config. Нужна checkpointed host expansion из gate-accepted artifacts, не model-authored полномочный config.
7. **Media delivery/capability.** Bridge отвергает required_media. Нужны реальные bounded delivery receipts отдельно от локального пути; способность конкретного клиента к image/full MP4 остаётся внешней неизвестной.

Всё перечисленное — независимые программные пробелы даже при будущем разрешённом доступе. Live interface нельзя реализовать по догадке или снять gate ради synthetic теста. [E03–E04]

Отдельный контрактный gap: Canon допускает одинаковый fact_id в разных editions, а Scene.fact_refs/assumption_refs — list[str]; reconciliation строит sets только fact_id. Полный Canon editions сохраняет, но ссылка Scene неоднозначна. Future acquisition должен квалифицировать ссылку edition или явно закрепить edition на Scene; canonical Identifier/Review и production-core переписывать не требуется. [E03:ACQ-12]

## 7. Production, QA, правки и recovery

Реальный общий caller: execute_engine → OfflineProductionBridge.candidate → prepare_request('synthesize') → JobManager.enqueue → verify_audio → measured_edit → ProductionBridge.compile/render → MeasuredEngine.qa → assembly/final/portable. Sources/story stages честно помечены owner_supplied_not_model_research. Simulation execute возвращает Notes с files={}, не входит в этот engine. [E05:P1]

Сохраняются original segment IDs/text/order и полная original→derived ID mapping. Cache identity содержит spoken text/model SHA/полные settings; root.resolve повторяет operations.Context. verify_audio сравнивает весь request, job/run/script/manifest, WAV hash/rate/duration/settings. Короткие IDs сохраняются, длинные bounded SHA-derived; canonical Identifier не менялся. [E05:P2; CI engine_bridge/production/bulk tests]

Timing измеряется по WAV; текущая100ms alignment разрешает срезать только нулевой PCM хвост. Still может удерживаться; video/multishot mismatch даёт VISUAL_TIMING_REQUIRES_APPROVAL. Нет автоматического обрезания произносимого текста или подмены source ranges.

MLT compiler проверяет assets/rights/hash/duration/frame ranges; unsupported transitions/transforms/fades/video-audio/capabilities дают capability_gap. Новые build directories exclusive, старые/manual bytes не перезаписываются. Replay/render отклоняет изменённый MLT/media/portable; сохранность ручных правок означает отсутствие clobber, а не автоматический импорт/merge. [E05:P3]

| Событие | Меняется/инвалидируется | Сохраняется; предел доказательства |
|---|---|---|
| Одна реплика, version+1 + exact parent | changed Scene→candidate→WAV task/job→edit/render/QA; final assembly/package/review hash | остальные Scene nodes/jobs и unchanged segment cache identity; generic реальные bytes не измерены, simulation проверена |
| Только mapping/duration | candidate+downstream task IDs; final assembly | тот же spoken-text cache key может дать WAV hit; video/multishot timing нужно заново утвердить |
| Повтор после accepted stage | проверяются receipts/files/gate; accepted не публикуется второй раз | immutable outputs и ids; тесты restart boundaries synthetic |
| Потеря CLI, живой detached job | тот же job/key опрашивается | продолжение допустимо; новый общий real disconnect test не выполнялся |
| Умерший running worker / OS restart | generic synthesize повторно enqueue и ждёт pending/running до LOCAL_JOB_TIMEOUT | **нет вызова JobManager.resume**; static high-confidence gap, crash не воспроизводился |
| Ранее failed/blocked synthesize | verify_audio отклоняет non-done receipt | частичный output/error/job сохраняется, автоматического resume нет |
| Ручная правка accepted MLT | hash mismatch блокирует replay/render | bytes сохранены; нужен отдельно accepted revision/import |

Точечный repro для future recovery test: в disposable root создать injected dead-running job без TTS/process запуска; вызвать generic synthesize с конечным clock/deadline; проверить существующий resume/liveness/attempt/stale policy. Existing test_jobs проверяет helper resume, не этот общий caller. Сейчас это STATIC_DEFECT_CANDIDATE, не наблюдённая смерть production worker. [E05:P4/P6]

Technical QA проверяет streams1920×1080/30, timing, audio/video presence/start delta, full decode, black/silence/volume, MLT resources и subtitles. Phoneme alignment не измеряется; editorial/full-video creative QA остаётся pending/NOT_RUN. owner_review принимает exact hash/version canonical human_review и возвращает targeted_fix_plan, **automatic_execution=false**. Автоматического выбора/выполнения fix через runtime Codex нет. [E05:P5]

## 8. Drive, runner, ZIP и Windows

**Drive.** GoogleDriveHTTP и resumable delivery реализованы: token_supplier injection, HTTPS/no redirects, finite request/time budgets, remote identity/name/parent/key/size/checksum inspection, persisted upload IDs/offsets, restart без blind duplication. Readback здесь — metadata+checksum, не повторное скачивание всех bytes. Private sessions current-user DPAPI, bound к folder/run/version/ref/config, вне repo/sync/run/input; это не credential discovery. [E05:D1]

uploaded=true/remote_sync=VERIFIED выставляются после проверок только у несинтетического transport. MemoryDrive даёт SIMULATED_DELIVERY_VERIFIED с uploaded=false. Повтор сохранённого final receipt — прежнее verified свидетельство, не новая проверка состояния облака. Ordinary system_main не передаёт drive_host в SystemPipeline, поэтому до реального host auth он не доводит. Нужны host bootstrap/glue, выбранный владельцем authorized callback/folder/session root и отдельный реальный upload/inspect test. Не установлено, какой Drive account подключён: account APIs не вызывались и это не нужно для статического аудита.

**Runner.** actual new-video.yml в feature/CI lane: private + main-only, manual_dispatch, pinned actions/github.sha, clean checkout/persist-credentials:false, pre/postclean, serialized run, explicit STUDIO_PYTHON/isolated LOCAL_RUN_ROOT, fake-only healthcheck/dispatch. В текущем main этот workflow отсутствует. Регистрация runner **UNVERIFIED**, не «отсутствует». Dispatch запускает legacy fake RunCoordinator.create/advance, не новый --config SystemPipeline. После отдельной CI/review/main/owner isolation авторизации он проверит fake orchestration, не автономное производство. Ничего не регистрировалось. [E05:R1]

**Локальная source поставка.** ZIP integrity passed;82 inventory files на диске и в ZIP совпали по size/SHA. ZIP SHA256:
78abb19e38968195417384a077e45020b1a3a75a61eddfde9a9af44e44040ea7.
Inventory SHA256:
91878866198c1b11b5961168008c7f9da3f68de68706db8392e0431aaca75583.
Declared source commit c881e4cb5f07c11508326dc42cf657f485a50d63. [E09]

Это целый source archive, но **не production-complete distribution**:
- operations.py:277 запускает scripts/synthesize_script.py — в ZIP нет;
- ProductionBridge.render:285 импортирует scripts.render_project — в ZIP нет;
- дополнительные preview/ASR imports scripts.check_multi_ai_render/transcribe_audio тоже не включены; важны, если эти surface входят в поставку.
Это внутренний source closure gap, не намеренное исключение больших внешних binaries.

Default packaged studio-v2.cmd doctor вернул exit5/PYTHON_REQUIRED: он жёстко ищет packaged .venv-core и не использует STUDIO_PYTHON. При явном запуске packaged launcher установленным project Python doctor вернул PLANNED/exit0. Это доказывает doctor, не synthesize/render.

Models/fonts/DB/credentials/user media в source ZIP намеренно не нужны. Но model_ref привязан к installed source root/models/piper/..., а check_runtime требует этого точного пути/hash/config. Произвольный внешний tts_model не заменяет размещение автоматически. Нужна явная согласованная deployment инструкция; модели не копировались/не запускались. Historical profile provenance refs отсутствуют в ZIP: они нужны load_profile/production healthcheck, но общий pipeline использует profile_spec+check_runtime, поэтому не надо ошибочно требовать копировать studio manifests во всякую production поставку. [E05:Z1]

**PILOT сохранность.** Повторно проверены14 manifest files,6 ненулевых mono WAV22050Hz и относительные MLT resources; все копии равны оригиналу. MP4 SHA:
4f43e8a13452e37674f0ba6d69c681138720beabe9196fde67555354284c5447.
MLT SHA:
f53e8d678490d1c528ad02ebca841bd12c6e016f80fd60062cc83d17d485790d.
Manifest SHA:
bb9ab2315e92011c28c5563707fcf8b6033fdfbcdc6721216d46440f7bee35a7.
Новый decode/render не выполнялся; прежние67.904s/1080p характеристики — историческое PILOT evidence, не свежее измерение QA. [E10]

**Windows.** Python3.11.9, Windows kernel10.0.26200, PowerShell7.6.5, Git2.55.0.windows.5, pydantic2.12.5/numpy2.4.3/mcp1.30.0. PyYAML и Pillow отсутствуют; PyYAML нужен локальному CI policy validator, Pillow не declared core dependency. Known runtime locator paths tts_python/tts_model/melt/ffmpeg/ffprobe существуют; модели/клиенты не запускались и их работоспособность не установлена. STUDIO_PYTHON не задан; batch проекта его всё равно не использует. Свободно при измерении11,031,429,120 bytes (~10.27GiB), не исторические11.85GB. [E11]

Repo находится в OneDrive: годится для source, **не для активной run SQLite**. Production run/session root должен быть вне repo/studio/sync и без reparse links; audit root таков. Чужие input originals могут читаться только по explicit allowlist; session root дополнительно вне input/run. Рабочая студия не сканировалась. Неизвестны будущий owner-selected runtime/budgets/model placement и runner checkout; безопасные paths ещё нужно выбрать, а не считать готовыми по примеру.

## 9. Свежий CI failure и покрытие

Источник: [run39798692447](https://github.com/elkyran/AI_VIDEO_02/actions/runs/39798692447), attempt1, artifact12435672020.
ZIP integrity и hashes/size **всех11 manifest-listed файлов** проверены заново.
ZIP SHA6817893d5efe099f327c7d8e6521fd52789ce76b4b402096f277d1e5537bfe4a.
Manifest SHA96940d45e9c276ffb81a5e7775ec46446d566a88034f99f07556d84894f4ad96.
dirty=false, source_unchanged_during_checks=true, recorded model/API0, production=false. [E06]

| Группа/step | Tests / skips | Seconds | Итог |
|---|---:|---:|---|
| workflow/schema policy, PowerShell | — | — | passed |
| v2 | итог отсутствует;82 individual ok | 420.031 | timeout, exit124, failed |
| contracts |174 /0 |332.812 | passed |
| queue |50 /0 |51.828 | passed |
| adapters |16 /0 |0.157 | passed |
| mai05 |108 /1 |28.343 | passed |
| panel |12 /0 |7.141 | passed |
| legacy |127 /1 |38.203 | passed |
| compile/export-history/diff |— |0.125/2.094/0.031 | passed |
| immutable envelope |— |— | failed, корректно не принимает failed checker |
| две hosted smoke |— |— | skipped после failure |

checks.json учитывает487 tests с2 skips; v2 не имеет summary и не входит в это число. Последняя строка:
test_concurrent_create_has_one_owner (…) ...
Timeout420s; not passed.
Это последний started/logged тест, а не доказанная причина timeout.

Skip1: installed Gemini schema unavailable; validator installed-client test не выполнялся.
Skip2: RealPiper requires installed local Piper на hosted runner.
Локальная среда и hosted имеют разные external capabilities; эти skips не маскировались. Полный исторический local report596=595 passed+1 skip на c881e4c заново проверен по11 hashes, но **не перезапускался**. [E08]

Два новых bounded offline diagnostics, неизменённые tests/source, external temp roots, bytecode disabled [E07]:
1. python -B -X utf8 evidence/diagnose_ci.py --run concurrent: limit90s, exit0, wall2.282s;3/3, unittest1.690s; individual0.610/0.547/0.531s.
2. та же команда --run ci_prefix: только82 завершённых CI tests в том же порядке, limit390s, exit0, wall205.375s; unittest summed204.688s. SystemTests132.204s; самый дорогой 1/2/5 scenes44.219s, revision18.609s, roots17.469s, CLI13.641s. Полного checker и третьего repro не было.

**Доказано:** общий budget исчерпан; local concurrency не зависает; в prefix много накопленных операций; hosted contracts/queue/legacy тоже заметно медленнее исторических local групп. **Гипотезы:** накопленное filesystem/SQLite/context overhead правдоподобно; hosted-specific lock/wait/deadlock не исключён. CI не записывает per-test monotonic times/stack dump, поэтому точный вклад последнего теста неизвестен. RLock сериализует q.create, SQLite timeout15s, ThreadPoolExecutor.map теста не имеет собственного timeout; общий checker ограничивает группу. Смена timeout сама по себе диагноз не устанавливает.

Следующий CI шаг принадлежит ChatGPT: bounded per-test/module times + dump before deadline, process ownership/cleanup evidence; затем конкретное исправление/разделение тяжёлого setup по измерению при сохранении всех assertions. Не объявлять failure случайным и не повышать timeout автоматически.

| Existing test / группа | Что покрывает | Не достигает |
|---|---|---|
| release_plan autonomous_brief… / distinct_briefs… | план, blocked gates, dynamic IDs | acquisition execution |
| typed_reconciliation… | supplied helper Canon/story/mappings | accepted worker artifact→graph |
| source_text_is_untrusted… | hash/inventory negative safety | настоящий source extraction |
| release_flow one_two_five… | queue/gate + injected fixture | generation Canon/outline/visuals, WAV/render |
| release_routes RouteSchedulerTests | actual reservation/identity/generation/quota gates | реальные credentials/sessions/quota |
| provider_protocol / integration recovery | exact synthetic parser/session/receipt | реальный CLI/provider launch |
| system real_cli… / restart… | настоящий subprocess, simulation | новый общий real engine/process crash |
| engine_bridge cache_prediction… / aliases | full settings/cache equivalence без TTS | новая audible WAV acceptance, общий measured_edit handler |
| compiler_preserves… / offline_qa | compiler на synthetic assets / отдельный metadata technical_qa (media metrics=null) | новый MLT render, общий MeasuredEngine.qa handler |
| system_delivery + DriveHostTests | MemoryDrive/checksum/session/DPAPI helpers | ordinary launcher host glue/remote upload |
| PILOT old artifacts | текущая byte/header/XML сохранность | новый общий рендер/новая creative QA |
| generic dead-job / source ZIP closure | существенных integration tests не найдено | recovery dead real job; lazy production scripts |

## 10. Заявления и действительность

| Claim / источник | Факт / последствие | Минимальное действие / owner |
|---|---|---|
| «Одна тема» — исходная цель и RELEASE task§5 | Brief планируется; нет acquisition execution; полный manual ApprovedInputs обязателен | source extraction+typed accepted acquisition/expansion; Codex |
| «Два ИИ» | routes/queue реализованы; workers synthetic-only | live transport отдельно после eligibility/capability; Codex+owner/external |
| «Codex Production» | deterministic Python выполняет production; runtime Codex не соединён | bounded worker/proposal/fix caller; Codex, без подмены Story/Visual |
| «Локальная поставка готова» | source ZIP hashes целы, doctor доступен с явным Python; production scripts отсутствуют | transitive closure + deployment instructions; Codex |
| «Можно пользоваться» | plan/doctor, fixtures, conditional approved-input; нет topic-only выпуска | точный режим/inputs/error guide, отдельно real acceptance |
| «Реальное видео» | PILOT действительно существует и сохранён; RELEASE новый ролик запрещал | не требовать новый фильм от прошлого пакета; general run — отдельное разрешение |
| «Drive подключён» | host interface/HTTP есть; launcher callback/remote account/readback не проверены | bootstrap + owner authorized tiny remote proof |
| «Restart работает» | accepted checkpoint replay tested; dead job recovery gap | wire existing helper + finite integration tests |
| «Все tests green» | исторический author local596 passed/skip; свежий independent run failed | текущий CI evidence выше; ChatGPT-owned diagnosis/gate |
| readiness old queue/HTTP/unsafe workflow paragraphs | противоречат новому вступлению/actual source | удалить двусмысленность в будущем docs batch, старые evidence сохранить |

RELEASE REPORT/STATUS честно говорят PARTIAL, code_integration_complete=false, live=false, copied PILOT, remote=false. Это **не ложное объявление полной готовности**. Вместе с тем разрешённая программная цель RELEASE§5 требовала реальные стадии получения результатов; план+fixture её не завершает. В том же задании запрещены обход eligibility и расширение протокола по догадке — эти ограничения сохраняются, а blocker должен быть передан архитектору, а не снят. [E13]

Providers/SYSTEM запрещали live/новое производство; отсутствие новых live/video proofs не является нарушением этих запретов. Packaging closure/recovery/integration gaps — технические задачи; eligibility/auth — внешние gates; качество TTS/музыки/стиля — отложенные пожелания. Historical docs о missing PyYAML/старом CI следует датировать, а не принимать за текущий CI статус.

## 11. Минимальный backlog и один следующий пакет

Все пункты ниже — **предложение архитектору**, никакие исправления сейчас не разрешаются.
Общие запрещённые зоны: рабочая студия/SQLite/media/cache/credentials/accounts, main mutation, protected production-core; Codex не меняет5 ChatGPT-owned CI/security файлов. Canonical Identifier/Review и существующий движок сохраняются. Нужное расширение protected schema/runtime extension point архитектор должен явно распределить, без обхода canonical validators.

| ID / category / факт | Минимальная работа; reuse | Deps / owner / объём | Приёмка и безопасный scope |
|---|---|---|---|
| B01 CI; timeout доказан, cause unknown | timings/stacks/owned lifecycle, measured setup optimization; existing checker | ChatGPT+Codex по ownership; M, диагностика | exact merge CI, все прежние596+tests, явные skips, обе smoke; no silent timeout pass |
| B02 CODE; source context gap | bounded text/JSON extractor с source/edition/offsets; existing Source | Codex; M | настоящие разрешённые bytes в queue context, injection/changed hash negatives; offline |
| B03 CODE; outputs не зарегистрированы | bounded outline model + Canon/outline/SegmentVisuals canonical acceptance цепь; reuse Task/gate/exporter | B02; Codex/allocated interface owner; L, несколько границ | poll/manual/recovery/replay всех outputs, malformed/forged citations/authority denied |
| B04 INTEGRATION; plan-only graph | accepted-artifact acquisition executor/expansion в той же queue; current ApprovedInputs graph | B03; Codex; L, dynamic admission | ordinary CLI новый Brief без готового story/Scenes;1/2/5 scenes; crash accept→expand, no duplicates |
| B10 CODE; semantic refs gap | qualify editions, enforce duration/style allocation/infeasible result | B03; Codex; M | одинаковые fact_id разных editions не смешиваются; target имеет проверяемый смысл |
| B05 INTEGRATION; runtime workers/fix absent | separate bounded transport interface + automatic Codex proposal/fix caller; existing worker/gate/engine | B04; live X01/X02; Codex; L | offline contract through ordinary caller; live выключен; real proof отдельно |
| B06 CODE/TEST; static recovery candidate | wire existing JobManager.resume/liveness/retry в generic adapter | Codex; S/M, helper есть | dead/failed/blocked/live/stale no-tools tests, same job/no duplicate, finite budget |
| B07 INTEGRATION; launcher Drive gap | approved host bootstrap передаёт DriveHostContext | X03 mechanism decision; Codex+owner; M | actual launcher dummy callback tests; wrong scope no network; real cloud отдельно |
| B08 CODE/TEST; ZIP scripts отсутствуют | transitive internal scripts/resources; interpreter/model placement guide | Codex; S | relocated lazy import/script closure, plan/doctor, без TTS/render |
| B09 TEST; general real path unverified | отдельный новый approved-input functional acceptance, revision/replay | B01/B06/B08/X04; Codex+owner; M | реальные WAV→MLT→MP4/QA/portable; neighboring hashes/jobs unchanged |
| B11 CONFIG/docs; stale contradictions | один актуальный runbook с scope/blocked states | B01/B04/B07/B08; file owner; S | команды/errors соответствуют исходникам и измерениям |

MUST FINISH: B02/B03/B04/B10 acquisition; B05 automatic runtime worker/fix integration; B06 recovery; B07 delivery bootstrap; B08 closure; B01 independent CI; B09 отдельно разрешённая real acceptance.

Критический путь:
B02→B03→B04+B10→B05 offline integration→independent CI/software review.
Параллельно B06/B07/B08.
После этого две разные приёмки:
local approved-input: runtime/storage+owner permission→real general pipeline/revision/replay;
autonomous: lawful eligibility→verified live A/B/Codex+media→полный новый-topic run→owner review→verified Drive.
Runner/main — отдельная delivery/deployment ветка, не prerequisite локальных computation и не доказательство автономности.

**Один предлагаемый next batch: SYSTEM-CLOSURE-02.** В одном exact-HEAD development-поручении закрыть B02/B03/B04/B10, offline часть B05, B06/B07/B08 и согласованные CI observability/tests/docs. Использовать один Coordinator, прежние gate/Registry/engine, data-only injected workers для нового acquisition path. Не повторять PILOT и не выдавать такой offline результат за живую готовность. При неизвестном client interface сначала закрепить bounded adapter contract; реальный launch не выдумывать. Статус конца пакета должен отдельно перечислять offline code completion, local real acceptance и autonomous live gates.

Checklist следующего software review:
- новый Brief+inventory проходит **исполнение acquisition** без prewritten creative bundle;
- Canon/outline/Scenes/mappings принимаются тем же gate, editions/citations/versions/allowlists защищены;
- обе synthetic reservations реально одновременно active; alias/stale/quota/replay negatives сохранены;
- runtime Codex/fix interface реально вызван, не подменён Python label; commands/authority host-owned;
- dead/live local job recovery и zip lazy execution closure проверены без tools;
- Drive ordinary bootstrap тестируется до queue и auth failures, без реальных secrets/network;
- exact-SHA independent CI проходит полный сохранённый набор и hosted smoke;
- residual live/production/remote acceptance прямо NOT_RUN, без общего зелёного AUTONOMOUS.

EXTERNAL GATES / единый owner action list:
1. Принять архитектурный scope/ownership interfaces; ChatGPT завершает CI диагностику/review.
2. Выбрать isolated checkout/run/input/session roots, storage budgets и размещение существующего Ruslan/runtime; без очистки/скачивания/молчаливой синхронизации.
3. Отдельно разрешить один general functional production acceptance после software проверки; текущий аудит его не запускает.
4. Только lawful service eligibility; пока BLOCKED_ELIGIBILITY сохраняется, никакого agy/login/account substitution.
5. После допустимости отдельно подтвердить actual client интерфейс,2 разные host identity/session bindings, модели/ошибки/quota и media capability; если сервис не поддерживает требование, owner/architect принимает явное решение, fallback не автоматический.
6. Выбрать штатный approved Drive host callback/folder/session root и отдельно разрешить bounded tiny upload/checksum/readback/restart proof.
7. Если нужен GitHub runner: после green review отдельно разрешить main integration и runner isolation/registration/start; fake-only exact SHA. Не обещать runner «зарегистрирован» без свидетельства.

После next batch останутся X01 eligibility, X02 live identity/client/media capability, X03 real Drive auth/readback, X04 new real production approval/runtime/storage, X05 main/runner owner actions и B09 реальная проверка. Часть live transport зависит от непроверенного поддерживаемого интерфейса, поэтому нельзя гарантировать её завершение одним offline коммитом.

DO NOT TOUCH: Ruslan/model/profile/lexicon, существующие operations/jobs/Registry/renderer, WAV cache и отдельные WAV, MLT/timeline/оригиналы/manual edits, canonical Identifier/Scene/Review/EditPlan meanings, accepted historical artifacts/commits/evidence.
POST-FIRST-VIDEO: ударения, интонации/тембр, музыкальная/визуальная подача и художественное улучшение текста; сейчас без изменений.

## 12. Ответы архитектору и evidence index

1. Сейчас — plan/doctor; simulation на готовых fixtures; условный approved-input из полного готового creative JSON+runtime. Без программиста/JSON подготовки topic-only продукта нет.
2. Полной новой темы без ручного сценария нет; первый code break — executable source→Canon; live admission дополнительно blocked eligibility.
3. Двух-worker queue/binding/gates готовы как offline software; transport/session IDs/media только synthetic, реальные аккаунты/квоты неизвестны.
4. Не хватает extraction, canonical acquisition outputs/outline, queued accepted-artifact expansion, unknown Scene/mapping acquisition, runtime Codex/fix, live/media transport и ordinary Drive bootstrap; отдельно recovery/ZIP gaps.
5. CI останавливает420s общий v2 deadline. Local diagnostics исключают воспроизводимый здесь concurrency hang, но не hosted-specific wait. Причина остаётся гипотезой до timing/stack evidence.
6. Existing engine/profile/WAV/cache/MLT/Registry/queue/gate/typed Scene/Review/EditPlan/Drive HTTP сохраняются; новую архитектуру не нужно.
7. SYSTEM-CLOSURE-02 выше — один dependency-ordered программный пакет с offline acceptance и честными external gates.
8. Owner actions перечислены единым списком; без secrets, обхода eligibility и автоматической смены аккаунта.
9. Local acceptance: отдельно разрешённый реальный общий CLI→WAV/MLT/MP4/QA/portable+revision/replay. Autonomy: тот же путь от нового Brief через реальные distinct workers/media/Codex и remote receipt без ручных AI responses.
10. **Фактов достаточно для одного конечного software implementation prompt с conditional external acceptance.** Для безусловного обещания полной автономности недостаточно: неизвестны supported installed-client interface/media/identity/quota, hosted timeout root cause, actual authorized Drive host и runner registration, future runtime/root/storage budget и general real recovery. Причины — запрет live/account/production probes и отсутствие per-test CI telemetry; безопасные способы проверки приведены B01/X01–X05. Эти неизвестные не заменены догадкой.

| Evidence ID | Проверяемый источник, decisive excerpt / scope |
|---|---|
| E01 | evidence/git_status_before.txt, local_refs_before.txt, git_preflight_after_fetch.txt, git_after.json; git fetch/status/HEAD/tree/log/diff/ls-remote, exit0;4 старых untracked trees |
| E02 | agent_entry_acquisition.md ENTRY-01..10; [launcher exact SHA](https://github.com/elkyran/AI_VIDEO_02/blob/64905d00016c406d437d9f5b2c0eb2c1c60f7752/scripts/launch_multi_ai_v2.py#L62), [dispatcher](https://github.com/elkyran/AI_VIDEO_02/blob/64905d00016c406d437d9f5b2c0eb2c1c60f7752/app/v2_pipeline.py#L740) |
| E03 | agent_entry_acquisition.md ACQ-01..13; [acquisition plan](https://github.com/elkyran/AI_VIDEO_02/blob/64905d00016c406d437d9f5b2c0eb2c1c60f7752/app/v2_release_plan.py#L45), [sole handoff caller](https://github.com/elkyran/AI_VIDEO_02/blob/64905d00016c406d437d9f5b2c0eb2c1c60f7752/app/v2_release_flow.py#L31), [topic-only context](https://github.com/elkyran/AI_VIDEO_02/blob/64905d00016c406d437d9f5b2c0eb2c1c60f7752/app/v2_pipeline.py#L352) |
| E04 | agent_workers.md W01..W08; [route scheduler](https://github.com/elkyran/AI_VIDEO_02/blob/64905d00016c406d437d9f5b2c0eb2c1c60f7752/app/multi_ai.py#L705), [protocol fixed schemas](https://github.com/elkyran/AI_VIDEO_02/blob/64905d00016c406d437d9f5b2c0eb2c1c60f7752/app/v2_provider_protocol.py#L77); test names§5 |
| E05 | agent_engine_delivery.md P1..P6,D1,R1,Z1; [engine caller](https://github.com/elkyran/AI_VIDEO_02/blob/64905d00016c406d437d9f5b2c0eb2c1c60f7752/app/v2_pipeline.py#L408), [generic job poll](https://github.com/elkyran/AI_VIDEO_02/blob/64905d00016c406d437d9f5b2c0eb2c1c60f7752/app/v2_pipeline.py#L519), [host missing in CLI](https://github.com/elkyran/AI_VIDEO_02/blob/64905d00016c406d437d9f5b2c0eb2c1c60f7752/app/v2_pipeline.py#L782) |
| E06 | GitHub snapshot/jobs/artifacts/job log/merge parents/compare; downloaded CI ZIP+manifest/checks/logs, ci_integrity.json, ci_source_comparison.json; hashes§9; independent run failed |
| E07 | diagnose_ci.py + repro_concurrent/ci_prefix.json/.log, ci_timing_analysis.json; 2 bounded diagnostics,85 executions including3 repeated cases, не85 distinct requirements |
| E08 | historical_full_integrity.json; old c881e4c manifest35bbbf5…;11/11 hashes verified, full checker NOT_RUN now |
| E09 | release_integrity.json;82/82 disk+ZIP hashes/size, ZIP integrity; missing internal execution scripts; no tools executed |
| E10 | pilot_current_bytes.json;14 original/copy hashes,6 WAV headers/nonzero bytes/XML resources; no decode/render |
| E11 | environment.json + environment_powershell.json; version metadata/path existence/free bytes; no auth/client/GPU stress |
| E12 | readonly_cli.json + external readonly_configs;9 safe command results, all run roots absent |
| E13 | actual RELEASE REPORT/STATUS/NEEDS_CHATGPT_CHANGE and original SYSTEM/PROVIDERS/RELEASE instructions, read targeted claims only; no old report accepted as new test |
| E14 | audit_actions in FACTS, git_after.json, audit_evidence.json; only external audit outputs and allowed Git fetch metadata changed |

Крупные журналы находятся в evidence/. AUDIT_01_FACTS.json содержит machine-readable requirements/blockers/dependencies/owners/commands; audit_evidence.json содержит размеры/SHA всех выданных файлов, исключая себя и disposable roots. Ключевые conclusions, excerpts, CI/artefact hashes и приёмка приведены выше и не требуют доступа к C:.

Для выполненных audit-команд: workload model requests=0, paid API requests=0, production started=false, installs=0, commits/push/merge=0. Это не счётчик работы Codex-разработчика и не наблюдение всех процессов компьютера. Сетевое чтение Git/GitHub/artifact **выполнялось**; provider/account APIs и Drive upload не выполнялись.

После создания отчёта — STOP; никаких fixes/commit/push/merge/видео.
