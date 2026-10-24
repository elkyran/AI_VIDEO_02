# One-brief offline production bridge (BULK-01)

Это development-подготовка на существующем RunCoordinator/Coordinator,
V2ResultGate, Registry, ProductionScript, ProductionBridge и ArtifactStore.
Нового движка, очереди, scheduler или Registry нет. Production выключен.
Antigravity AGE-GATED / DISABLED; модели, login, API и Drive здесь не вызываются.

Одна команда с явной симуляцией и отдельным локальным root вне repo/sync/production:

```powershell
.venv-core/Scripts/python.exe -X utf8 -m app.multi_ai_v2 --root C:/AI_VIDEO_02_LOCAL/bulk_demo prepare --brief examples/bulk_01/brief.json --key original_demo --fixture-demo
```

Эта команда проходит существующие 19 fake stages, проверяет accepted Scene,
создаёт оригинальные маленькие text/PNG fixtures и registered mapping, сохраняет
ProductionScript candidate, отдельные narration/cache descriptors, canonical
EditPlan, editable project.mlt, timing.json, subtitles и immutable release.
Полученный MLT не является отрендеренным видео. Озвучка не генерируется.
Static PNG в demo — только fixture интеграции, не алгоритм художественного монтажа.
API mapping поддерживает несколько проверенных source ranges на один segment.

```powershell
.venv-core/Scripts/python.exe -X utf8 -m app.multi_ai_v2 --root C:/AI_VIDEO_02_LOCAL/bulk_demo status --run-id RUN_ID
.venv-core/Scripts/python.exe -X utf8 -m app.multi_ai_v2 --root C:/AI_VIDEO_02_LOCAL/bulk_demo resume --run-id RUN_ID
.venv-core/Scripts/python.exe -X utf8 -m app.multi_ai_v2 --root C:/AI_VIDEO_02_LOCAL/bulk_demo inspect-artifacts --run-id RUN_ID
.venv-core/Scripts/python.exe -X utf8 -m app.multi_ai_v2 --root C:/AI_VIDEO_02_LOCAL/bulk_demo report --run-id RUN_ID
```

Существующие create/advance/pause/stop/manifest сохранены. Тот же brief/key
идемпотентен; другой brief или первоначальный offline intent с тем же key конфликтует.
Необязательное additional_requirements ограничено 2000 символами; отсутствие/null
сохраняет исторический brief hash. Для нового offline intent выберите новый key.

Вместо demo можно передать `--inputs INPUTS.json` по `OfflineInputs`:
engine_relative, source_root_relative (оба под owning runs/RUN_ID), canon
(CanonBible), source_manifest_sha256 и visual_mapping. Assets должны уже быть
зарегистрированы в existing Registry выделенного engine workspace. Без mapping —
VISUAL_REQUIRED. Абсолютные model paths и подстановка случайного asset запрещены.
Внешний existing-engine workspace доступен через явный host Python API
OfflineProductionBridge; CLI не направляет автоматические записи в рабочую студию.

Каждый источник читается только как данные, сверяется с pinned manifest и hash.
Цитирование не доказывает истинность канона. Fact refs используют edition:fact_id,
source dependencies — edition:source_id → SHA-256. Assumptions остаются unconfirmed.
Source text не становится инструкциями Coordinator и не выполняется как код.

ProductionScript candidate имеет validated_not_registered. Для настоящего
synthesize требуется отдельно зарегистрировать точный script и вызвать
prepare_engine_request: он переиспользует ProductionBridge.prepare_request,
перепроверяет profile/runtime/revision и ничего не enqueue. Исполнять descriptor
через существующий JobManager можно только по отдельному разрешению владельца.
Cache recipes сверены тестом с operations.synthesize; WAV не создаются молча.
Live audio import пока BLOCKED, synthetic audio bindings разрешены лишь для fake
fixtures и должны совпадать с cache key конкретного текста/голоса.

propose_revision меняет только заданный segment новой Scene версии;
enqueue_fixture_revision допускает только fake adapters и тот же trusted gate.
Pinned/manual Scene не заменяется. scene_graph/invalidation_plan сохраняют
незатронутые cache keys и WAV hashes; final assembly может требовать пересборки.
Старые версии и прерванные/ручные builds не удаляются и не перезаписываются.

Technical QA подтверждает hashes, MLT references, объявленный профиль/coverage и
subtitle bounds с existing subtitle checker. Decoded MP4 FPS/resolution/duration,
silence и AV-sync остаются null без измерения; full-video creative QA — NOT_RUN.
Canonical Review маршрутизирует технические и editorial issues без fake acceptance.

Release содержит version/run/artifact/hash/size/provenance и production/delivery
status. Активная SQLite не копируется. delivery_plan — подготовка будущего
официального Drive client; uploaded=false, remote_sync=UNVERIFIED, cloud_receipt=null.
copy_to_fixture работает только под OS temporary directory, не вызывается CLI.
COPIED_VERIFIED подтверждает лишь локальные bytes, не cloud delivery.

Настоящие Ruslan WAV, render, полный video review, self-hosted runner и cloud
readback остаются за отдельным разрешением и live acceptance. Этот пакет не
создаёт YouTube-пилот и не повышает исторические development статусы.
