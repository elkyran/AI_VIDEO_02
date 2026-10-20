---
schema_version: 1
id: SRC-CLAIMS
kind: capability
title: События и доказательства
area: AREA-SRC
parent: AREA-SRC
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

# События и доказательства

## Кратко
**События и доказательства**
Связывает утверждение с источником.
Состояние: частично; не проверено.
Ограничение: Схема не доказывает истинность.
Далее: Проверить цитату.

## Назначение
Контракт Claim связывает утверждения с SourceRef; Library проверяет ссылки.

## Входы и выходы
Вход: выбранные материалы и текущие версии по ссылкам. Выход: события и доказательства в границах описанной реализации; результаты производства остаются в реестре.

## Реализация
- `app/contracts.py` — project-relative; разрешается адаптером context/resolve.
- `app/library.py` — project-relative; разрешается адаптером context/resolve.

## Доказательства
Изучены интерфейсы исходников. Исполнение функции и production-проверка этим описанием не подтверждаются.

## Связи
[[areas/AREA-SRC|К области]] · [[Studio_Map.canvas|Главная карта]] · [[START|Начало]]


## Ограничения
Корректная схема не подтверждает истинность утверждения.

## Будущие улучшения
Сверять конкретное утверждение с источником.
