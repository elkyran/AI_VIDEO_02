---
schema_version: 1
id: EDT-TIMELINE
kind: capability
title: Таймлайн проекта
area: AREA-EDT
parent: AREA-EDT
implementation: partial
verification: not_checked
verified_at: null
verified_revision: null
code_refs:
- app/timeline.py
- app/contracts.py
evidence_refs: []
requires: []
related: []
task_ids: []
graph_terms:
- build_timeline
- build_project
---

# Таймлайн проекта

## Кратко
**Таймлайн проекта**
Формирует Timeline и MLT.
Состояние: частично; не проверено.
Ограничение: Ручные правки нужно сохранять.
Далее: Проверить короткий временный проект.

## Назначение
build_timeline и build_project формируют Timeline и MLT.

## Входы и выходы
Вход: выбранные материалы и текущие версии по ссылкам. Выход: таймлайн проекта в границах описанной реализации; результаты производства остаются в реестре.

## Реализация
- `app/timeline.py` — project-relative; разрешается адаптером context/resolve.
- `app/contracts.py` — project-relative; разрешается адаптером context/resolve.

## Доказательства
Изучены интерфейсы исходников. Исполнение функции и production-проверка этим описанием не подтверждаются.

## Связи
[[areas/AREA-EDT|К области]] · [[Studio_Map.canvas|Главная карта]] · [[START|Начало]]


## Ограничения
Сохранять ручные правки и связи ресурсов.

## Будущие улучшения
Проверять проект на коротких временных данных.

[[generated/code/nodes/app_timeline_build_timeline|Техническая реализация в Graphify]]
