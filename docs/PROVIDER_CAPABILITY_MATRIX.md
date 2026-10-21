# Возможности исполнителей MULTI-AI-V2

Целевая схема: [архитектура владельца](MULTI_AI_V2_ARCHITECTURE.md).
Наличие приложения, тарифа или бинарника не подтверждает live integration.
Python Coordinator управляет задачами; официальные клиенты исполняют роли.

| Исполнитель / роль | Маршрут | Подтверждённая граница | Текущее решение |
|---|---|---|---|
| Codex / director | Официальный Codex, подписка ChatGPT | Исторический один JSON smoke; не сквозная v2 production цепочка | Live production wiring NOT_IMPLEMENTED; новый smoke в Phase A не запускался |
| Gemini Story / writer | Будущий официальный Antigravity integration | Логическая роль; отдельный аккаунт/квота не предполагаются | AGE-GATED / DISABLED / BLOCKED |
| Gemini Visual / critic | Тот же будущий официальный Antigravity integration | Логическая роль; независимый второй аккаунт не объявлен | AGE-GATED / DISABLED / BLOCKED |
| Fake Story/Visual/Codex | FakeAdapter | Локальные JSON fixtures, validators и queue/recovery | IMPLEMENTED только для симуляции; не просмотр MP4 и не реальное производство |
| Existing AI_VIDEO_02 | Piper, WAV/cache, Timeline/MLT, FFmpeg, technical QA | Существующий производственный движок | Связь v2 с production jobs NOT_IMPLEMENTED; движок в этом пакете не запускался |
| Windows runner / RTX 4050 | Локальное исполнение тяжёлых операций | Целевая площадка; готовность GPU/runner не подтверждается документацией | Регистрация runner запрещена до отдельного решения |
| Google Drive | Хранилище accepted artifacts, sources/scripts/WAV/previews/MLT/MP4/backups | Исторический connector upload/readback малого manifest; локальная sync-копия не равна cloud proof | Unattended runner auth/root и сквозная загрузка NOT_IMPLEMENTED |

Antigravity остаётся отключённым до подтверждения соответствия официальным
возрастным и другим требованиям. Не выполнять login, model smoke, auth migration,
не копировать credentials и не искать обходные маршруты. Cursor и второй
Antigravity account не являются альтернативными ветвями текущей целевой схемы.
Исторические эксперименты остаются evidence, не действующим планом подключения.
Автоматическая ротация аккаунтов запрещена; неизвестные квоты — null.
Никаких платных API без отдельного решения владельца.

Историческое свидетельство Codex: reports/multi_ai_v2/evidence/20261020T131633Z/
codex_probe.json, CLI 0.163.0-alpha.10.2, exit=0, MULTI-AI-V2-OK, один запрос,
input_tokens=11408, output_tokens=38; requested/returned model=null.
Это исторические метрики, не расходы текущего Phase A: model_requests=0,
paid_api_requests=0, production_started=false. Текущий вход/тариф/квоты заново
не проверялись; историческое evidence не повышает readiness.
