# Аккаунты и официальный вход

У каждой роли постоянный аккаунт. Конец квоты ставит задачу в WAITING_QUOTA;
автоматической смены аккаунта, модели или платного маршрута нет.

Codex: войти официальной командой `codex login`, проверить `codex login status`.
Вход и токены обслуживает клиент. Диагностика v2 использует официальный app-server
account/read и account/rateLimits/read, сохраняет только fingerprint и безопасные
поля. Auth-файлы не читаются и не копируются. `--live-codex` разрешает ровно один
безопасный schema smoke только при подтверждённой обычной квоте. Пример:

```powershell
.venv-core/Scripts/python.exe -X utf8 scripts/probe_multi_ai_v2.py --output reports/multi_ai_v2/evidence/NEW_TIMESTAMP --codex-home C:/Users/diffe/.codex --live-codex
```

Antigravity: официальный CLI agy отсутствует в проверенном PATH и каталоге
установки приложения. После установки официального CLI владелец входит в
интерактивном agy; затем нужны локальный --help, версия, поддерживаемая модель,
изолированный JSON smoke. Не применять к agy флаги Gemini CLI по аналогии.
Установка/вход не выполнялись, внутренний language_server.exe не используется.

Gemini Visual выключен. Cursor editor 3.1.15 не равен Cursor Agent CLI.
Официальный headless у Cursor существует, но local CLI, billing, Google identity,
независимая квота и MP4 transport здесь не подтверждены. Google AI Pro не
подтверждает оплату Cursor. Второй Antigravity тоже не подтверждён: документация
описывает общую локальную авторизацию. Не копировать credential-файлы, не
изобретать OAuth и не автоматизировать GUI.

Источники: [Codex](https://developers.openai.com/codex/noninteractive/),
[Antigravity auth](https://www.antigravity.google/docs/install?tab=cli),
[Antigravity headless](https://www.antigravity.google/docs/cli/headless/),
[Cursor headless](https://cursor.com/docs/cli/headless),
[Cursor pricing](https://cursor.com/docs/models-and-pricing).
Факт capability хранится в timestamp probe, неизвестные model/quota — null.
