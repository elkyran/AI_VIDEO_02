# Продвижение проекта — 24 октября 2026

## Разрешённая область

Владелец поручил продолжить исправление Windows-путей, GitHub CI и интеграцию.
Затем подтвердил: работать только в GitHub, ничего локально не сохранять и не запускать;
производство отложить, доступ двух аккаунтов Story/Visual пока не подтверждён.

В этом пакете нет локального checkout, установок, production, TTS, рендера, model/API
запросов, авторизации аккаунтов, регистрации runner или cloud upload.

## Исправление

В tests/test_v2_production.py корень общей ProductionCandidateTests заготовки
нормализован через Path(self.temp.name).resolve() до создания очереди и engine.
Его наследует GenericMeasuredEditTests.

Причина: workspace_path разрешал RUNNER~1 в runneradmin, но база для relative_to
оставалась коротким именем. На последнем прежнем CI это давало единственную
ошибку test_actual_measured_edit_preserves_text_order_silence_and_rejects_speech_tail.

Изменена одна строка. Production, профиль Ruslan, проверки путей, assertions,
список тестов и deadlines не ослаблены.

Коммиты:
- feature: 428dfe3bf2f19274ddd86b4138a22210f16f20bf;
- та же правка в проверочной ветке: db166afaa391e19019bc0133774c8ec747caaa60.

## Проверки и интеграция

PR9 успешно проверен и объединён в feature: merge commit
38c5529657c83c37220ce5241eee2e87be5fb5f5.

- [Hosted Windows run 40077604785](https://github.com/elkyran/AI_VIDEO_02/actions/runs/40077604785): success.
- Проверенный временный merge: 32c2e33259fe9fd768fd09a147814d378e465480.
- 642 tests = 640 passed + 2 existing environment skips; failures/errors=0.
- Все 14 unittest groups и compileall/export/diff passed.
- Immutable evidence envelope, оба hosted replay smoke и clean checkout passed.
- Manifest SHA-256: e0578d2d7ac887d5cae6411d2401b4c3fe596ce3224ef216cf080b3868576f0c.
- GitHub artifact 12554251305, ZIP digest:
  9dcd2e34c1c31549186046aa9aee1b429f3f590232684eb4afbb43302effdb80.
- Проверка hashes/size/commit/tree выполнена самим hosted workflow.
  Архив на пользовательский компьютер не скачивался.

Этот отчёт подготовлен после PR9, до финального решения по PR6. Для PR6 нужна
отдельная свежая проверка итогового main+feature merge. Текущее решение, ссылка на
финальный CI и SHA main фиксируются в [PR6](https://github.com/elkyran/AI_VIDEO_02/pull/6);
до его зелёного gate интеграция в main не считается завершённой.

Прежний run 39857157487 остаётся историческим failed: 642 tests,
639 passed, 2 environment skips, 1 error. Его результаты не переписаны.

Проверки выполняются на временном GitHub-hosted Windows runner, а не на
пользовательском компьютере. Сохранённые evidence — GitHub Actions artifacts
с ограниченным retention; ссылки на runs остаются в этом отчёте.

## Состояние шести направлений

| Направление | Состояние |
|---|---|
| Windows-пути | Однострочная нормализация общей fixture; результат CI указан выше |
| GitHub-интеграция | PR9 -> feature; PR6 -> main; результаты и SHA указаны выше |
| Реальные Story/Visual | Доступ не подтверждён; live workers остаются выключенными |
| Windows self-hosted runner | Регистрация и запуск отложены владельцем |
| Полный новый выпуск | Отложен; новый MP4 не создан |
| Качество голоса/визуала/музыки | В этом пакете не менялось; оценивать после реального выпуска |

Существующий PILOT-01 остаётся историческим доказательством только своего
технического scope. Прохождение offline tests не доказывает live двухаккаунтную
интеграцию, художественную готовность, cloud delivery или автономность.

## Следующие действия

1. Для будущих code changes использовать текущий main и PR с независимым CI.
2. Согласовать официально доступных Story/Visual исполнителей и реальную
   поддержку нужных медиа; сохранить фиксированные роли и host-owned bindings.
3. Выбрать отдельный исполняющий Windows-host. GitHub хранит код и запускает CI;
   текущий workflow new-video.yml остаётся private/main-only/manual/fake-only.
4. После отдельного решения владельца проверить runner и runtime без
   изменения прежней рабочей студии.
5. Только затем выполнить один содержательный выпуск, адресную правку сцены,
   replay и переносимость. Просмотр/прослушивание владельцем остаются приёмкой.
6. Drive и качество улучшать после функционального выпуска.

Исторические development/HANDOFF.md и tasks.json не переписаны вручную:
они относятся к журналу T00–T12. Этот отчёт фиксирует отдельный текущий пакет.

## Источники

- [PR9](https://github.com/elkyran/AI_VIDEO_02/pull/9)
- [PR6](https://github.com/elkyran/AI_VIDEO_02/pull/6)
- [SYSTEM_RUNBOOK](../../../docs/SYSTEM_RUNBOOK.md)
- [SYSTEM_READINESS](../../../docs/SYSTEM_READINESS.md)
- [PILOT-01](../pilot_01/REPORT.md)
