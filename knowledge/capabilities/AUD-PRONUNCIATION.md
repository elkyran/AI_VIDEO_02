---
schema_version: 1
id: AUD-PRONUNCIATION
kind: capability
title: Имена и произношение
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
requires:
- AUD-PROFILE
related: []
task_ids:
- T01
graph_terms:
- synthesize
- _synthesize_piper
- check_runtime
---

# Имена и произношение

## Кратко
**Имена и произношение**
Хранит имена и произношение.
Состояние: реализовано; исторические данные.
Ограничение: Словарь не доказывает слуховое качество.
Далее: Прослушать конкретную реплику.

## Назначение
Профиль содержит словарь имён, TTS применяет настройки произношения.

## Входы и выходы
Вход: выбранные материалы и текущие версии по ссылкам. Выход: имена и произношение в границах описанной реализации; результаты производства остаются в реестре.

## Реализация
- `app/tts.py` — project-relative; разрешается адаптером context/resolve.
- `app/production_profile.py` — project-relative; разрешается адаптером context/resolve.

## Доказательства
- `config/production_profile.json`
- `reports/development/2026-10-17_A/T01_checks.json`

## Связи
[[areas/AREA-AUD|К области]] · [[Studio_Map.canvas|Главная карта]] · [[START|Начало]]
- [[capabilities/AUD-PROFILE|Диктор Ruslan]]

## Ограничения
Правильное написание не доказывает слуховое качество.

## Будущие улучшения
Проверять конкретную реплику и слушать окончания.

Задачи: T01. Статус только в [[generated/TASKS|журнале задач]].

[[generated/code/nodes/app_tts_synthesize|Техническая реализация в Graphify]]
