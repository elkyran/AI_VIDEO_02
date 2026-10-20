---
schema_version: 1
id: EDT-TITLES
kind: capability
title: Титры и субтитры
area: AREA-EDT
parent: AREA-EDT
implementation: partial
verification: not_checked
verified_at: null
verified_revision: null
code_refs:
- app/timeline.py
- scripts/transcribe_audio.py
evidence_refs: []
requires: []
related: []
task_ids: []
---

# Титры и субтитры

## Кратко
**Титры и субтитры**
Создаёт титры и субтитры.
Состояние: частично; не проверено.
Ограничение: Читаемость требует просмотра.
Далее: Проверить проблемный участок.

## Назначение
MLT и write_srt поддерживают титры и временные подписи.

## Входы и выходы
Вход: выбранные материалы и текущие версии по ссылкам. Выход: титры и субтитры в границах описанной реализации; результаты производства остаются в реестре.

## Реализация
- `app/timeline.py` — project-relative; разрешается адаптером context/resolve.
- `scripts/transcribe_audio.py` — project-relative; разрешается адаптером context/resolve.

## Доказательства
Изучены интерфейсы исходников. Исполнение функции и production-проверка этим описанием не подтверждаются.

## Связи
[[areas/AREA-EDT|К области]] · [[Studio_Map.canvas|Главная карта]] · [[START|Начало]]


## Ограничения
Читаемость и попадание слов требуют визуальной проверки.

## Будущие улучшения
Проверять короткий участок с проблемными титрами.
