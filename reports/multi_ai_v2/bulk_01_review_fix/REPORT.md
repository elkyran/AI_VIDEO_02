# BULK-01 FINAL REVIEW FIX

Дата: 2026-10-21. Repository: `elkyran/AI_VIDEO_02`.
Ветка: `feature/multi-ai-coordinator-v2`. Только два порученных исправления.

## Версии

- Ожидаемый и подтверждённый исходный HEAD: `58d27579839cd408a89e91d15de83aa4a4cce882`.
- Security HEAD: `ff18f40f1cdc20b8947c1cc96184dbe157c2fdc1`; существование проверено через fetch, без merge.
- Fix 1: `b8b082616e0d8b27e084cf41ae9ae846a2926e7c` — нормализация тестового Context.
- Fix 2: тематический коммит `fix(v2): bound derived identifiers while preserving legacy artifacts`, включающий этот отчёт. Полный SHA указывается в финальном сообщении после commit/push.
- Проверенный source manifest SHA-256: `49d2421b9d7d08a4d877584c82ba0881aed996253a8b3a0f49542597cf5c95d6`.

## Исправления

1. Mock повторяет `operations.Context`: разрешённый root и разрешённые relative paths. Сохранено полное сравнение config и cache keys, добавлена проверка количества запросов. Windows alias получен через GetShortPathNameW без фиксированного имени пользователя; тест реально выполнен, не пропущен. `app/operations.py` и narration runtime не менялись.
2. Общий `v2_ids.derived_id` сохраняет прежний join до 96 символов. Для превышения — 31 символ читаемого префикса, `_` и полный SHA-256 от JSON исходных частей с сохранением их границ. Применён к block, edit, audio, revision и offline fixture shot IDs. Canonical Identifier не изменён.
3. Полные scene/segment → block/plan/voice соответствия сохранены в существующем `scene_block_mapping`; дополнительные поля появляются только для длинных ID. Полные run/key → task соответствия уже сохраняет `context.v2_contract`. QA сопоставляет audio placements через исходные segment IDs вместо удаления строкового префикса.

## Проверки

| Проверка | Результат |
|---|---|
| Fix 1, engine bridge | 5 passed, 0 skipped |
| Адресные engine_bridge / production / visuals / bulk_boundaries / offline_qa / offline | 34 passed, 0 skipped |
| Полный `scripts/check_multi_ai_v2.py` | 455 tests: 454 passed, 1 skipped, 0 failed/errors |
| Группы v2 / contracts / queue / adapters / mai05 / panel / legacy | 85 / 58 / 50 / 16 / 107 / 12 / 127 |
| Единственный прежний skip | RealPiperTests: Requires installed local Piper |
| compileall app scripts tests; git diff --check | exit 0 |
| Export scan, рабочие файлы и Git history | passed; 1589 файлов, 1742 history blobs, findings=[] |
| Исходники неизменны во время полного прогона | true |

Новые проверки покрывают 96-символьные scene/segment/run/key, одинаковые префиксы с разными окончаниями, неоднозначные границы частей, сохранение коротких ID, restart/replay candidate/MLT/voice graph и pending/completed revision без повторных attempts/builds. Все прежние 449 тестов сохранены.

Первый запуск bridge в ограниченном sandbox дал 5 setup errors `RESULT_NOT_ACCEPTED`; повтор того же кода с разрешёнными host-процессами прошёл 5/5. Итоговый полный checker выше завершился без ошибок.

## Внешнее evidence

Новый корень: `C:/Users/diffe/.codex/visualizations/2026/10/20/01a158d2-a617-7862-8ff9-5159342bcc27/bulk_01_review_fix/`.

| Этап / manifest.json относительно корня | SHA-256 |
|---|---|
| `fix1/20261021T111915701994Z_e817f15ba5464115ba0808968b44e48c/manifest.json` | `d6e78b8dc011caacdc85f684614e0bbf55b2d4d51869e7756c6cc346e854f9e5` |
| `targeted/20261021T112158588923Z_138d6339693f407a81486023c3e46cdd/manifest.json` | `0f32b810d9c736b04828fd82b59f093ef2bfdf4087bb917bac6c4306fdc29ce6` |
| `full/20261021T112338269137Z_d6846bb25c22477c84adda74987b70da/manifest.json` | `ee19584520666bd7be54076e7d97070b7b9e91f285a5a9f9cb540eab0efd00e3` |

Полные логи сохранены вне репозитория; evidence зафиксировано на Fix 1 + рабочие изменения Fix 2, привязка к точным исходникам — source manifest выше. Это локальная проверка, повторного независимого GitHub CI ещё нет.

## Сохранность и границы

Model requests = 0; paid API requests = 0; production started = false. Только временные синтетические fixtures; существующие MLT, WAV, Ruslan, cache и production Registry не изменялись. Credentials не читались. Production-core, CI/security, production profile и main не изменялись. Четыре прежних untracked каталога review evidence сохранены и не включены в коммиты. Предыдущие коммиты/evidence сохранены. Merge PR №1, reset/clean и force-push не выполнялись.

Следующий шаг после push — независимый GitHub CI. Статус: `BULK_01_REVIEW_FIX_WAITING_FOR_GITHUB_CI`.
