# MAP_REPORT — T07 → T08 → T09 → T10

Дата владельца: 2026-10-17 (Asia/Qyzylorda). Доказательства используют ISO UTC.
Разрешённый объём выполнен одним агентом. T00–T03 не повторялись; T04–T06 и T11–T12 остаются pending. Производство, синтез, полный рендер и импорт архива не запускались. Идеи остаются proposed.

## Результат

- T07: самостоятельный vault; 7 областей, 33 возможности, 4 шаблона, принятое решение о порядке, 2 предложенные идеи. Статусы реализации и проверки разделены; ограничения T04–T06 сохранены.
- T08: 9 стандартных редактируемых Canvas: Studio_Map, 7 областей и Video_Process. Карточки file-backed, раздел «Кратко»; Markdown-связи и возвраты существуют. Главная не перегружена возможностями. GUI Obsidian **not_checked**: приложение не найдено в sky.list_apps и стандартных каталогах; установка не выполнялась. Визуальная приёмка владельцем остаётся отдельной.
- T09: настоящий Graphify 0.9.92, Python 3.11.9, отдельная `.venv-memory`. 54 исходных файла/теста, 837 узлов, 2884 связей, схема графа 1. Три реальные explain-запроса проверены. Native export obsidian проверен в staging и перенесён только под generated/code с ID-файлами и исправленными vault-relative ссылками. `.obsidian` не копировался.
- T10: `python -m app.project_memory` — status/task/context/validate/map-sync/new/resolve. Переиспользован Journal; новая очередь не создана. Production начинает с операций и реальных ID, development — с выбранных источников. Все чтения доступны без Obsidian.

## Открыть

Vault: `C:\Users\diffe\OneDrive\Документы\ChatGPT\AI_VIDEO_02\knowledge`. В Obsidian: «Открыть папку как хранилище», затем [START](../../../knowledge/START.md).

[Главная карта](../../../knowledge/Studio_Map.canvas) · [Аудио](../../../knowledge/maps/Audio.canvas) · [Процесс](../../../knowledge/maps/Video_Process.canvas) · [Код](../../../knowledge/Code_Map.md) · [Инструкция](../../../knowledge/HOW_TO_USE.md).

Локальный HTML: `C:\Users\diffe\OneDrive\Документы\ChatGPT\AI_VIDEO_02\graphify-out\graph.html`. Двойной щелчок в проводнике открывает файл в браузере. HTML использует vis-network с unpkg.com; полная автономность от сети не подтверждена. Изображение [проверки HTML](graphify-html-asr.png) — свидетельство работы поиска и панели узла, не замена Canvas. Встроенный браузер успешно проверил HTML через временный сервер на 127.0.0.1; сервер остановлен. Прямой file:// URL отвергнут политикой встроенного браузера, обход не выполнялся. Код не опубликован.

## Проверки и версии

- [T07_checks.json](T07_checks.json): схема, уникальность, существующие ссылки, parent/requires без циклов. Версия `vault-sha256:5cc2172c4a97b1fa78629ded43304ff855842f1c51f76a51a6f30efe46c1b93b`.
- [T08_checks.json](T08_checks.json): Canvas, отсутствие перекрытий, путь главная → аудио → ASR и обратно; временное расширение/подкарта, новое название/алиас, два no-op sync без смены mtime; сохранены ручные координаты, размеры, цвет, неизвестные свойства, текст и рёбра. Конфликт/активный редактор → .proposed.canvas. Версия `canvas-sha256:5fe0029d13b2aa965d93192fa5f4cf3df412ccbcfae5d77de5d04f1f1fb0400b`.
- [T09_checks.json](T09_checks.json): реальные TTS/ASR/Timeline запросы; allowlist/покрытие новых модулей A; нативные ссылки; сохранность пользовательских заметок и .obsidian; реальный временный граф обнаружил изменение тестового кода. Unit-проверки покрывают удаление/переименование/добавление и подмену graph.json. Версия `corpus-sha256:8797ecc536cd33c58959ae502770252995bafacb0f6dcefbad4e83da22fcb82b`.
- [T10_checks.json](T10_checks.json): релевантность, бюджет, ошибки ID, fallback, кириллица/пробелы, чтение без приложения, production read-only. 18 тестов адаптера/журнала: 17 passed, 1 skipped (Windows не разрешает создание временного symlink). 94 теста прежнего быстрого набора passed. Наборы пересекаются по Journal; это не 112 независимых тестов.
- [Сохранность](preservation_after.json): 6 защищённых файлов совпали с хешами A; профиль/runtime/модель и прежние исходники совпали с начальным адресным снимком, кроме разрешённого app/development.py. Ревизии двух проектов и записанные в baseline поля десяти jobs не изменились. База читалась read-only; полный аудит таблиц и книги не выполнялся.
- [Манифест результата](artifact_manifest.json) фиксирует хеши заметок/карт. graphify-out/corpus.json хранит время индекса и хеши каждого исходника, включая незакоммиченные новые модули.

## Измерения

Контекст AUD-ASR: 3887 символов; произношение: 4166; production: 3310. По умолчанию depth=1, до 8 результатов, ориентир 12000 символов. Сокращения помечены truncated/omitted; важные ограничения не скрываются.
Финальное обновление Graphify: 19.75 с. Токены: null. Модель/стоимость/экономия подписки: null; не измерялись. Качество нового видео не оценивалось, нового видео нет.

## Ограничения

Obsidian GUI not_checked; T11/T12 не закрыты. T04–T06 остаются отдельно разрешаемой работой. Журнал ограничен T00–T12, расширение не реализовано. Глобальные настройки, hooks, watcher, multi-agent Graphify, Cloud и семантические модели не устанавливались/не запускались; backend none задан процессам, сетевую изоляцию не заявляем.
Graphify предупреждает: app/__main__.py сканируется, но не даёт символов. Полные предупреждения: [refresh.log](../../../graphify-out/refresh.log). После повторных структурных сканов 4 устаревших экспортных файла сохранены для явного архивирования, список в [graph-refresh-final.json](graph-refresh-final.json); автоматического удаления нет.
Атомарный rename не гарантирует отсутствие гонки с внешним редактором. При применении Obsidian и другие редакторы должны оставаться закрыты; обнаруженные конфликты сохраняют предложение.
Первый контроль реестра сравнил полный result с сокращённым baseline и был исправлен на сравнение только записанных полей; подробности в [диагностике](preservation-diagnostics.log). Изменений производственных данных не обнаружено в проверенном объёме.

## Команды и откат

Из корня AI_VIDEO_02:

```powershell
.venv-memory/Scripts/python.exe -m app.project_memory status
.venv-memory/Scripts/python.exe -m app.project_memory context --id AUD-ASR --mode development --json
.venv-memory/Scripts/python.exe -m app.project_memory map-sync --dry-run
.venv-memory/Scripts/python.exe -m app.project_memory map-sync --apply
.venv-memory/Scripts/python.exe -m app.code_map refresh --dry-run
.venv-memory/Scripts/python.exe -m app.code_map refresh --apply
```

Закрыть редакторы, сравнить нужный файл с `reports/development/2026-10-17_MAP/before/` или `development/memory-backups/<sha256>/`; восстанавливать только выбранные файлы, сохранив поздние ручные правки. Журнал не сбрасывать: восстановление реализации отражать через Journal.update с expected_hash, стабильным operation_key и сохранёнными доказательствами. Затем development sync и map-sync. База/медиа/WAV/MLT/runtime не изменялись и восстановления не требуют. Подробнее [HOW_TO_USE](../../../knowledge/HOW_TO_USE.md).

Остановиться после T07–T10. Не начинать B, T11/T12, новый ролик или реализацию идей.

Источники инструмента: [официальный репозиторий](https://github.com/Graphify-Labs/graphify), [extract](https://docs.graphify.com/reference/extract), локальный [--help](graphify-help.log). Команды установленных версий проверены локально.
