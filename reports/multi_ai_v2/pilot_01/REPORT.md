# PILOT-01 — итоговый отчёт

Статус: **LOCAL_TECHNICAL_PILOT_VERIFIED**. Только оригинальный локальный технический пилот «Как студия меняет одну сцену», без художественного acceptance и cloud delivery. Дата: 2026-10-21.

## Реальные файлы и измерения

- Preview: [preview.mp4](C:/Users/diffe/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/AI_VIDEO_02/pilots/pilot_01_20261021_7f91c2e8/engine/builds/film_v1_88b18848/preview.mp4) — `C:/Users/diffe/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/AI_VIDEO_02/pilots/pilot_01_20261021_7f91c2e8/engine/builds/film_v1_88b18848/preview.mp4`.
- Final: [final.mp4](C:/Users/diffe/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/AI_VIDEO_02/pilots/pilot_01_20261021_7f91c2e8/portable/final.mp4) — `C:/Users/diffe/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/AI_VIDEO_02/pilots/pilot_01_20261021_7f91c2e8/portable/final.mp4`.
- MLT: [project.mlt](C:/Users/diffe/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/AI_VIDEO_02/pilots/pilot_01_20261021_7f91c2e8/portable/project.mlt) — `C:/Users/diffe/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/AI_VIDEO_02/pilots/pilot_01_20261021_7f91c2e8/portable/project.mlt`.
- Relocated MLT: [project.mlt](C:/Users/diffe/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/AI_VIDEO_02/pilots/pilot_01_20261021_7f91c2e8/portable_copy/project.mlt) — `C:/Users/diffe/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/AI_VIDEO_02/pilots/pilot_01_20261021_7f91c2e8/portable_copy/project.mlt`.

Физический root: `C:/Users/diffe/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/AI_VIDEO_02/pilots/pilot_01_20261021_7f91c2e8`. Windows app virtualization разрешила исходный LOCALAPPDATA path в этот LocalCache root; он вне репозитория, рабочей студии и sync-папок.

Preview: **66.816 s**. Final: **67.904 s**, video stream 67.900 s, 2037 frames, r/avg FPS 30/1, **1920×1080, H.264**, AAC 48 kHz stereo. Начала audio/video — 0.000 s; разница 0.000 s. Перенесённый MLT реально исполнен установленным MLT в не-silent WAV 67.900 s (`relocated_probe.wav` вне пакета).

- Preview SHA-256: `8a4bc889a8a96fe1c31eefc76ae8f9b00ae2827de41bac2b8ee0356507a7257a`.
- Final MP4 SHA-256: `4f43e8a13452e37674f0ba6d69c681138720beabe9196fde67555354284c5447`.
- Portable project.mlt SHA-256: `f53e8d678490d1c528ad02ebca841bd12c6e016f80fd60062cc83d17d485790d`.
- Portable manifest SHA-256: `bb9ab2315e92011c28c5563707fcf8b6033fdfbcdc6721216d46440f7bee35a7`.

`portable/` и `portable_copy/` содержат одинаковые manifest-bound файлы. Сохранены относительные MLT media paths и sidecar subtitles. SQLite/WAL, модели, шрифты и логи в package не включены. Среда engine не нужна для media references перенесённого MLT. Все outputs и прошлые версии сохранены.

## Git и область изменений

- Initial/expected feature HEAD: `32860d344116d5eed96659d84658dad5dcfba974`.
- Origin/main при preflight: `7a1e4058df09dbb66448a3624e0250129eaf7aa0`.
- CI budget commit: `4b9e7e08f00a526a3a08a63b4750a254096215d0`.
- Local adapter commit: `7927cfb78d6d71869f6991330756dc3465932c92`.
- Final implementation SHA: `234f66db8fcc39544a3327d46df73b7b421b337f`.
- Final tested source manifest SHA-256: `5592bda5f2939015fbd79e3910456a247272333ea03f51f225e80cbf55053af4`.
- Последний commit содержит только этот отчёт и маленькие redacted JSON; его полный publication SHA — в финальном сообщении push и внешней post-commit привязке.

Ветка только `feature/multi-ai-coordinator-v2`. Локальный `d9d67b6` синхронизирован с `32860d3` отдельно разрешённым владельцем fast-forward. PR/main merge, reset/clean, force-push не выполнялись. Пять ChatGPT-owned файлов и production-core не изменялись относительно проверенного `32860d3`. Старые untracked review evidence сохранены, не добавлены в Git.

Изменённые файлы: `app/multi_ai_v2.py`, `app/v2_acceptance.py`, новый `app/v2_local_pilot.py`, `scripts/check_multi_ai_v2.py`, `tests/test_multi_ai_v2_checker.py`, новый `tests/test_v2_local_pilot.py`, `docs/LOCAL_VIDEO_PILOT.md` и три маленьких файла этого report directory. Canonical Brief/Review, Identifier и schemas не дублировались/не менялись.

## Admission, runtime и binding

Оригинальные host-authored/manual-assisted Scenes созданы заново из разрешённого текста. Fake result не переименовывался в real. HostAdmissionAdapter принимает только точный разрешённый input bundle через существующие Coordinator/V2ResultGate/immutable completion/CAS. Обычный fake/default production путь остаётся прежним. Новых очередей, scheduler, acceptance tables или монтажного движка нет.

Permit привязан к единственному root/run, исходникам, профилю и операциям synthesize/compile/render/qa/portable. Проверяются reparse points, включая дочерние cache/output junctions, до запуска инструментов. Отдельные Registry в coordinator/engine — прежний bridge layout на существующем Registry.

- Input bundle SHA-256: `ecd6b40054a4b1b9aeaacb29c5f502ad284b659f995b310f752605f409889a5e`.
- Profile SHA-256: `4fda02f2e97c89995bf603d9637774f00d7d5d2c9e0d33cd56fba37cf2f7f26e`.
- Permit file SHA-256: `58afe03c520d0a1f86555828685d904cf52241fe8ca37c434139c28c1441907c`.
- Ruslan ONNX: `72a5f88e0b20928064eb45d88e1daa21f8af62d18613580d32cbb4aed48dcf7f`.
- Model config: `706a4fb17bc304abd07809b552deae615e64dcbffbfbd09854ba37ca59e88117`.

Использованы установленные Python 3.11.9, Piper 1.8.0 из `.venv-tts`, Ruslan medium 22050 Hz, approved block normalization, length_scale=1.08, noise_scale=.667, noise_w_scale=.8, volume=.85, sentence_silence=.08, leading=.1, trailing=.18, use_cuda=false. Профиль/runtime рабочей студии не менялись и не копировались. Внешняя модель читалась read-only; downloads/installations отсутствуют.

Каждый WAV проверен по completed existing synthesize job: run/project/script SHA, manifest SHA, точный request config/cache key, text/model/config/settings/version, физические bytes и measured duration. Core `operations.synthesize` и JobManager не изменены. Для кадрового grid удалён только проверенный нулевой PCM-tail менее 100ms, исходные WAV сохранены полностью.

Existing ProductionBridge.compile/render/export и scripts.render_project использованы напрямую. Existing renderer — libx264 CRF20/preset fast/4 threads; отличие от legacy NVENC encoder профиля объявлено явно. Голос и 1920×1080/30 не менялись. Graphics — собственные System.Drawing PNG, без внешних материалов и AI-генерации.

## Настоящий targeted fix, кеш и restart/replay

Изменена только фраза `неточная фраза` → `фраза, требующая уточнения` в `scene_002.voice_001`, Scene version 1 → 2. Вторая реплика этой сцены — cache_hit=true. SHA WAV и scene MP4 первой/третьей сцен совпадают до/после. Финальный фильм повторно собран из accepted scene MP4 picture и отдельных original WAV.

| Счётчик | До правки | После правки | После restart/replay |
|---|---:|---:|---:|
| synthesize jobs | 4 | 5 | 5 |
| job attempts | 4 | 5 | 5 |
| host queue attempts | 3 | 4 | 4 |
| scene renders | 3 | 4 | 4 |
| film reassemblies | 1 | 2 | 2 |

9 segment requests: **7 новых WAV, 2 cache hits**, без дополнительных попыток. Smoke — первый сегмент, затем он взят из кеша. Новые процессы повторили prepare/fix/replay/deliver на final implementation SHA без новых jobs/renders. Изменённый script со старым production key реально дал `IDEMPOTENCY_CONFLICT` до TTS; counts 5/5/4/4/2 не изменились.

| Job ID | Script asset | Attempts | Cache hits |
|---|---|---:|---:|
| `job_4722cbb1164d4a0ab8b0` | `script_scene_001_v1_smoke` | 1 | 0 |
| `job_46762ba995ab42f3a372` | `script_scene_001_v1_full` | 1 | 1 |
| `job_73583b58372a4770b361` | `script_scene_002_v1_full` | 1 | 0 |
| `job_d8574d219e5549e8a234` | `script_scene_003_v1_full` | 1 | 0 |
| `job_cc001badc49d440cb17b` | `script_scene_002_v2_full` | 1 | 1 |

| WAV block (две версии строки scene_002) | Seconds | SHA-256 |
|---|---:|---|
| scene_001_voice_001 | 10.842540 | `7cefa40f2807bf114c4a19bbb52b40bc091681a6b1de50c081d76b6b9a07b132` |
| scene_001_voice_002 | 9.926621 | `16b7ee4a14b09d5e43802a768317a46af576f959699edaa8bf28363f55ae38e3` |
| scene_002_voice_001 | 10.436190 | `0bc1baaeaaea6176bb872e668bd4a5104b890f76c83fc371c2d9d4cae5106f49` |
| scene_002_voice_002 | 12.956825 | `3ec73a4e6f423ef70b2ed7469fe5915b6eb2449de0c6520d0ae219d9dc3482ed` |
| scene_003_voice_001 | 10.530340 | `6ccfc3941df17d3cd0b6622d88ae944b48ef60b2e5907f93d5b6013b0e9873d4` |
| scene_003_voice_002 | 12.399546 | `2b04449637690982eebabe2714e228ef7ab1a5cb6a3501d0ec9b1918b6bf2f99` |
| scene_002_voice_001 | 11.597188 | `dc19b5f48b689a623995caff6c1091ec8d96c7a19b15f0321f718e3774e2bdbe` |

Полные before/after scene/WAV bindings — `PILOT_EVIDENCE.json`; проверка окончательного commit/source с теми же реальными файлами — `FINAL_BINDING.json`. Первый JSON сохраняет также историческую привязку pre-hardening runtime; второй привязан к final implementation.

## Технический QA и ограничения

FFprobe и полное FFmpeg decoding прошли; black intervals=[], отсутствующих media=[], subtitle issues=[]. Mean volume final −25.2 dBFS, max −4.3 dBFS. Самая длинная detected pause — 0.557104 s; длинной добавленной тишиной ролик не растягивался. Frame grid и coverage проверены compiler, stream/timeline alignment измерен; phoneme alignment/ASR не измерялись. Полные silence/level/decode логи сохранены.

`editorial_review=PENDING_OWNER`, `full_video_creative_qa=NOT_RUN`: качество истории/монтажа/голоса автоматически не принято. Это технический выпуск, не готовый качественный аниме-разбор.

## CI blockers и regression

Node harness из expected HEAD проверен три раза: 1.125 / 1.094 / 1.062 s wall time, 3 passed. Исходный 10s timeout локально не воспроизвёлся; harness/routes/assertions не менялись, увеличение deadline без измеренного основания не применялось. Последующий полный mai05 также passed. Старый CI timeout не объявлен неважным flake.

Contracts budget изменён только на 420s; v2=420s, остальные группы=180s. Добавлен negative contracts timeout test: даже partial OK сохраняет exit124/failed. Local contracts: 76 тестов, 97.125s. Предыдущий 180s budget уже не соответствовал размеру группы и реально исчерпан в PR CI.

**NEEDS_CHATGPT_CHANGE:** ChatGPT-owned CI evidence validator должен ожидать **420 для v2/contracts, 180 для остальных**, сохранив coverage/status/hash/skip checks и GitHub job limit 30 min. Этот файл не менялся. GitHub CI массово не перезапускался.

Сохранённые независимые PR run 39596169009 evidence: attempt1 manifest `856e71d0da4592ab07169ed6f522b2cae407f30e23ed43696815786242bca489`; attempt2 `871dc764ca810c1344aad5df52824f0b8ca6b92f7a10d3550cb7ecafcd86dbab`. Temporary merge `021ab2f38e8c8d004bdf4c8867a6a9c575d4d291`, tree `6db3bdb9ccdc2b01352b7fb98c5d92502329554c`; прежнее evidence не удалялось.

Полный final canonical checker: **474 tests = 473 passed + 1 прежний skip, 0 failed/errors**. Все прежние 455 сохранены; добавлены timeout test и 18 pilot tests. Skip — прежний RealPiperTests в core-окружении (Requires installed local Piper); настоящий пилот использовал отдельно установленный `.venv-tts`. Новых skips/скрытых retries нет. Source unchanged during checks=true.

| Group | Tests | Seconds | Budget | Status |
|---|---:|---:|---:|---|
| v2 | 86 | 128.453 | 420 | passed |
| contracts | 76 | 97.125 | 420 | passed |
| queue | 50 | 12.688 | 180 | passed |
| adapters | 16 | 0.437 | 180 | passed |
| mai05 | 107 | 36.437 | 180 | passed |
| panel | 12 | 3.406 | 180 | passed |
| legacy | 127 | 12.828 | 180 | passed |
| compileall | — | 0.329 | 180 | passed |
| check_git_export | — | 3.640 | 180 | passed |
| diff_check | — | 0.078 | 180 | passed |

## Команды и evidence

Каждая CLI-фаза выполнялась отдельным процессом. Точные argv/exit/elapsed и полные stdout/stderr — в immutable external manifests/logs. Основные вызовы (тот же root/run):

```powershell
$r = 'C:/Users/diffe/AppData/Local/AI_VIDEO_02/pilots/pilot_01_20261021_7f91c2e8'
# init; smoke; prepare; fix; replay; deliver (последовательно, с opt-in)
.venv-core/Scripts/python.exe -X utf8 -m app.v2_local_pilot PHASE --root $r --run-id pilot_01_20261021_7f91c2e8 --execute-local-pilot
.venv-core/Scripts/python.exe -X utf8 scripts/check_multi_ai_v2.py --output C:/Users/diffe/.codex/visualizations/2026/10/20/01a158d2-a617-7862-8ff9-5159342bcc27/pilot_01/full_final
.venv-core/Scripts/python.exe -m compileall -q app scripts tests
git diff --check
git status --short
git diff --stat
```

Полные per-job Piper requests/logs/manifests — `<PILOT_ROOT>/engine/jobs/JOB_ID/`. Renderer metadata/returncodes/logs — рядом с каждым MP4 (`*.render.json`, `*.render.log`); FFmpeg/ffprobe argv/exit — `<PILOT_ROOT>/logs/*.json`. Relocated MLT command/exit — `<PILOT_ROOT>/relocated_mlt_command.json`.

Исходные argv выше использовали AppData alias внутри Codex. Для повторного запуска вне Codex задайте `$r` физическим LocalCache root из начала отчёта.

External evidence root: `C:/Users/diffe/.codex/visualizations/2026/10/20/01a158d2-a617-7862-8ff9-5159342bcc27/pilot_01`.

| Stage | manifest.json относительно external root | SHA-256 |
|---|---|---|
| node_preflight | `node_preflight/20261021T124459561482Z_8af836a6f8f94ca3b386ba5728c20c26/manifest.json` | `48ce76a8ab7b46cf75fd0ec9e98256f1f75531d5feb3947a7d5a9f55b02b0ada` |
| real_init | `real_init/20261021T125846222009Z_b751fde2e23e4558b8f63930d11eed28/manifest.json` | `15a8da19da59df4c5e126c5425c1468b1a27030202f9f574f995bd17157e88d3` |
| real_smoke | `real_smoke/20261021T130039704878Z_f70fe793cc8b443b8f312929d3b340e9/manifest.json` | `fc9b9ebeef4560193dbc32c0ca056b6957916fd27832af77bcf0ac1afae19455` |
| real_prepare | `real_prepare/20261021T130117670769Z_afe256555b98481b8bf26ff9c1265dda/manifest.json` | `adfef2c39c74efe09d04e841c74e4d87326494ad7a7d638b08bdcbc6f76f0081` |
| real_fix | `real_fix/20261021T130449406845Z_ad86779d5c9d4107b192d7434156a480/manifest.json` | `d4e33385a1f0970520b666ce08e0756a1c46ddd20ed697564d1406798694340b` |
| real_replay | `real_replay/20261021T130625488635Z_fb91bb0551ca40e2af6b5aff334a7a1c/manifest.json` | `8f9a76773031d7c869e7151d59a0f8cbb5a2e630cddb8db62ba4a9035a1b5736` |
| real_conflict | `real_conflict/20261021T130950721599Z_8b819efbe3fc487bb546e4ee1fcf860f/manifest.json` | `7b0baf34108b6cf9fbaa848b7c3df3bea97054268553aed125c1d92809e98b2f` |
| real_deliver | `real_deliver/20261021T132130880430Z_433cb9c6656b46f98d867f2ffea7ca9b/manifest.json` | `c6a3808b4190122440edc865f36ef9daa24d045c641e24411b2ee8e60799a3c4` |
| final_committed_runtime | `final_committed_runtime/20261021T132313212706Z_7ffa6e4d4948416ba25f7fc4e1ffb202/manifest.json` | `40b199260f47b01f0f44f081331d4c39ace80e9d39404d4095dd5ba46e424977` |
| full_final | `full_final/20261021T132202194412Z_18a6f6d130d54f3a9f4136dda407a33c/manifest.json` | `7280a53bbd57202352630d405200ffc4d7b2b955b1e951b2f5bbe654e631ae36` |

Предыдущий full pass до junction hardening: `full/20261021T131323137283Z_13bde191545241368597272f89f32e33/manifest.json`, SHA `918a8259eba431989aad4759355775877d9f738eb03af90dcbe85b32e8e44b50`, 473 tests. Финальный pass выше включает новую junction-регрессию. Отладочные failed evidence (reserved scene hash key, CLI class identity) сохранены; дефекты исправлены адресно до успешного smoke. Маленькая ошибка fixture title также исправлена до full pass. Ни одно повреждённое output не перезаписывалось ради зелёного результата.

## Расход и следующий шаг

Initial free C: 9,435,570,176 bytes; final measurement 9,384,939,520 bytes. Pilot persistent outputs 44,741,750 bytes (≈42.67 MiB), лимит 1 GiB, резерв 4 GiB соблюдён. Очистки пользовательских файлов не было.

Реальный pilot: `production_started=true`; canonical offline evidence: `production_started=false`. LLM/provider requests=0, paid API requests=0; локальный Piper действительно исполнялся. Accounts/auth/OAuth/credentials, рабочие Registry/медиа/cache, main, providers и services не менялись. `uploaded=false`, `remote_sync=UNVERIFIED`. Публикации, runner registration и PR merge отсутствуют.

**Один next action:** независимый owner/ChatGPT review этого пакета с синхронизацией CI timeout policy перед следующим GitHub CI gate; прослушивание/просмотр владельцем остаются обязательными для artistic acceptance. После final push работа остановлена.
