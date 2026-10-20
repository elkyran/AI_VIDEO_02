# Карта кода

## Кратко
Локальный структурный индекс Graphify. Состояние и команды обновления — в [[HOW_TO_USE|инструкции]].
Исходники не копируются в vault.

Технические заметки находятся в `generated/code/`; граф и HTML — в `graphify-out/` проекта. Пути вне vault разрешает адаптер `resolve`. Версия graphifyy 0.9.92, backend none, структурный code-only индекс. Актуальность: `python -m app.code_map status`.

- [[generated/code/graph.canvas|Открыть технический Canvas Graphify]].
- [[generated/code/nodes/app_tts_synthesize|Озвучка: synthesize]].
- [[generated/code/nodes/app_speech_review_review_build_speech|ASR: review_build_speech]].
- [[generated/code/nodes/app_timeline_build_timeline|Монтаж: build_timeline]].

HTML загружает vis-network с unpkg.com; полностью автономный просмотр не подтверждён. Markdown и Canvas остаются локальными. Исходники, медиа и БД в vault не копируются.

Для обзорного Graph view задайте фильтр `-path:generated/code`.
[[capabilities/OPS-CODEMAP|Graphify]] · [[START|Начало]]
