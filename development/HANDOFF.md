# HANDOFF

Сформировано из development/tasks.json; generation=21. Не редактировать отдельно.

Корень: AI_VIDEO_02 (каталог над development/).
Разрешённый пакет: C+T10. Следующий доступный ID: T04.
Закрыты: T00, T01, T02, T03, T07, T08, T09, T10.
Профиль: [config/production_profile.json](../config/production_profile.json).
Интерфейсы: [INTERFACES.md](INTERFACES.md). План: [01_DEVELOPMENT_PLAN.md](01_DEVELOPMENT_PLAN.md).

Изменения и результаты — ссылки из единственного журнала:
- T00 (done): [development/INTERFACES.md](../development/INTERFACES.md); [reports/development/2026-10-17_A/baseline.json](../reports/development/2026-10-17_A/baseline.json); [reports/development/2026-10-17_A/production_baseline.json](../reports/development/2026-10-17_A/production_baseline.json)
- T01 (done): [AGENTS.md](../AGENTS.md); [config/production_profile.json](../config/production_profile.json); [development/INTERFACES.md](../development/INTERFACES.md)
- T02 (done): [app/development.py](../app/development.py); [development/HANDOFF.md](../development/HANDOFF.md); [development/STATUS.md](../development/STATUS.md); [development/tasks.json](../development/tasks.json)
- T03 (done): [app/exchange.py](../app/exchange.py); [development/INTERFACES.md](../development/INTERFACES.md); [reports/development/2026-10-17_A/exchange_smoke.json](../reports/development/2026-10-17_A/exchange_smoke.json)
- T07 (done): [knowledge/START.md](../knowledge/START.md); [knowledge/capabilities/AUD-ASR.md](../knowledge/capabilities/AUD-ASR.md); [knowledge/decisions/DEC-MAP-ORDER.md](../knowledge/decisions/DEC-MAP-ORDER.md)
- T08 (done): [app/project_memory.py](../app/project_memory.py); [knowledge/Studio_Map.canvas](../knowledge/Studio_Map.canvas); [knowledge/maps/Audio.canvas](../knowledge/maps/Audio.canvas); [knowledge/maps/Video_Process.canvas](../knowledge/maps/Video_Process.canvas)
- T09 (done): [app/code_map.py](../app/code_map.py); [development/tooling.lock.json](../development/tooling.lock.json); [graphify-out/corpus.json](../graphify-out/corpus.json); [graphify-out/graph.html](../graphify-out/graph.html); [graphify-out/graph.json](../graphify-out/graph.json); [knowledge/generated/code/graph.canvas](../knowledge/generated/code/graph.canvas)
- T10 (done): [app/project_memory.py](../app/project_memory.py); [development/INTERFACES.md](../development/INTERFACES.md); [knowledge/HOW_TO_USE.md](../knowledge/HOW_TO_USE.md); [reports/development/2026-10-17_MAP/MAP_REPORT.md](../reports/development/2026-10-17_MAP/MAP_REPORT.md)

Ограничения: production не запускался; нового видео нет. Проверки моделей/рендера имитированы либо исключены.
Токены/настройки модели, недоступные из структурированного источника, — null.
Финальная замена Ruslan второго выпуска пока вне штатной цепочки QA/package (T05–T06).
Исторические статусы: читать существующий registry.sqlite; исходный срез — production_baseline.json в отчёте.
Graphify/Obsidian и T04–T12 не выполнялись в пакете A.

Точное следующее действие: Остановиться после T07–T10. T04–T06 и T11–T12 требуют отдельного поручения; идеи не реализовывать.
Не повторять аудит и выполненный пакет. Сначала читать этот HANDOFF и tasks.json.
Отчёт/откат: [reports/development/2026-10-17_MAP/REPORT.md](../reports/development/2026-10-17_MAP/REPORT.md).

Текущее разрешение: T07 → T08 → T09 → T10; T04–T06 не закрывать.
Решение: [knowledge/decisions/DEC-MAP-ORDER.md](../knowledge/decisions/DEC-MAP-ORDER.md).
Карта, проверки, ограничения и откат: [reports/development/2026-10-17_MAP/MAP_REPORT.md](../reports/development/2026-10-17_MAP/MAP_REPORT.md).
