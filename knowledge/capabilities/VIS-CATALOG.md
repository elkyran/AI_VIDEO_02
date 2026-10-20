---
schema_version: 1
id: VIS-CATALOG
kind: capability
title: Архив визуалов
area: AREA-VIS
parent: AREA-VIS
implementation: partial
verification: not_checked
verified_at: null
verified_revision: null
code_refs:
- app/registry.py
- app/contracts.py
evidence_refs: []
requires: []
related: []
task_ids: []
---

# Архив визуалов

## Кратко
**Архив визуалов**
Регистрирует визуальные активы.
Состояние: частично; не проверено.
Ограничение: Полная каталогизация не подтверждена.
Далее: Выбрать нужные материалы.

## Назначение
Реестр хранит активы и ссылки на исходники.

## Входы и выходы
Вход: выбранные материалы и текущие версии по ссылкам. Выход: архив визуалов в границах описанной реализации; результаты производства остаются в реестре.

## Реализация
- `app/registry.py` — project-relative; разрешается адаптером context/resolve.
- `app/contracts.py` — project-relative; разрешается адаптером context/resolve.

## Доказательства
Изучены интерфейсы исходников. Исполнение функции и production-проверка этим описанием не подтверждаются.

## Связи
[[areas/AREA-VIS|К области]] · [[Studio_Map.canvas|Главная карта]] · [[START|Начало]]


## Ограничения
Импорт всего архива и полная семантическая каталогизация не подтверждены.

## Будущие улучшения
Регистрировать только выбранные материалы.
