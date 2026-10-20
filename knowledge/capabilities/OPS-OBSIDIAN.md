---
schema_version: 1
id: OPS-OBSIDIAN
kind: capability
title: Навигация Obsidian
area: AREA-OPS
parent: AREA-OPS
implementation: implemented
verification: tested
verified_at: '2026-10-17T14:07:31.647228+00:00'
verified_revision: canvas-sha256:5fe0029d13b2aa965d93192fa5f4cf3df412ccbcfae5d77de5d04f1f1fb0400b
code_refs:
- app/project_memory.py
evidence_refs:
- reports/development/2026-10-17_MAP/T08_checks.json
requires: []
related:
- OPS-CODEMAP
- IDEA-OBSIDIAN-NAVIGATION
task_ids:
- T07
- T08
- T10
---

# Навигация Obsidian

## Кратко
**Навигация Obsidian**
Заметки и девять Canvas созданы. Структура и сохранность проверены. GUI not_checked: Obsidian не найден. Далее: открыть vault и проверить чтение.

## Назначение
Markdown-карточки и стандартные Canvas образуют карту студии.

## Входы и выходы
Вход: выбранные материалы и текущие версии по ссылкам. Выход: навигация obsidian в границах описанной реализации; результаты производства остаются в реестре.

## Реализация
- `app/project_memory.py` — project-relative; разрешается адаптером context/resolve.

## Доказательства
Изучены интерфейсы исходников. Исполнение функции и production-проверка этим описанием не подтверждаются.

## Связи
[[areas/AREA-OPS|К области]] · [[Studio_Map.canvas|Главная карта]] · [[START|Начало]]
- [[capabilities/OPS-CODEMAP|Карта кода Graphify]]

## Ограничения
Создание карты не доказывает качество видео; GUI проверяется отдельно.

## Будущие улучшения
Проверить карту и безопасное сохранение ручных правок.

Задачи: T07, T08, T10. Статус только в [[generated/TASKS|журнале задач]].

[[ideas/IDEA-OBSIDIAN-NAVIGATION|Предложенная идея — требует отдельного решения]]
