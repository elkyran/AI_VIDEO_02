---
schema_version: 1
id: AUD-PROFILE
kind: capability
title: Диктор Ruslan
area: AREA-AUD
parent: AREA-AUD
implementation: implemented
verification: historical_evidence
verified_at: null
verified_revision: null
code_refs:
- app/tts.py
- app/production_profile.py
evidence_refs:
- config/production_profile.json
- reports/development/2026-10-17_A/T01_checks.json
requires: []
related: []
task_ids:
- T01
---

# Диктор Ruslan

## Кратко
**Диктор Ruslan**
Закрепляет диктора Ruslan.
Состояние: реализовано; исторические данные.
Ограничение: Fitted takes не меняют глобальный темп.
Далее: Проверять профиль перед производством.

## Назначение
Профиль закрепляет Piper ru_RU-ruslan-medium и технические предпочтения.

## Входы и выходы
Вход: выбранные материалы и текущие версии по ссылкам. Выход: диктор ruslan в границах описанной реализации; результаты производства остаются в реестре.

## Реализация
- `app/tts.py` — project-relative; разрешается адаптером context/resolve.
- `app/production_profile.py` — project-relative; разрешается адаптером context/resolve.

## Доказательства
- `config/production_profile.json`
- `reports/development/2026-10-17_A/T01_checks.json`

## Связи
[[areas/AREA-AUD|К области]] · [[Studio_Map.canvas|Главная карта]] · [[START|Начало]]


## Ограничения
Индивидуальные fitted takes не становятся глобальной настройкой темпа.

## Будущие улучшения
Сохранить Ruslan; проверять профиль перед новым производством.

Задачи: T01. Статус только в [[generated/TASKS|журнале задач]].

[[generated/code/nodes/app_tts_synthesize|Техническая реализация в Graphify]]
