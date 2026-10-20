# MULTI-AI-V2 — аудит текущего кода

Основание: новое поручение владельца от 20.10.2026. Это отдельный пакет;
исторические T00–T12 и MAI-01…12 не переименовываются и не закрываются.
Исходный git status чистый, HEAD `7a1e4058df09dbb66448a3624e0250129eaf7aa0`.

## Подтверждено кодом

| Область | Реализация и граница |
|---|---|
| Производственное состояние | app/registry.py, app/jobs.py: registry.sqlite, ревизии, requests, jobs; сохранить как есть |
| Очередь Multi-AI | app/multi_ai.py: ai_tasks/ai_attempts/ai_artifacts/ai_events/ai_profiles/ai_scenes в том же Registry; зависимости, CAS, leases, восстановление, лимит 1/profile и 1/local_heavy |
| Адаптеры | app/ai_adapters.py: FakeAdapter; app/ai_cli_runtime.py: subprocess, JSON и восстановление официальных клиентов |
| Контракты | app/editorial.py: Scene, EditPlan, Review, evidence coverage; симуляция не устанавливает художественную готовность |
| Монтаж | app/production_bridge.py: проверка и компиляция EditPlan в существующий MLT, отдельный render; production request пока descriptor |
| Основной движок | app/tts.py, timeline.py, operations.py, speech_review.py: WAV/cache, MLT, FFmpeg, QA; не переписывать |
| Аккаунты | app/ai_accounts.py, ai_codex_diagnostics.py: официальные проверки, идентичности и квоты; прежняя схема 4+4 не доказывает целевую схему 2 Gemini + Codex |
| UI | app/multi_ai_ui.py, multi_ai_preview.py: локальная панель, отдельный старый режим |
| Профиль | config/production_profile.json: Piper Ruslan; ссылки на сохранённые WAV и Timeline; не изменён |

## Подтверждено текущим чтением состояния

Production registry открыт mode=ro: 45 done, 2 blocked, 4 stale; pending/running нет.
Multi-AI live: 8 completed, 3 failed, 1 blocked; активных attempts нет; control=paused.
Это состояния реестра, а не новое подтверждение QA или приёмки видео.
Прочитаны HANDOFF, tasks.json, INTERFACES, ROADMAP и implementation_status.
Исторические чаты и credential-файлы не читались.

## Подтверждено тестом

На момент read-only аудита новые тесты ещё не запускались. Результаты текущей
ревизии записываются отдельно в evidence; прежние отчёты не заменяют новые проверки.

## Частично / только документация

Существующая очередь не содержит понятия пользовательского remote Run и полного
удалённого производственного workflow. Мост умеет MLT и descriptors операций,
но не автоматическое принятие AI-сценария в production и не финальную доставку.
Новый план не должен создавать вторую очередь или переносить активный production DB.

## Не найдено / невозможно проверить / внешняя авторизация

- .github/workflows отсутствует; self-hosted runner не подтверждён. Чтение Windows
  Service Manager из sandbox запрещено; это не доказательство отсутствия службы.
- LOCAL_RUN_ROOT, LOCAL_CACHE_ROOT, GOOGLE_DRIVE_ROOT не заданы. Drive 5 ТБ,
  свободное облачное место, синхронизация и загрузка не подтверждены.
- Cursor 3.1.15 подтверждён package.json; отдельные agent/cursor-agent не найдены
  в PATH. Его подписка/credits/BYOK и вторая Google-личность не подтверждены.
- Antigravity desktop установлен; agy CLI не найден в PATH и resources/bin.
  В resources/bin только language_server.exe и webm_encoder.exe: внутренний
  language server не используется как неофициальный API.
- Официальные документы описывают headless agy и Cursor CLI, но установленный
  маршрут, оплата, медиа и независимость второй личности требуют живых проб.
  Antigravity документирует общую локальную авторизацию разных поверхностей;
  второй изолированный профиль нельзя предполагать.
- Codex CLI найден; текущий auth/limits и маленький запрос проверяются отдельно.
- 60–90-секундный remote-пилот заблокирован отсутствием подтверждённых Story,
  Visual, runner и Drive. Симуляция не будет названа видеопилотом.

## Решение

Переиспользовать Coordinator и Registry. Добавить небольшой v2 facade: run manifest,
фиксированные роли, строгий context pack, нормализованные статусы и fail-closed
маршруты; FakeAdapter прогоняет управление зависимостями и восстановление.
Новые таблицы допустимы только для run metadata; tasks/attempts/events не дублировать.
Активные v2 данные — в явно заданном локальном LOCAL_RUN_ROOT вне синхронизации.
Старые workspace и очередь не мигрировать автоматически. Внешние блокеры отмечать
в evidence. Рабочий production остаётся доступен через прежний studio.cmd.

Официальные источники (проверены 20.10.2026):
- https://developers.openai.com/codex/noninteractive/
- https://www.antigravity.google/docs/cli/headless/
- https://www.antigravity.google/docs/install?tab=cli
- https://cursor.com/docs/cli/headless
- https://cursor.com/docs/models-and-pricing

## Дополнение после внешнего подключения

Google Drive connector появился во время выполнения. Создана папка AI_VIDEO_02,
проверочный manifest загружен и прочитан обратно в исходных байтах; совпали размер
373 и SHA-256. См. evidence/20261020T131633Z/drive_probe.json. Первоначальное
отсутствие connection выше относится к началу аудита. Unattended runner auth,
local mount и объём 5 ТБ по-прежнему не подтверждены.
