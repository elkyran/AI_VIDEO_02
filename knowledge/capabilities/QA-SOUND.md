---
schema_version: 1
id: QA-SOUND
kind: capability
title: Проверка звука
area: AREA-QA
parent: AREA-QA
implementation: partial
verification: not_checked
verified_at: null
verified_revision: null
code_refs:
- app/speech_review.py
- app/operations.py
evidence_refs: []
requires: []
related: []
task_ids: []
---

# Проверка звука

## Кратко
**Проверка звука**
Проверяет звук и речь.
Состояние: частично; не проверено.
Ограничение: Неизвестное прослушивание не passed.
Далее: Слушать имена и окончания.

## Назначение
Речевая сверка и qa проверяют аудио и метрики.

## Входы и выходы
Вход: выбранные материалы и текущие версии по ссылкам. Выход: проверка звука в границах описанной реализации; результаты производства остаются в реестре.

## Реализация
- `app/speech_review.py` — project-relative; разрешается адаптером context/resolve.
- `app/operations.py` — project-relative; разрешается адаптером context/resolve.

## Доказательства
Изучены интерфейсы исходников. Исполнение функции и production-проверка этим описанием не подтверждаются.

## Связи
[[areas/AREA-QA|К области]] · [[Studio_Map.canvas|Главная карта]] · [[START|Начало]]


## Ограничения
Неизвестное прослушивание не считается пройденным.

## Будущие улучшения
Слушать отрицания, имена, окончания и баланс.
