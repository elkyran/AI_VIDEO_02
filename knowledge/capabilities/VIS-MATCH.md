---
schema_version: 1
id: VIS-MATCH
kind: capability
title: Соответствие кадра тексту
area: AREA-VIS
parent: AREA-VIS
implementation: partial
verification: not_checked
verified_at: null
verified_revision: null
code_refs:
- app/contracts.py
- app/operations.py
evidence_refs: []
requires: []
related: []
task_ids: []
---

# Соответствие кадра тексту

## Кратко
**Соответствие кадра тексту**
Связывает кадры с репликами.
Состояние: частично; не проверено.
Ограничение: Смысл оценивает ИИ и человек.
Далее: Сверить кадр с текстом.

## Назначение
ShotPlan и ProductionVisualShot задают выбор и длительность кадра.

## Входы и выходы
Вход: выбранные материалы и текущие версии по ссылкам. Выход: соответствие кадра тексту в границах описанной реализации; результаты производства остаются в реестре.

## Реализация
- `app/contracts.py` — project-relative; разрешается адаптером context/resolve.
- `app/operations.py` — project-relative; разрешается адаптером context/resolve.

## Доказательства
Изучены интерфейсы исходников. Исполнение функции и production-проверка этим описанием не подтверждаются.

## Связи
[[areas/AREA-VIS|К области]] · [[Studio_Map.canvas|Главная карта]] · [[START|Начало]]


## Ограничения
Семантическое соответствие оценивают ИИ и человек.

## Будущие улучшения
Сверить выбранный кадр с репликой.
