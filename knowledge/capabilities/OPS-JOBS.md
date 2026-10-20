---
schema_version: 1
id: OPS-JOBS
kind: capability
title: Задания и кеш
area: AREA-OPS
parent: AREA-OPS
implementation: implemented
verification: historical_evidence
verified_at: null
verified_revision: null
code_refs:
- app/jobs.py
- app/registry.py
- app/exchange.py
evidence_refs:
- reports/development/2026-10-17_A/T03_checks.json
requires: []
related: []
task_ids:
- T02
- T03
graph_terms:
- JobManager
- enqueue
- chain
---

# Задания и кеш

## Кратко
**Задания и кеш**
Использует существующий реестр и кеш.
Состояние: реализовано; исторические данные.
Ограничение: Timeout не отменяет job.
Далее: Прочитать состояние по ID.

## Назначение
JobManager и Exchange используют существующую очередь и реестр.

## Входы и выходы
Вход: выбранные материалы и текущие версии по ссылкам. Выход: задания и кеш в границах описанной реализации; результаты производства остаются в реестре.

## Реализация
- `app/jobs.py` — project-relative; разрешается адаптером context/resolve.
- `app/registry.py` — project-relative; разрешается адаптером context/resolve.
- `app/exchange.py` — project-relative; разрешается адаптером context/resolve.

## Доказательства
- `reports/development/2026-10-17_A/T03_checks.json`

## Связи
[[areas/AREA-OPS|К области]] · [[Studio_Map.canvas|Главная карта]] · [[START|Начало]]


## Ограничения
Журнал развития отдельно от jobs; timeout не отменяет работу.

## Будущие улучшения
Читать job по ID; не создавать вторую очередь.

Задачи: T02, T03. Статус только в [[generated/TASKS|журнале задач]].
