---
schema_version: 1
id: QA-FACTS
kind: capability
title: Проверка фактов
area: AREA-QA
parent: AREA-QA
implementation: partial
verification: not_checked
verified_at: null
verified_revision: null
code_refs:
- app/contracts.py
- app/library.py
evidence_refs: []
requires: []
related: []
task_ids: []
---

# Проверка фактов

## Кратко
**Проверка фактов**
Проверяет ссылки на доказательства.
Состояние: частично; не проверено.
Ограничение: Ссылка не гарантирует верный смысл.
Далее: Сверить факт с первоисточником.

## Назначение
SourceRef и validate_ref связывают утверждения с материалами.

## Входы и выходы
Вход: выбранные материалы и текущие версии по ссылкам. Выход: проверка фактов в границах описанной реализации; результаты производства остаются в реестре.

## Реализация
- `app/contracts.py` — project-relative; разрешается адаптером context/resolve.
- `app/library.py` — project-relative; разрешается адаптером context/resolve.

## Доказательства
Изучены интерфейсы исходников. Исполнение функции и production-проверка этим описанием не подтверждаются.

## Связи
[[areas/AREA-QA|К области]] · [[Studio_Map.canvas|Главная карта]] · [[START|Начало]]


## Ограничения
Наличие ссылки не доказывает смысловую верность.

## Будущие улучшения
Проверить факт по конкретному фрагменту.
