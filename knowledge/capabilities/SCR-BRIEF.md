---
schema_version: 1
id: SCR-BRIEF
kind: capability
title: Тема и замысел
area: AREA-SCR
parent: AREA-SCR
implementation: partial
verification: not_checked
verified_at: null
verified_revision: null
code_refs:
- app/contracts.py
evidence_refs: []
requires: []
related: []
task_ids: []
---

# Тема и замысел

## Кратко
**Тема и замысел**
Фиксирует тему и цель выпуска.
Состояние: частично; не проверено.
Ограничение: Замысел утверждает человек.
Далее: Согласовать brief.

## Назначение
ProjectBrief описывает тему, цель и параметры выпуска.

## Входы и выходы
Вход: выбранные материалы и текущие версии по ссылкам. Выход: тема и замысел в границах описанной реализации; результаты производства остаются в реестре.

## Реализация
- `app/contracts.py` — project-relative; разрешается адаптером context/resolve.

## Доказательства
Изучены интерфейсы исходников. Исполнение функции и production-проверка этим описанием не подтверждаются.

## Связи
[[areas/AREA-SCR|К области]] · [[Studio_Map.canvas|Главная карта]] · [[START|Начало]]


## Ограничения
Тему и художественный замысел утверждает человек.

## Будущие улучшения
Согласовать brief до производства.
