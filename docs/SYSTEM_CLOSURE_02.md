# SYSTEM-CLOSURE-02: scope and normal entry

Новый вход — `BriefInputs`: version, разрешённые sources/media, requested_scene_count,
Brief и host policy `acquisition`. Готовые Canon/story/Scenes/visuals не нужны.
Source passages используют **raw UTF-8 byte offsets**: BOM входит в offsets,
CRLF сохраняется, границы Unicode проверяются. JSON strict, bounded, без сети/OCR.
Worker text — данные; он не предоставляет runtime, paths или permissions.

`acquisition.duration_tolerance_ms` обязателен. StoryOutline оценивает время;
WAV измеряется отдельно. Длительность и голос автоматически не подгоняются.
`director_required=true` требует fixed Codex route и trusted director factory.
Только injected offline adapter допускается этим пакетом; live Codex UNVERIFIED.
EditPlan director сохраняет measured voice/subtitles/profile; unsupported effects
и чужие assets отклоняются. Review metadata-only не означает просмотр/прослушивание.

`HostServices` передаётся in-process из доверенного host в `launch.main` →
`system_main` → `SystemPipeline`. JSON не импортирует Python-код/клиентов и не
содержит OAuth paths. Source-only simulation требует exact `SyntheticTransport`;
без wiring будет HOST_TRANSPORT_REQUIRED, без director — DIRECTOR_BINDING_REQUIRED.
Live Story/Visual остаются BLOCKED_ELIGIBILITY. Обычный approved_input совместим.

Обычные команды (пути выбирает владелец; сейчас это инструкция, не разрешение production):

```powershell
studio-v2.cmd --config C:/AI_VIDEO_02_inputs/host.json plan
studio-v2.cmd --config C:/AI_VIDEO_02_inputs/host.json doctor
studio-v2.cmd --config C:/AI_VIDEO_02_inputs/host.json --execute-system --authorize start
studio-v2.cmd --config C:/AI_VIDEO_02_inputs/host.json --execute-system resume
studio-v2.cmd --config C:/AI_VIDEO_02_inputs/host.json --execute-system inspect-artifacts
studio-v2.cmd --config C:/AI_VIDEO_02_inputs/host.json --execute-system deliver
```

Для нового source-only пути доверенный embedder вызывает тот же
`scripts.launch_multi_ai_v2.main(argv, host_services=services)`. В этом пакете
offline integration находится в `tests.test_v2_closure_integration`; тесты получают
ответы только на границе synthetic transport и перехватывают native IO. Они
вызывают настоящие handlers, но не доказывают настоящее видео/интеллект/качество.
Не запускать эти doubles как production-клиент.

Один узкий edit-only fix выполняется по canonical accepted Review с exact
subject hash/version, однозначной placement location и отдельным
`acquisition.auto_fix_authorized=true`, `brief.auto_fix_cycles=1`. Не более одного
cycle на authority/run; unaffected Scene и accepted history сохраняются.
Narration и visual-selection issues сейчас возвращают NEEDS_REVIEW с назначенным
Story/Visual owner. Их автоматическое выполнение — оставшийся software gap.
Pinned/manual пользовательские edits не принимаются как auto-fix inputs.

Recovery проверяет requests/job/script binding, invalidation/cancellation,
retry budget и worker birth receipt. Исторический job без host receipt держится
в JOB_OWNERSHIP_UNVERIFIED. Unknown/foreign PID не убивается/не дублируется.
Durable resume intent делает неоднозначный crash fail-closed, требуя inspect.
Protected JobManager не возвращает birth spawned child: первая запись является
HOST_OBSERVED_WORKER, а не криптографическим подтверждением владения процессом.

Drive bootstrap принимает exact `DriveHostContext`. Нет binding → WAITING_AUTH.
Resolver закреплён к folder/run/version/config и request budget. Synthetic HTTP
никогда не становится uploaded/live remote proof. Accounts/auth не открываются.

## Source deployment

Source ZIP содержит explicit inventory, hashes и source commit/tree. После
распаковки launcher сверяет SOURCE_INVENTORY.json до tools. Нужен установленный
Python 3.11+ с зависимостями pyproject.toml. Если packaged .venv-core отсутствует,
`studio-v2.cmd` принимает только известный владельцу `STUDIO_PYTHON`; установки нет.
Source ZIP не является самодостаточным exe; development CI в него не входит.

Существующие Ruslan files располагаются относительно распакованного root:
`models/piper/ru_RU-ruslan-medium.onnx` и соответствующий `.onnx.json`.
Хеши/параметры должны совпадать с неизменённым config/production_profile.json.
Runtime указывает существующие Piper interpreter, FFmpeg/ffprobe, Shotcut melt;
profile согласуется явно. Fonts и tools устанавливаются/предоставляются владельцем
отдельно. Активная SQLite, модели, cache, media и credentials в source ZIP не входят.
Preview/ASR внутренние Python scripts включены; их native модели/tools — external.

После независимого software review отдельное general-real acceptance требует
нового external root, owned inventory и hashes, точного runtime/profile, разрешений
synthesize/compile/render/qa/portable и отдельного разрешения владельца. Проверить
новые WAV → MLT → MP4, один scoped fix, restart и переносимость; сохранить receipts.
Это будущая проверка. В SYSTEM-CLOSURE-02 реальные TTS/render/upload не выполняются.
