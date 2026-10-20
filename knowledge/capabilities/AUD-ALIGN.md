---
schema_version: 1
id: AUD-ALIGN
kind: capability
title: Соответствие речи и тайминги
area: AREA-AUD
parent: AREA-AUD
implementation: partial
verification: not_checked
verified_at: null
verified_revision: null
code_refs:
- scripts/transcribe_audio.py
- app/speech_review.py
evidence_refs:
- development/INTERFACES.md
requires: []
related:
- AUD-WAV
task_ids:
- T04
---

# Соответствие речи и тайминги

## Кратко
**Соответствие речи и тайминги**
Связывает слова и тайминги.
Состояние: частично; не проверено.
Ограничение: ASR ошибается; T04 pending.
Далее: Проверить спорные слова.

## Назначение
align_script_words связывает слова с временными метками и субтитрами.

## Входы и выходы
Вход: выбранные материалы и текущие версии по ссылкам. Выход: соответствие речи и тайминги в границах описанной реализации; результаты производства остаются в реестре.

## Реализация
- `scripts/transcribe_audio.py` — project-relative; разрешается адаптером context/resolve.
- `app/speech_review.py` — project-relative; разрешается адаптером context/resolve.

## Доказательства
- `development/INTERFACES.md`

## Связи
[[areas/AREA-AUD|К области]] · [[Studio_Map.canvas|Главная карта]] · [[START|Начало]]
- [[capabilities/AUD-WAV|Отдельные реплики WAV]]

## Ограничения
ASR может пропускать отрицания и окончания; T04 остаётся pending.

## Будущие улучшения
Проверять спорные слова и тайминги вручную.

Задачи: T04. Статус только в [[generated/TASKS|журнале задач]].
