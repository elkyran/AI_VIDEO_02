---
schema_version: 1
id: SCR-REVIEW
kind: capability
title: Редактура и согласование
area: AREA-SCR
parent: AREA-SCR
implementation: partial
verification: not_checked
verified_at: null
verified_revision: null
code_refs:
- app/operations.py
- app/review.py
evidence_refs: []
requires: []
related: []
task_ids: []
---

# Редактура и согласование

## Кратко
**Редактура и согласование**
Поддерживает адресную редактуру.
Состояние: частично; не проверено.
Ограничение: Тест не заменяет принятие владельцем.
Далее: Зафиксировать решение человека.

## Назначение
patch_script и read_review поддерживают адресную правку и просмотр результата.

## Входы и выходы
Вход: выбранные материалы и текущие версии по ссылкам. Выход: редактура и согласование в границах описанной реализации; результаты производства остаются в реестре.

## Реализация
- `app/operations.py` — project-relative; разрешается адаптером context/resolve.
- `app/review.py` — project-relative; разрешается адаптером context/resolve.

## Доказательства
Изучены интерфейсы исходников. Исполнение функции и production-проверка этим описанием не подтверждаются.

## Связи
[[areas/AREA-SCR|К области]] · [[Studio_Map.canvas|Главная карта]] · [[START|Начало]]


## Ограничения
Программная проверка не равна редакционному принятию.

## Будущие улучшения
Зафиксировать решение владельца отдельно.
