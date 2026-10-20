---
schema_version: 1
id: AUD-WAV
kind: capability
title: Отдельные реплики WAV
area: AREA-AUD
parent: AREA-AUD
implementation: partial
verification: not_checked
verified_at: null
verified_revision: null
code_refs:
- app/tts.py
- app/operations.py
evidence_refs: []
requires: []
related: []
task_ids: []
---

# Отдельные реплики WAV

## Кратко
**Отдельные реплики WAV**
Создаёт отдельные WAV и манифест.
Состояние: частично; не проверено.
Ограничение: Кеш и оригиналы сохраняются.
Далее: Переиспользовать совпавший блок.

## Назначение
synthesize создаёт блоки WAV и манифест; кеш хранит готовые реплики.

## Входы и выходы
Вход: выбранные материалы и текущие версии по ссылкам. Выход: отдельные реплики wav в границах описанной реализации; результаты производства остаются в реестре.

## Реализация
- `app/tts.py` — project-relative; разрешается адаптером context/resolve.
- `app/operations.py` — project-relative; разрешается адаптером context/resolve.

## Доказательства
Изучены интерфейсы исходников. Исполнение функции и production-проверка этим описанием не подтверждаются.

## Связи
[[areas/AREA-AUD|К области]] · [[Studio_Map.canvas|Главная карта]] · [[START|Начало]]


## Ограничения
Кеш и исходные WAV сохраняются; новый синтез здесь не выполнялся.

## Будущие улучшения
Переиспользовать проверенный блок при совпадении входов.
