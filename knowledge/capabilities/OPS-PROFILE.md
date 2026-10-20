---
schema_version: 1
id: OPS-PROFILE
kind: capability
title: Профиль и режимы
area: AREA-OPS
parent: AREA-OPS
implementation: implemented
verification: historical_evidence
verified_at: null
verified_revision: null
code_refs:
- app/production_profile.py
evidence_refs:
- config/production_profile.json
- reports/development/2026-10-17_A/T01_checks.json
requires: []
related: []
task_ids:
- T01
---

# Профиль и режимы

## Кратко
**Профиль и режимы**
Разделяет предпочтения и runtime.
Состояние: реализовано; исторические данные.
Ограничение: Профиль требует отдельной задачи.
Далее: Выполнить --check-runtime.

## Назначение
Проверка профиля и runtime отделяет предпочтения от локальных путей.

## Входы и выходы
Вход: выбранные материалы и текущие версии по ссылкам. Выход: профиль и режимы в границах описанной реализации; результаты производства остаются в реестре.

## Реализация
- `app/production_profile.py` — project-relative; разрешается адаптером context/resolve.

## Доказательства
- `config/production_profile.json`
- `reports/development/2026-10-17_A/T01_checks.json`

## Связи
[[areas/AREA-OPS|К области]] · [[Studio_Map.canvas|Главная карта]] · [[START|Начало]]


## Ограничения
Профиль не менять без отдельной development-задачи.

## Будущие улучшения
Использовать --check-runtime перед производством.

Задачи: T01. Статус только в [[generated/TASKS|журнале задач]].
