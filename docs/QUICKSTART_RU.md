# Как пользоваться готовым MVP

Обновлено 16 октября 2026 года. Текущий выпуск «Мир без Рудеуса» имеет статус `ready_for_review`; прослушивание человеком и принятие пользователем ещё не выполнены.

## Посмотреть результат

В папке проекта:

- `output/world_without_rudeus_ruslan.mp4` — актуальный рассказ «Мир без Рудеуса: кем стали бы остальные» с возвращённым голосом Ruslan, около 8 минут 21 секунды, 1920×1080 / 30 fps. Открыть обычным видеоплеером.
- `output/world_without_rudeus_ruslan.srt` — субтитры к текущему видео. Редактируемый проект с новым голосом: `workspace/studio/voice_replacements/ruslan_final/project/project.mlt`; прежние архивы ниже относятся к версии до замены голоса.
- `output/world_without_rudeus_editable.zip` — распаковать целиком; открыть находящийся внутри `project.mlt` в Shotcut. Папку `media` оставить рядом: она нужна редактору. В комплекте также находятся отдельные WAV, основной и сырой SRT, сценарий, источники и отчёты.
- `output/universal_video_studio_mvp_motion.zip` — снимок исходников до возвращения голоса Ruslan; текущие инструменты замены голоса находятся в этом рабочем каталоге. Это не автономный установщик с уже загруженными моделями; установка описана в [INSTALL.md](INSTALL.md).

Первый 73-секундный технический пилот сохранён отдельно как `output/first_video.mp4` и `output/mushoku_pilot_package.zip`. Его результаты проверок не подменяют QA текущего выпуска.

Для ручного монтажа сначала скопируйте всю распакованную папку и работайте с копией. Отдельный MLT без медиа непереносим.

## Продолжить работу на этом компьютере

Откройте PowerShell в корне проекта. Здесь окружение и рабочая область уже подготовлены:

```powershell
$studioPy = ".\.venv-core\Scripts\python.exe"
$studioWorkspace = "workspace/studio"
$videoProjectId = "world_without_rudeus"
& $studioPy -m app --workspace $studioWorkspace status $videoProjectId
```

`revision` — текущая версия; `jobs` — сохранённые производственные задания. Работу можно продолжать после закрытия чата. Не вводите старую ревизию из прежнего примера.

## Исправить одну реплику

ID текущего сценария на момент выдачи — `without_rudeus_video_v4`; первый блок — `b01`. Перед запуском замените текст в примере на свою полную реплику:

```powershell
$videoScriptId = "without_rudeus_video_v4"
$videoState = & $studioPy -m app --workspace $studioWorkspace status $videoProjectId | ConvertFrom-Json
$videoPatchKey = "patch-" + [guid]::NewGuid().ToString("N")
$videoPatch = & $studioPy -m app --workspace $studioWorkspace patch $videoProjectId $videoScriptId b01 --text "Здесь полный новый текст первой реплики." --revision $videoState.revision --key $videoPatchKey | ConvertFrom-Json
$videoScriptId = $videoPatch.script_asset_id
```

Ответ возвращает новый `script_asset_id`. Используйте его дальше и для следующей правки. Прежний сценарий сохраняется; зависимые задания получают статус `stale`. Неизменённая озвучка может использоваться из проверенного кэша, но монтаж и MP4 нужно собрать заново.

## Запустить следующие этапы

Один раз добавьте небольшую функцию: она сохраняет JSON-параметры и каждый раз читает актуальную ревизию.

```powershell
function Start-VideoJob($operation, $parameters) {
    $videoRunKey = $operation + "-" + [guid]::NewGuid().ToString("N")
    $videoRequest = "requests/$videoRunKey.json"
    New-Item -ItemType Directory -Force -Path "$studioWorkspace/requests" | Out-Null
    $parameters | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath "$studioWorkspace/$videoRequest"
    $videoState = & $studioPy -m app --workspace $studioWorkspace status $videoProjectId | ConvertFrom-Json
    & $studioPy -m app --workspace $studioWorkspace run $videoProjectId $operation --input $videoRequest --revision $videoState.revision --key $videoRunKey | ConvertFrom-Json
}
```

Выполняйте по одной строке. **Перед следующим этапом дождитесь `state: done` предыдущего.** Перед упаковкой дополнительно проверьте результат QA: `ready_for_review` допускает дальнейший просмотр, `blocked` требует устранить замечания. Само завершение задания не означает успешную проверку.

| Этап | Команда |
|---|---|
| Озвучка | `$speechJob = Start-VideoJob "synthesize" @{script_asset_id=$videoScriptId}` |
| Монтаж | `$buildJob = Start-VideoJob "build" @{script_asset_id=$videoScriptId; audio_job_id=$speechJob.job_id}` |
| Экспорт MP4 | `$renderJob = Start-VideoJob "render" @{build_job_id=$buildJob.job_id}` |
| Проверки и субтитры | `$qaJob = Start-VideoJob "qa" @{render_job_id=$renderJob.job_id}` |
| Переносимый комплект | `$packageJob = Start-VideoJob "package" @{qa_job_id=$qaJob.job_id}` |

Проверка состояния, например озвучки:

```powershell
& $studioPy -m app --workspace $studioWorkspace job $speechJob.job_id
```

Для остальных этапов подставьте соответствующую переменную. После `package` результат задания содержит новые пути `video`, `project`, `archive` относительно `workspace/studio`. Эти новые файлы не перезаписывают автоматически уже выданные файлы в `output`.

Если задание завершилось ошибкой, прочитайте `error`. После устранения причины: `resume JOB_ID`; для остановки: `cancel JOB_ID` — с теми же `python -m app --workspace ...`. Устаревшее задание `stale` возобновить нельзя: создайте цепочку для нового сценария. Повторный запуск `Start-VideoJob` создаёт новый запрос; для продолжения существующего используйте его сохранённый `job_id`.

## Что пока ограничено

Текущий выпуск — `editorial_review` по выбранным прочитанным источникам, а статус покрытия остаётся `partial`. Полные WN и LN не прочитаны и не сопоставлены; рубеж `full_episode` не завершён, хотя само видео длится более восьми минут. Видеофрагменты и изображения из аниме имеют сохранённые ссылки и атрибуцию; их права остаются `unknown`, пакет предназначен для локального просмотра без публикации и распространения. Голос локальный, его условия использования описаны в [LICENSES.md](LICENSES.md). Технический QA пройден, но предупреждения ASR об именах и словах сохранены; это не заменяет ваше прослушивание и принятие. Отдельной панели управления пока нет; установку на другом компьютере и работу Claude/Gemini ещё предстоит проверить. Подробности — в [current_state.md](current_state.md).
