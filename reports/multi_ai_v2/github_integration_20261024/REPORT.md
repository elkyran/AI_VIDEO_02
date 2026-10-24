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

PR6 также успешно проверен и объединён в main:
d5481c618740eb85866ebbd67d159adb6a131855.

- [Hosted Windows run 40078748459](https://github.com/elkyran/AI_VIDEO_02/actions/runs/40078748459): success.
- Проверенный временный main+feature merge: afce7dfc5b3649d70d5bbc47c8f966fee6b1ec00.
- Head feature на приёмке: 13c03b2200cbdbd43b112a5401dec49b73eb6e74.
- 642 tests = 640 passed + 2 existing environment skips; failures/errors=0.
- Все 17 checker groups, evidence envelope, оба 19-stage replay smoke
  и clean checkout passed.
- Manifest SHA-256: b7fcaad7198c6e165736dcf50015f71017b1f40262138decd56c8bab0f4d2c05.
- GitHub artifact 12554791132, ZIP digest:
  ba3c65a7988622a7604d651751c4120574077c672f3b4953b7443cf85f60af77.

GitHub commit SHA отдельно от tree: фактические checkout SHA подтверждены в job logs;
их manifest/файлы проверены hosted integrity step. Дополнительный локальный аудит
скачанного ZIP не выполнялся по ограничению владельца.

Проверенный набор unittest groups:

| Группа | Тесты | Пропуски | Результат |
|---|---:|---:|---|
| v2 | 111 | 0 | passed |
| contracts | 174 | 0 | passed |
| queue | 50 | 0 | passed |
| adapters | 16 | 0 | passed |
| mai05 | 108 | 1 | passed |
| panel | 12 | 0 | passed |
| legacy | 127 | 1 | passed |
| acquisition | 13 | 0 | passed |
| closure | 12 | 0 | passed |
| review_recovery | 11 | 0 | passed |
| review_story | 1 | 0 | passed |
| review_visual | 1 | 0 | passed |
| review_source_revision | 1 | 0 | passed |
| review_crash | 5 | 0 | passed |

Компиляция, проверка Git export и whitespace также passed.
Конфигурация production_profile.json совпадает с исходным main:
Git blob 6a8bcc43a554b2e6827b9886d261ce2bce3625d6.
Финальное обновление этого отчёта — только документация, без изменения
проверенного Python/workflow/schema/profile кода.

Прежний run 39857157487 остаётся историческим failed: 642 tests,
639 passed, 2 environment skips, 1 error. Его результаты не переписаны.

Проверки выполняются на временном GitHub-hosted Windows runner, а не на
пользовательском компьютере. Сохранённые evidence — GitHub Actions artifacts
с ограниченным retention; ссылки на runs остаются в этом отчёте.

## Состояние шести направлений

| Направление | Состояние |
|---|---|
| Windows-пути | Исправлено; closure 12/12 passed в обеих независимых проверках |
| GitHub-интеграция | PR9 и PR6 объединены после успешных отдельных CI; main d5481c6 |
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
