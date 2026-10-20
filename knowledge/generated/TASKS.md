# Задачи

Срез журнала: 2026-10-17T14:11:25.448896+00:00; generation=21; SHA-256=167a854ff2a4017fbe8361334e434967a39823248c5ddfef59e86fa418edbe21.
Источник: `development/tasks.json`. Производные данные; обновление явной командой map-sync.

| ID | Статус | Предпосылки |
|---|---|---|
| T00 | done | — |
| T01 | done | T00 |
| T02 | done | T01 |
| T03 | done | T02 |
| T04 | pending | T03 |
| T05 | pending | T03, T04 |
| T06 | pending | T05 |
| T07 | done | T02 |
| T08 | done | T07 |
| T09 | done | T00, T07 |
| T10 | done | T03, T07, T08 |
| T11 | pending | T10 |
| T12 | pending | T00, T01, T02, T03, T04, T05, T06, T07, T08, T09, T10, T11 |

Определение: `python -m app.project_memory task --id T04`.
Журнал допускает только T00–T12; идеи не создают исполняемые задачи.

[[START|На главную]]
