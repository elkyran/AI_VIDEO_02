---
schema_version: 1
id: VIS-PREP
kind: capability
title: Подготовка кадров
area: AREA-VIS
parent: AREA-VIS
implementation: partial
verification: not_checked
verified_at: null
verified_revision: null
code_refs:
- app/media.py
- app/timeline.py
evidence_refs: []
requires: []
related: []
task_ids: []
---

# Подготовка кадров

## Кратко
**Подготовка кадров**
Готовит кадры и ресурсы проекта.
Состояние: частично; не проверено.
Ограничение: Оригиналы не перезаписываются.
Далее: Создавать производные отдельно.

## Назначение
framing_filter и staging готовят кадр и переносимые ресурсы проекта.

## Входы и выходы
Вход: выбранные материалы и текущие версии по ссылкам. Выход: подготовка кадров в границах описанной реализации; результаты производства остаются в реестре.

## Реализация
- `app/media.py` — project-relative; разрешается адаптером context/resolve.
- `app/timeline.py` — project-relative; разрешается адаптером context/resolve.

## Доказательства
Изучены интерфейсы исходников. Исполнение функции и production-проверка этим описанием не подтверждаются.

## Связи
[[areas/AREA-VIS|К области]] · [[Studio_Map.canvas|Главная карта]] · [[START|Начало]]


## Ограничения
Исходники и ручные кадрирования нельзя перезаписывать.

## Будущие улучшения
Создавать производные материалы отдельно.
