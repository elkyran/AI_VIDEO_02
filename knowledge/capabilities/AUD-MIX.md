---
schema_version: 1
id: AUD-MIX
kind: capability
title: Музыка и баланс
area: AREA-AUD
parent: AREA-AUD
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

# Музыка и баланс

## Кратко
**Музыка и баланс**
Собирает музыку и речь.
Состояние: частично; не проверено.
Ограничение: Баланс требует прослушивания.
Далее: Проверить уровни.

## Назначение
original_music и Timeline описывают музыкальную дорожку и сборку звука.

## Входы и выходы
Вход: выбранные материалы и текущие версии по ссылкам. Выход: музыка и баланс в границах описанной реализации; результаты производства остаются в реестре.

## Реализация
- `app/media.py` — project-relative; разрешается адаптером context/resolve.
- `app/timeline.py` — project-relative; разрешается адаптером context/resolve.

## Доказательства
Изучены интерфейсы исходников. Исполнение функции и production-проверка этим описанием не подтверждаются.

## Связи
[[areas/AREA-AUD|К области]] · [[Studio_Map.canvas|Главная карта]] · [[START|Начало]]


## Ограничения
Автоматический баланс не заменяет прослушивание.

## Будущие улучшения
Проверять уровни на актуальной версии.
