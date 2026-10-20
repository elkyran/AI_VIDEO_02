---
schema_version: 1
id: SCR-BLOCKS
kind: capability
title: Блоки сценария
area: AREA-SCR
parent: AREA-SCR
implementation: partial
verification: not_checked
verified_at: null
verified_revision: null
code_refs:
- app/operations.py
- app/contracts.py
evidence_refs: []
requires: []
related: []
task_ids: []
---

# Блоки сценария

## Кратко
**Блоки сценария**
Описывает текст и визуалы блоков.
Состояние: частично; не проверено.
Ограничение: Правки требуют актуальной ревизии.
Далее: Проверить контракт блока.

## Назначение
ProductionScript и ProductionBlock описывают текст, визуалы и длительности.

## Входы и выходы
Вход: выбранные материалы и текущие версии по ссылкам. Выход: блоки сценария в границах описанной реализации; результаты производства остаются в реестре.

## Реализация
- `app/operations.py` — project-relative; разрешается адаптером context/resolve.
- `app/contracts.py` — project-relative; разрешается адаптером context/resolve.

## Доказательства
Изучены интерфейсы исходников. Исполнение функции и production-проверка этим описанием не подтверждаются.

## Связи
[[areas/AREA-SCR|К области]] · [[Studio_Map.canvas|Главная карта]] · [[START|Начало]]


## Ограничения
Правка блока требует согласованной ревизии проекта.

## Будущие улучшения
Использовать текущий контракт; не запускать выпуск этим заданием.
