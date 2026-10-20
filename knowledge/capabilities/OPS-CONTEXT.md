---
schema_version: 1
id: OPS-CONTEXT
kind: capability
title: Контекст по узлу
area: AREA-OPS
parent: AREA-OPS
implementation: implemented
verification: tested
verified_at: '2026-10-17T14:07:31.644229+00:00'
verified_revision: corpus-sha256:8797ecc536cd33c58959ae502770252995bafacb0f6dcefbad4e83da22fcb82b
code_refs:
- app/project_memory.py
- app/development.py
- app/code_map.py
evidence_refs:
- reports/development/2026-10-17_MAP/T10_checks.json
requires: []
related: []
task_ids:
- T10
---

# Контекст по узлу

## Кратко
**Контекст по узлу**
Выдаёт компактный контекст по ID. Реализовано; локальные тесты passed. Ограничение: чтение ничего не запускает. Далее: передать ID разрешённой задачи.

## Назначение
Журнал T02 и короткий обмен T03 дают основу для адаптера контекста.

## Входы и выходы
Вход: выбранные материалы и текущие версии по ссылкам. Выход: контекст по узлу в границах описанной реализации; результаты производства остаются в реестре.

## Реализация
- `app/development.py` — project-relative; разрешается адаптером context/resolve.

## Доказательства
Изучены интерфейсы исходников. Исполнение функции и production-проверка этим описанием не подтверждаются.

## Связи
[[areas/AREA-OPS|К области]] · [[Studio_Map.canvas|Главная карта]] · [[START|Начало]]


## Ограничения
Контекст не запускает операции; ограничения T04–T06 сохраняются.

## Будущие улучшения
Использовать контекст только в границах разрешённой задачи.

Задачи: T10. Статус только в [[generated/TASKS|журнале задач]].
