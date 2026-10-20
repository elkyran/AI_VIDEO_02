---
schema_version: 1
id: DEC-MAP-ORDER
kind: decision
title: Разрешён порядок T07 → T08 → T09 → T10
area: null
parent: null
implementation: implemented
verification: not_checked
verified_at: null
verified_revision: null
code_refs:
- app/development.py
evidence_refs:
- development/INTERFACES.md
requires: []
related: []
task_ids:
- T07
- T08
- T09
- T10
status: accepted
---

# Порядок работ по поручению владельца

## Кратко
Принято 2026-10-17: T07 → T08 → T09 → T10.
T00–T03 не повторять; T04–T06 оставить pending; T11–T12 не разрешены.

## Основание
Владелец явно изменил порядок в текущем задании. Проверка исходного плана: T07 технически требует T02; T06 представляла условие «после B». Карта может честно показывать незавершённую B.

## Последствия
Journal.authorize_map_batch сохраняет прежнюю зависимость в amendment и ограничивает разрешение четырьмя ID. Остальные зависимости и проверки done сохраняются. Один агент; без платных API, синтеза, рендера и изменения TTS.

[[generated/TASKS|Задачи]] · [[START|Начало]]
