# Как пользоваться

## Открыть студию

В Obsidian выберите «Открыть папку как хранилище» и укажите папку `knowledge` в корне AI_VIDEO_02. Затем откройте [[START|START.md]]. Сторонние плагины не нужны. Obsidian в проверенной среде не найден; визуальная приёмка GUI = `not_checked`. Файлы можно читать и без приложения.

[[Studio_Map.canvas|Главное дерево]] содержит семь областей. Откройте карточку области и её ссылку «Открыть карту». [[areas/AREA-AUD|Аудио]] → [[maps/Audio.canvas|карта аудио]] → [[capabilities/AUD-ASR|ASR]]; [[capabilities/AUD-PROFILE|голос Ruslan]] находится рядом. Карточки показывают короткий раздел «Кратко»; полная заметка открывается двойным щелчком/командой открытия файла. В каждой заметке есть возврат к области и главной. Цвет означает область, готовность написана текстом.

[[maps/Video_Process.canvas|Процесс]] показывает исполнителя каждого этапа. [[generated/HISTORY|История]], [[generated/TASKS|задачи]] и [[generated/STATUS|состояние]] — срез единственного журнала `development/tasks.json`, а не редактируемая очередь. Новая карта ничего не запускает. Полный анализ новеллы и голоса персонажей остаются будущими возможностями.

## Команды из корня проекта

Используйте изолированное окружение `.venv-memory`, которое не меняет TTS и `.venv-core`. Все примеры ниже выполняются из AI_VIDEO_02. Технические предпочтения по-прежнему только в `config/production_profile.json`.

```powershell
.venv-memory/Scripts/python.exe -m app.project_memory status
.venv-memory/Scripts/python.exe -m app.project_memory task --id T04
.venv-memory/Scripts/python.exe -m app.project_memory context --id AUD-ASR --mode development --json
.venv-memory/Scripts/python.exe -m app.project_memory context --id AUD-PRONUNCIATION --mode development --json
.venv-memory/Scripts/python.exe -m app.project_memory context --id QA-DELIVERY --mode production --json
.venv-memory/Scripts/python.exe -m app.project_memory resolve --ref app/speech_review.py
.venv-memory/Scripts/python.exe -m app.project_memory validate
```

`context` не исполняет команды заметок. По умолчанию глубина requires = 1, максимум результатов Graphify = 8, ориентир 12 000 символов. `missing`, `truncated`, `omitted` и `graph_status` сообщают о недостающем и сокращённом. Расширяйте адресно: `--depth 2`, `--limit 12`, `--max-chars 20000`. Важные ограничения сохраняются целиком; если они сами превышают бюджет, возвращается `budget_exceeded`. Символы не равны токенам или расходу подписки.

В production сначала показываются существующие операции и ID зарегистрированных jobs/результатов. Код не вкладывается. Статус `done` не утверждает качество; QA/package второго выпуска ещё не подтверждают финальную замену Ruslan (T05–T06).

## Добавить предложение или область

Идея не создаёт и не закрывает исполняемую задачу. Примеры для вашего будущего добавления:

```powershell
.venv-memory/Scripts/python.exe -m app.project_memory new --kind idea --id IDEA-MY-QUESTION --title "Мой вопрос" --area AREA-OPS --related OPS-CODEMAP
.venv-memory/Scripts/python.exe -m app.project_memory new --kind capability --id AUD-MY-CAPABILITY --title "Моя возможность" --area AREA-AUD
.venv-memory/Scripts/python.exe -m app.project_memory new --kind area --id AREA-MY-AREA --title "Моя область"
.venv-memory/Scripts/python.exe -m app.project_memory new --kind decision --id DEC-MY-DECISION --title "Моё решение" --related OPS-OBSIDIAN
```

Заполните текст и свойства по [[templates/capability|шаблону возможности]], [[templates/area|области]], [[templates/idea|идеи]] или [[templates/decision|решения]]. Сохраняйте ID и имя файла; меняйте `title`, `aliases` и видимый заголовок. `parent`/`requires` не должны образовывать циклы. `area` ссылается на ID области; для области свойство `canvas` задаёт путь карты. Решения: proposed → accepted → superseded. Удаление не автоматизировано; для архивирования явно установите `archived: true`, затем согласуйте удаление старых ссылок вручную. Синхронизация сама не удаляет старые узлы.

[[ideas/IDEA-GRAPHIFY-QUESTIONS|Сохранённые вопросы к Graphify]] и [[ideas/IDEA-OBSIDIAN-NAVIGATION|улучшение навигации]] пока proposed. Журнал допускает только T00–T12; интерфейса добавления T13 пока нет. Для нового согласованного T-ID нужно отдельное поручение на расширение существующего Journal; второй планировщик не создаётся.

## Обновить карты и сохранить ручные правки

Сохраните работу и закройте Obsidian на время применения. Затем:

```powershell
.venv-memory/Scripts/python.exe -m app.project_memory validate
.venv-memory/Scripts/python.exe -m app.project_memory map-sync --dry-run
.venv-memory/Scripts/python.exe -m app.project_memory map-sync --apply
```

Dry-run сохраняет проверяемое предложение в `development/memory-sync-plan.json`. Apply проверяет хеши заметок, журнала и изменяемых файлов; предыдущие байты сохраняются в `development/memory-backups/<sha256>/`. Неизменённые карты не перезаписываются. Старые узлы, размеры, цвета, текст, неизвестные свойства и ручные рёбра сохраняются. Новые карточки занимают свободное место; новые области — резервную зону. При росте свыше 12 возможностей появляется подкарта.

При конфликте или запущенном Obsidian изменения Canvas сохраняются в `*.proposed.canvas`. Текущая карта остаётся на месте. Сравните предложение, закройте редактор и повторите dry-run/apply: обновление будет рассчитано по последним ручным правкам. Атомарная запись и проверка процесса не гарантируют защиту, если другой редактор начнёт запись в самый момент применения. Поэтому редакторы должны оставаться закрытыми. Старые предложения можно архивировать вручную после проверки.

Generated-страницы — целиком производные. Пользовательские мысли сохраняйте в areas/capabilities/ideas/decisions; синхронизация их текст не переписывает. Новую область или возможность не требуется добавлять в код генератора.

## Graphify и реализация функции

[[Code_Map|Вход в техническую карту]] → [[generated/code/graph.canvas|нативный Canvas Graphify]]. В обзорном Graph view Obsidian задайте фильтр **`-path:generated/code`**, чтобы сотни технических заметок не заслоняли семь областей. Фильтр документирован; глобальные настройки не менялись.

`graphify-out/graph.json` и `graphify-out/graph.html` находятся вне vault, в корне проекта. Открыть HTML можно через проводник; `resolve --ref graphify-out/graph.html` выдаст точный локальный путь. HTML интерактивен, но загружает vis-network с `unpkg.com`; для первого открытия требуется доступ к CDN. Полная автономность от сети не проверена. Код не публиковался. Экспортные Markdown-файлы и Canvas читаются локально без CDN.

Индекс обновляйте после изменения исходников или тестов, не после правки мысли или координат:

```powershell
.venv-memory/Scripts/python.exe -m app.code_map status
.venv-memory/Scripts/python.exe -m app.code_map refresh --dry-run
.venv-memory/Scripts/python.exe -m app.code_map refresh --apply
```

Адаптер сверяет `development/tooling.lock.json`, выставляет `GRAPHIFY_BACKEND=none` только дочернему процессу, выполняет `graphify extract . --code-only --max-workers 1` с временным каталогом вывода, затем нативные export html/obsidian. Выход сначала проверяется. В vault переносятся только заметки и Canvas под `generated/code`; `.obsidian` исключён. ID технических файлов берётся из настоящего графа, ссылки перенесены в namespace vault. Изменённые человеком экспортные файлы получают предложения, а не перезаписываются. Устаревшие файлы перечисляются для явного архивирования; автоматического удаления нет.

Реальные проверенные запросы:

```powershell
$env:GRAPHIFY_BACKEND='none'
.venv-memory/Scripts/graphify.exe explain app_tts_synthesize --graph graphify-out/graph.json
.venv-memory/Scripts/graphify.exe explain app_speech_review_review_build_speech --graph graphify-out/graph.json
.venv-memory/Scripts/graphify.exe explain app_timeline_build_timeline --graph graphify-out/graph.json
```

Версия: graphifyy 0.9.92, источник [Graphify-Labs/graphify](https://github.com/Graphify-Labs/graphify). Сохранены хеши корпуса, время, версия схемы, полные журналы и предупреждения. `app/__main__.py` сканируется, но символов не даёт: это явное предупреждение, не пропавший файл. При missing/stale графе контекст использует актуальные `code_refs` и не выдаёт старые строки за свежие.

## Передать задачу другому агенту

Передайте ID, режим и результат `task --id T04` или `context --id AUD-ASR --mode development --json`. Попросите сначала прочитать HANDOFF, tasks.json и только нужную задачу плана. T04–T06 требуют отдельного разрешения. Не передавайте весь vault, граф, книгу или журналы. Текущий проход заканчивается T07–T10; T11–T12 остаются pending.

## Откат

Исходные изменённые файлы сохранены в `reports/development/2026-10-17_MAP/before/`; последующие изменения карт/экспорта — в `development/memory-backups/`. Закройте редакторы, сравните текущий файл с резервной копией и восстановите только выбранный файл, если после снимка не было нужных ручных правок. Безопаснее извлечь копию отдельно и сравнить. Не заменяйте весь vault.

Journal не сбрасывайте старой копией tasks.json. При откате реализованной функции запишите `needs_review` через `Journal.update` с актуальным expected_hash, новым стабильным operation_key и сохранёнными ссылками на прежние доказательства. Выполните `python -m app.development sync` и map-sync. Новый адаптер и `.venv-memory` можно отключить; старые команды студии продолжают работать. Производственную БД, WAV, медиа, MLT, модели и runtime этот проход не меняет; восстанавливать их не требуется. Не применять reset --hard/clean.

[[START|Начало]]
