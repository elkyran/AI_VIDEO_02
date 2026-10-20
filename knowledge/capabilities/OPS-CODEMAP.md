---
schema_version: 1
id: OPS-CODEMAP
kind: capability
title: Карта кода Graphify
area: AREA-OPS
parent: AREA-OPS
implementation: implemented
verification: tested
verified_at: '2026-10-17T14:07:31.646228+00:00'
verified_revision: corpus-sha256:8797ecc536cd33c58959ae502770252995bafacb0f6dcefbad4e83da22fcb82b
code_refs:
- app/code_map.py
evidence_refs:
- reports/development/2026-10-17_MAP/T09_checks.json
requires: []
related:
- OPS-OBSIDIAN
- IDEA-GRAPHIFY-QUESTIONS
task_ids:
- T09
---

# Карта кода Graphify

## Кратко
**Карта кода Graphify**
Структурный Graphify 0.9.92: граф, HTML и заметки. Локально проверено. Ограничение: HTML использует CDN. Далее: обновлять после правки кода.

## Назначение
Создан локальный структурный индекс исходников и тестов.

## Входы и выходы
Вход: выбранные материалы и текущие версии по ссылкам. Выход: карта кода graphify в границах описанной реализации; результаты производства остаются в реестре.

## Реализация
Будущая возможность; код не указан.

## Доказательства
Изучены интерфейсы исходников. Исполнение функции и production-проверка этим описанием не подтверждаются.

## Связи
[[areas/AREA-OPS|К области]] · [[Studio_Map.canvas|Главная карта]] · [[START|Начало]]
- [[capabilities/OPS-OBSIDIAN|Навигация Obsidian]]

## Ограничения
Backend none; HTML использует CDN, офлайн-просмотр не подтверждён.

## Будущие улучшения
Обновлять граф после изменения кода через dry-run/apply.

Задачи: T09. Статус только в [[generated/TASKS|журнале задач]].

[[ideas/IDEA-GRAPHIFY-QUESTIONS|Предложенная идея — требует отдельного решения]]
