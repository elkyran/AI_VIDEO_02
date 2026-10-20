---
schema_version: 1
id: AUD-REPLACE
kind: capability
title: Замена звука
area: AREA-AUD
parent: AREA-AUD
implementation: partial
verification: not_checked
verified_at: null
verified_revision: null
code_refs:
- scripts/fit_narration.py
- scripts/replace_narration.py
evidence_refs:
- development/INTERFACES.md
- reports/development/2026-10-17_A/production_baseline.json
requires: []
related: []
task_ids:
- T05
- T06
---

# Замена звука

## Кратко
**Замена звука**
Заменяет звук отдельными скриптами. Частично готово. T05–T06 pending: нет штатной операции и актуальной финальной QA/package. Далее: отдельное поручение.

## Назначение
Отдельные fit_narration и replace_narration сохраняют путь замены звука.

## Входы и выходы
Вход: выбранные материалы и текущие версии по ссылкам. Выход: замена звука в границах описанной реализации; результаты производства остаются в реестре.

## Реализация
- `scripts/fit_narration.py` — project-relative; разрешается адаптером context/resolve.
- `scripts/replace_narration.py` — project-relative; разрешается адаптером context/resolve.

## Доказательства
- `development/INTERFACES.md`
- `reports/development/2026-10-17_A/production_baseline.json`

## Связи
[[areas/AREA-AUD|К области]] · [[Studio_Map.canvas|Главная карта]] · [[START|Начало]]


## Ограничения
T05 pending: штатной операции очереди нет. T06 pending: QA/package второго выпуска предшествуют финальной замене Ruslan.

## Будущие улучшения
Отдельное поручение T05–T06; текущие файлы и ручной MLT сохранить.

Задачи: T05, T06. Статус только в [[generated/TASKS|журнале задач]].
