# Реальные интерфейсы студии и разрешённых задач развития

Корень — существующий AI_VIDEO_02. Рабочая область студии — `workspace/studio`.
Python ядра — `.venv-core/Scripts/python.exe`; `studio.cmd` вызывает тот же `app.cli`.
Никакой команды создания нового выпуска в пакете A не запускалось.

## Существующий CLI (сохранён без изменений)

```powershell
studio.cmd --help
studio.cmd --workspace workspace/studio status roxy_rudeus_story
studio.cmd --workspace workspace/studio status world_without_rudeus
studio.cmd --workspace workspace/studio job JOB_ID
studio.cmd --workspace workspace/studio run PROJECT_ID OPERATION --input REQUEST.json --revision REVISION --key STABLE_KEY
```

`run`: synthesize, build, render, qa, package. Входные JSON и пути артефактов
относительны workspace. Это реальные производственные операции: примеры `run`
здесь являются справкой, а не разрешением запуска.
`cancel JOB_ID` отменяет работу; `resume JOB_ID` явно перезапускает допустимую
прерванную работу. Повтор ключа требует исходной ревизии и тех же параметров.

`scripts/fit_narration.py` и `scripts/replace_narration.py` пока отдельные инструменты.
Их интеграция в реестр и актуальность финальной упаковки относятся к T05–T06.
Второй выпуск: зарегистрированные QA/package предшествуют внешней замене на Ruslan.
Нельзя показывать их как QA/package окончательного варианта с возвращённым голосом.

## Профиль T01

```powershell
.venv-core/Scripts/python.exe -m app.production_profile --check-runtime
```

Источник предпочтений — `config/production_profile.json`. Производственный
`runtime.json` сохранён без изменений и содержит локаторы установленного окружения.
Валидатор проверяет структуру, доказательства/хеши, базовые параметры, модель и
согласованность эффективных runtime-настроек. Ничего не синтезирует и не исправляет молча.
Версия Piper в профиле — записанная версия исходного синтеза, не новый тест качества.
Параметры отдельных fitted takes остаются в финальном манифесте; они не становятся
новым глобальным темпом. Словарь здесь один; runtime проверяется на совпадение.
Старый CLI не получает новых запретов; guard действует в новом production chain.
Соглашение production/development в AGENTS само по себе не ограничивает shell.

## Прогресс T02

Эквивалент предложенного пути `tools/project_memory/` для раннего пакета —
`app/development.py`: он входит в существующий пакет app и не конфликтует с
игнорируемым каталогом сторонних бинарников tools/. CLI контекста и карты T10 описан ниже.

```powershell
.venv-core/Scripts/python.exe -m app.development status
.venv-core/Scripts/python.exe -m app.development sync
```

`Journal(root).initialize(plan_ref, run_ref)` сохраняет существующий журнал,
отклоняет подмену плана. `read()` возвращает `(state, sha256)`.
`update(task_id, status, expected_hash=sha256, operation_key=stable_key,
result_refs=[...], test_refs=[...], revision=..., blocker=...)` работает под OS-lock
и заменяет JSON атомарно. Повтор ключа с тем же содержимым не меняет результат.
Для done JSON по каждому test_ref обязан содержать `status: passed`, совпадающие
`task_id` и `revision`; файлы результата должны существовать.
STATUS/HANDOFF — производные. После прерывания между JSON и представлением выполнить sync.
Исходная зависимость T07 от T06 выражала порядок «после B», а не техническую предпосылку.
T12 пока pending; обработка неполных требований будет решена в разрешённом D.
Новое явное поручение владельца от 2026-10-17 разрешило только T07 → T08 → T09 → T10.
`Journal.authorize_map_batch(expected_hash=..., operation_key=..., decision_ref=..., run_ref=...)`
сохраняет старую зависимость в `map_authorization`, задаёт T07→T02 и `authorized_task_ids`.
T04–T06 остаются pending, T11–T12 не разрешены. Проверки зависимостей и done сохранены.
История новых переходов хранится в том же tasks.json; прежние хеши операций не сбрасываются.

## Короткий обмен T03

Отдельная тонкая обёртка сохраняет старый CLI:

```powershell
.venv-core/Scripts/python.exe -m app.exchange --workspace workspace/studio job JOB_ID --task-id T03
.venv-core/Scripts/python.exe -m app.exchange --workspace workspace/studio wait JOB_ID --timeout 30 --task-id T03
```

Новый `chain` принимает PROJECT_ID SCRIPT_ASSET_ID, `--revision ORIGINAL_REVISION`
и `--key STABLE_KEY`; выполняет существующие synthesize → build → render → qa → package.
Это справка для отдельного будущего производственного задания, не команда запуска пакета A.
Production — режим по умолчанию с проверкой профиля перед новыми этапами.
Для имитационных/отдельно разрешённых задач: `--mode development --task-id ID`,
необязательный `--repair-reason TEXT`. Новый режим не является sandbox.

После timeout или Ctrl+C повторять тот же chain с теми же ID, исходной revision
и key. Квитанции проверяются в существующем requests; новый scheduler/queue не создаётся.
Ошибка, stale, blocked и QA blocked останавливают продолжение. Повтор не вызывает resume.
Если pending worker не стартовал или был занят другим заданием, повтор только читает
его состояние: восстановление существующей очереди требует отдельной диагностики.
Чужая правка revision между этапами возвращает конфликт, не принимается молча.

`state` — состояние job, `quality_status` — результат проверки, `wait_status` —
completed/timeout/cancelled. Отмена ожидания не вызывает cancel job и не удаляет результат.
Deadline общий для всей цепочки (0–3600 секунд); время OS/SQLite вызова может немного
превысить deadline. Worker продолжает работать после выхода клиента.
Если deadline истёк до первой постановки, state=not_started, job_id=null.
Код выхода 2 означает ошибку; timeout/cancelled требуют чтения wait_status, код выхода 0.

Обязательные поля ответа: task_id, job_id, state, quality_status, artifacts,
warnings, errors, log_ref, next_action; дополнительно wait_status, metrics, truncated.
Полный JSON-журнал — `exchange_logs/` внутри выбранного workspace. Это не новые jobs.
Метрики разделяют время клиента/задания, повтор, attempts, cache_hits и причину правки;
недоступные модель/токены — null. Размер текста не является стоимостью или квотой.
Локальные журналы не предназначены для публикации: ошибки среды могут содержать её пути.

## Исходные свидетельства и восстановление

Срез существующего реестра: `reports/development/2026-10-17_A/production_baseline.json`.
Baseline и контроль сохранности: `reports/development/2026-10-17_A/baseline.json`.
В нём ID обоих выпусков, действительные пути и SHA-256 снимка исходников.
До начала Git был без коммитов, исходники untracked. Версия определяется снимком,
не вымышленным commit SHA; существующая Git-идентичность не менялась.

Откат: остановить относящиеся к изменению процессы, извлечь baseline_sources.zip
в отдельную временную папку и сравнить только нужные файлы. Восстанавливать файл
только если он не менялся владельцем позже. Добавленные модули/инструкции можно
отключить отдельно; старые app/cli.py, jobs.py, operations.py и runtime не менялись.
База в этом пакете не изменяется и обычно не требует восстановления. При аварийной
необходимости закрыть процессы и восстановить её через SQLite backup API из
registry.backup.sqlite, сохранив отдельную копию текущей базы. Не копировать DB
поверх активного WAL, не применять reset --hard/clean. Снимки исключены из Git.


## Карта и контекст T07–T10

Отдельная среда `.venv-memory` (Python 3.11, PyYAML 6.0.3, graphifyy 0.9.92).
Точные зависимости: `development/tooling.lock.json`. `.venv-core` и TTS не меняются.
Все команды выполняются из корня AI_VIDEO_02:

```powershell
.venv-memory/Scripts/python.exe -m app.project_memory status
.venv-memory/Scripts/python.exe -m app.project_memory task --id T04
.venv-memory/Scripts/python.exe -m app.project_memory context --id AUD-ASR --mode development --json
.venv-memory/Scripts/python.exe -m app.project_memory context --id QA-DELIVERY --mode production --json
.venv-memory/Scripts/python.exe -m app.project_memory validate
.venv-memory/Scripts/python.exe -m app.project_memory map-sync --dry-run
.venv-memory/Scripts/python.exe -m app.project_memory map-sync --apply
.venv-memory/Scripts/python.exe -m app.project_memory new --kind idea --id IDEA-EXAMPLE --title "Идея" --area AREA-OPS --related OPS-CODEMAP
.venv-memory/Scripts/python.exe -m app.project_memory resolve --ref app/speech_review.py
.venv-memory/Scripts/python.exe -m app.code_map status
.venv-memory/Scripts/python.exe -m app.code_map refresh --dry-run
.venv-memory/Scripts/python.exe -m app.code_map refresh --apply
```

`new` поддерживает area/capability/idea/decision; существующий ID не перезаписывает.
Пример IDEA-EXAMPLE — справка, не уже созданная идея. Идеи не становятся jobs или T-ID.
Журнал по-прежнему принимает только T00–T12. Новые T-ID требуют отдельного изменения Journal.
`--root PATH` у project_memory ставится перед подкомандой. ID ищется также по точному
названию/алиасу; неоднозначный или неизвестный ID даёт код выхода 2.

Контекст: depth=1, limit=8, max-chars=12000; допускаются явные адресные расширения.
Полные ограничения не обрезаются; при превышении обязательными ограничениями бюджета
есть `budget_exceeded`. `missing`, `truncated`, `omitted`, `graph_status` честно отражают
отсутствующее. Graphify missing/stale → актуальные code_refs, без старых результатов графа.
Production возвращает сначала готовые операции и ID из read-only SQLite; код не вкладывается.
Ни чтение, ни new не запускают модели, синтез, рендер или команды из текста заметки.

Map-sync работает по сохранённому dry-run, проверяет версии и создаёт резервные копии.
Существующие узлы/рёбра/координаты/цвета/размеры/неизвестные свойства сохраняются.
Новые узлы добавляются на свободное место. Редактор должен быть закрыт на всё применение;
при запущенном Obsidian или конфликте создаётся .proposed.canvas. Atomic replace не является
гарантией CAS с другим приложением. Generated-страницы производны; заметки владельца
хранить вне generated. Автоматического удаления нет; архивирование только явное.

Graphify выполняется только локальным CLI с GRAPHIFY_BACKEND=none и --code-only,
в свежем временном output. Corpus: исходники app/scripts и тесты, только .py/.ps1;
закрытый allowlist в .graphifyignore. Native export obsidian идёт в staging, затем
проверенные ссылки/Canvas переносятся только в knowledge/generated/code. .obsidian не копируется.
Сохранены версия схемы, хеши корпуса, предупреждения и полный лог graphify-out/refresh.log.
Изменение/добавление/удаление/переименование кода делает индекс stale. Не обновлять по правке заметки.
HTML использует vis-network CDN; отсутствие внешней сети не проверено. Код не опубликован.

Инструкция: knowledge/HOW_TO_USE.md. Отчёт: reports/development/2026-10-17_MAP/MAP_REPORT.md.
GUI Obsidian not_checked: приложение не найдено; JSON и локальные тесты не являются визуальным принятием.
