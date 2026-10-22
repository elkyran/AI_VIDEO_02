# Возможности исполнителей MULTI-AI-V2

RELEASE-01: host-owned worker routes и независимые account reservations теперь
реализованы в существующей очереди (cloud общий лимит 3, local-heavy 1), с
generation binding на всех путях acceptance/recovery. SYSTEM simulation может
явно подключить два synthetic slot через config, без fallback. Это software
wiring, не реальные Google-аккаунты: live Story/Visual false, AGE-GATED /
BLOCKED_ELIGIBILITY. Canon/outline/visual selection и фактическая доставка
image/frame/full-video текущим transport не подтверждены. Drive host resolver и
приватный session store реализованы, cloud proof остаётся UNVERIFIED.

SYSTEM-01: [актуальная readiness matrix](SYSTEM_READINESS.md) отделяет реализацию,
offline transport tests, live verification и owner approval. `ProviderBridge`
использует существующий adapter protocol и не устанавливает launcher/account
manager. Story/Visual по-прежнему AGE-GATED / DISABLED, без замены через Codex/API.
Drive v3 resumable transport реализован и протестирован с injected transport;
host credential_ref resolver и cloud proof не проверены. Старые свидетельства
ниже не переносятся автоматически на новый общий pipeline.

Целевая схема: [архитектура владельца](MULTI_AI_V2_ARCHITECTURE.md).
Наличие приложения, тарифа или бинарника не подтверждает live integration.
Python Coordinator управляет задачами; официальные клиенты исполняют роли.

| Исполнитель / роль | Маршрут | Подтверждённая граница | Текущее решение |
|---|---|---|---|
| Codex / director | Официальный Codex, подписка ChatGPT | Исторический один JSON smoke; не сквозная v2 production цепочка | Live production wiring NOT_IMPLEMENTED; новый smoke в Phase A не запускался |
| antigravity_story / writer | Целевой Antigravity с отдельным account binding A | Fixed offline slot, synthetic identity/auth/session; actual identity/auth/quota UNVERIFIED | OFFLINE_TESTED; live BLOCKED_ELIGIBILITY / AGE-GATED / DISABLED |
| antigravity_visual / critic | Целевой Antigravity с отдельным account binding B | Отдельный offline slot и canonical Review; actual identity/auth/quota UNVERIFIED | OFFLINE_TESTED; live BLOCKED_ELIGIBILITY / AGE-GATED / DISABLED |
| Fake Story/Visual/Codex | FakeAdapter | Локальные JSON fixtures, validators и queue/recovery | IMPLEMENTED только для симуляции; не просмотр MP4 и не реальное производство |
| Existing AI_VIDEO_02 | Piper, WAV/cache, Timeline/MLT, FFmpeg, technical QA | Существующий производственный движок | Связь v2 с production jobs NOT_IMPLEMENTED; движок в этом пакете не запускался |
| Windows runner / RTX 4050 | Локальное исполнение тяжёлых операций | Целевая площадка; готовность GPU/runner не подтверждается документацией | Регистрация runner запрещена до отдельного решения |
| Google Drive | Хранилище accepted artifacts, sources/scripts/WAV/previews/MLT/MP4/backups | Исторический connector upload/readback малого manifest; локальная sync-копия не равна cloud proof | Unattended runner auth/root и сквозная загрузка NOT_IMPLEMENTED |

Antigravity остаётся отключённым до подтверждения соответствия официальным
возрастным и другим требованиям. Не выполнять login, model smoke, auth migration,
не копировать credentials и не искать обходные маршруты. Два разных account
bindings — обязательная цель, не разрешение подключить второй аккаунт или
устранить возрастное ограничение. Cursor/Codex/API не заменяют эти роли.
Исторические эксперименты остаются evidence, не действующим планом подключения.
Автоматическая ротация аккаунтов запрещена; неизвестные квоты — null.
Никаких платных API без отдельного решения владельца.

PROVIDERS-01: [протокол, квоты и ограничения](DUAL_PROVIDER_INTEGRATION.md).
Text, image/frame, frame sequence, full video — отдельные capabilities, все
actual значения UNVERIFIED. Документированный текстовый input и путь MP4 не
подтверждают media delivery. Requested model не становится returned model;
последний null без host evidence. Accounts connected=0, live requests=NOT_RUN.
Разные identity hashes не доказывают независимые квоты. Fake19 остаётся старым
симулятором, не выполнением требования о двух реальных работниках.

Историческое свидетельство Codex: reports/multi_ai_v2/evidence/20261020T131633Z/
codex_probe.json, CLI 0.163.0-alpha.10.2, exit=0, MULTI-AI-V2-OK, один запрос,
input_tokens=11408, output_tokens=38; requested/returned model=null.
Это исторические метрики, не расходы текущего Phase A: model_requests=0,
paid_api_requests=0, production_started=false. Текущий вход/тариф/квоты заново
не проверялись; историческое evidence не повышает readiness.
