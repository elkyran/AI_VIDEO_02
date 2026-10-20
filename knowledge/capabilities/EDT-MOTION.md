---
schema_version: 1
id: EDT-MOTION
kind: capability
title: Движение в кадре
area: AREA-EDT
parent: AREA-EDT
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

# Движение в кадре

## Кратко
**Движение в кадре**
Задаёт движение и кадрирование.
Состояние: частично; не проверено.
Ограничение: Уместность проверяется просмотром.
Далее: Согласовать движение со сценой.

## Назначение
camera_filter задаёт движение и кадрирование.

## Входы и выходы
Вход: выбранные материалы и текущие версии по ссылкам. Выход: движение в кадре в границах описанной реализации; результаты производства остаются в реестре.

## Реализация
- `app/media.py` — project-relative; разрешается адаптером context/resolve.
- `app/timeline.py` — project-relative; разрешается адаптером context/resolve.

## Доказательства
Изучены интерфейсы исходников. Исполнение функции и production-проверка этим описанием не подтверждаются.

## Связи
[[areas/AREA-EDT|К области]] · [[Studio_Map.canvas|Главная карта]] · [[START|Начало]]


## Ограничения
Художественную уместность определяет просмотр.

## Будущие улучшения
Согласовывать движение с содержанием сцены.
