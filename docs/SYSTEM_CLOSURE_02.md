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

Source verifier допускает только fixed installation roots `.venv-core`, `.venv-tts`,
`tools/Shotcut` и точные external files из checksum-verified профиля. Содержимое
установок не сканируется и не подтверждается этой проверкой. Другие файлы/каталоги
и изменения shipped app/scripts/config блокируются; manifest не расширяет исключения.
Модели/инструменты можно указать только существующими явными runtime locators,
а для Ruslan сохраняется ожидаемый root-relative layout.

Неизменённый `load_profile` дополнительно требует существующие исторические proofs:

| Relative path | Required SHA-256 |
|---|---|
| workspace/studio/jobs/job_154a062f4b324170b915/audio_manifest.json | f77019c46379dcdee1ca2d9aa000b445141b1db1aa3558c63febff4e3875d305 |
| workspace/studio/voice_replacements/ruslan_final/audio_manifest.json | ec0ab555dcc50dba20c67864f7b9a0c8055d417edde504c4518d866ee6a94a00 |
| workspace/studio/jobs/job_7c4eb17065b34e79a300/project_attempt_1/timeline.json | b6bcd5b575f15c5f343c1dce579e8e131f6928dcf55fd74e664961177ccfa330 |

Также требуются точные refs `workspace/studio/runtime.json`, `WORKFLOW.md`,
`docs/without_rudeus_ruslan_verification.json` и
`audit/2026-10-17_video_codex_719e46b2/CODEX_VIDEO_AUDIT.md` (existence checks).
Эти private/local prerequisites **не входят в source ZIP**. Исторический runtime
reference — существующая проверка наличия, не источник credentials и не разрешение
открывать или публиковать его. Владелец предоставляет только необходимые разрешённые
refs отдельно; копирование студии целиком, SQLite или аккаунтов не требуется.
При отсутствии/изменении proofs production prepare блокируется до enqueue/tools.
Source relocation test проверяет этот отказ; настоящий restored runtime не заявлен.

После независимого software review отдельное general-real acceptance требует
нового external root, owned inventory и hashes, точного runtime/profile, разрешений
synthesize/compile/render/qa/portable и отдельного разрешения владельца. Проверить
новые WAV → MLT → MP4, один scoped fix, restart и переносимость; сохранить receipts.
Это будущая проверка. В SYSTEM-CLOSURE-02 реальные TTS/render/upload не выполняются.

## Prospective general-real acceptance recipe — NOT EXECUTED

Prerequisites: независимый exact-SHA CI после ChatGPT schema/group sync; отдельное
решение по SG01–SG03; разрешённые официальные изолированные Story/Visual и Codex
bindings с проверенной eligibility; явное разрешение владельца на native media.
Сейчас autonomous source-only путь блокируется: этот рецепт не обходит live gate.

Предлагаемые новые пути: input root `C:/AI_VIDEO_02_inputs/general_real_01`,
config `C:/AI_VIDEO_02_inputs/general_real_01_host.json`, run root
`C:/AI_VIDEO_02_runs/general_real_01_OWNER_CHOSEN_UNIQUE`. Они не создавались.
Владелец выбирает отсутствующий run root вне repo/OneDrive/Drive и подтверждает
бюджет медиа отдельно: development allowance 250MiB не является production budget.

Host JSON должен пройти текущую `HostRunConfig`, с такими точными полями:

| Field | Future value / binding |
|---|---|
| run_id, key, version, parent_sha256 | general_real_01; stable owner key; 1; null |
| mode, brief.production_mode | autonomous; production — только после разрешённых live gates |
| brief | owner topic, language=ru, target_minutes, style, source_profile=owner_supplied, auto_fix_cycles=1, output_profile=ruslan-local-narration |
| root, input_root | два абсолютных пути выше; input manifest заморожен до authorize |
| inputs | version=1, requested_scene_count от владельца, sources и media; без Canon/story/Scenes/mappings |
| inputs.sources[] | source_id, edition, relative .txt/.md/.json path, SHA-256, exact byte size, owned/permission/public_domain rights |
| inputs.media[] | asset_id, relative path, SHA-256, exact byte size, version=1, image/video kind, rights; video duration_ms измерен отдельно |
| profile_sha256 | fingerprint(profile_spec()), не raw file SHA; все исторические proof/model hashes совпадают |
| operations | synthesize, compile, render, qa, portable; deliver только после отдельного Drive разрешения |
| acquisition | duration_tolerance_ms явно owner-selected, director_required=true, auto_fix_authorized=true; retryable_job_errors=[] до отдельного решения |
| worker_routes | fixed antigravity_story/gemini profile A, antigravity_visual/gemini profile B, codex route; synthetic=false; реальные разные identity hashes и current auth_generation из разрешённого host, без выдуманных IDs |
| runtime | существующие абсолютные tts_python, tts_model, melt_path, ffmpeg_path, ffprobe_path; tts_engine=piper; tts_normalization_scope=block; tts_* settings, pronunciation_lexicon/terms, video_encoder/render_threads точно из неизменённого профиля |
| budget | конечные owner-approved max_output_bytes, reserve_bytes>=4294967296, max_input_bytes, seconds, attempts<=2, external_requests; согласовать реальный API budget отдельно |
| review_policy, drive | owner_required; null до отдельного разрешения delivery |

Хеши/размеры рассчитываются по выбранным владельцем inputs до запуска. JSON не
содержит callback, Python import, credentials или OAuth file. Trusted host передаёт
реальные допустимые bindings через тот же `launch.main(argv, host_services=...)`;
synthetic test doubles в этом рецепте запрещены. Отсутствующий binding — blocker.

Последовательность будущей проверки: выполнить plan и doctor командами выше с
новым config; сохранить их JSON и config fingerprint, затем отдельно разрешить
start. Проверить WAV manifests/cache, MLT hashes и actual MP4 QA. На одном accepted
Review выполнить только разрешённый edit fix; сохранить предыдущие версии и
byte hashes unaffected Scene. Выполнить resume тем же config/key, убедиться в
отсутствии новых jobs/dispatch, затем inspect-artifacts. Перенести portable пакет
в ещё один новый external root и проверить asset/MLT hashes и read-only reopen
без original checkout. Окончательная owner review фиксируется отдельно; QA passed
не равен owner accepted. Drive readback — следующий отдельный этап с approved host.
