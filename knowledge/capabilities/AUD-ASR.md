---
schema_version: 1
id: AUD-ASR
kind: capability
title: Распознавание речи
area: AREA-AUD
parent: AREA-AUD
implementation: partial
verification: not_checked
verified_at: null
verified_revision: null
code_refs:
- app/speech_review.py
- scripts/transcribe_audio.py
evidence_refs:
- development/INTERFACES.md
requires: []
related:
- AUD-WAV
task_ids:
- T04
graph_terms:
- review_build_speech
- _cached_result
- _transcribe_manifest
- _model_identity
---

# Распознавание речи

## Кратко
**Распознавание речи**
Распознаёт речь и проверяет текст. Частично готово. T04 pending: кеш ASR и сверка ещё не разделены. Далее: отдельное поручение T04.

## Назначение
review_build_speech и transcribe_manifest выполняют распознавание и проверку.

## Входы и выходы
Вход: выбранные материалы и текущие версии по ссылкам. Выход: распознавание речи в границах описанной реализации; результаты производства остаются в реестре.

## Реализация
- `app/speech_review.py` — project-relative; разрешается адаптером context/resolve.
- `scripts/transcribe_audio.py` — project-relative; разрешается адаптером context/resolve.

## Доказательства
- `development/INTERFACES.md`

## Связи
[[areas/AREA-AUD|К области]] · [[Studio_Map.canvas|Главная карта]] · [[START|Начало]]
- [[capabilities/AUD-WAV|Отдельные реплики WAV]]

## Ограничения
T04 pending: ключ ASR и сверка ожидаемого текста ещё не разделены; не обещать повторное использование после визуальных правок.

## Будущие улучшения
Отдельное поручение T04: разделить кеш распознавания и соответствие, с подменённым распознавателем.

Задачи: T04. Статус только в [[generated/TASKS|журнале задач]].

[[generated/code/nodes/app_speech_review_review_build_speech|Техническая реализация в Graphify]]
