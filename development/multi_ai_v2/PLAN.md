# MULTI-AI-V2 — план по целевой схеме владельца

PHASE A FINAL CI GATE уточняет решение владельца: один video brief → приватный
GitHub → self-hosted Windows runner / RTX 4050 → единственный Python Coordinator
и существующий AI_VIDEO_02 → официальные Codex / Antigravity → accepted artifacts
→ Google Drive. Полная схема и фактические границы:
[docs/MULTI_AI_V2_ARCHITECTURE.md](../../docs/MULTI_AI_V2_ARCHITECTURE.md).

Gemini Story и Gemini Visual — логические роли одного будущего Antigravity
integration, а не требование двух автоматически независимых аккаунтов.
Antigravity AGE-GATED / DISABLED до подтверждения официальных возрастных и других
требований: login, smoke, auth migration и обходные маршруты запрещены.
Автоматической ротации нет; платные API требуют отдельного решения владельца.

T00–T12 и MAI-01…12 не меняются. STATUS.json сохраняет существующие состояния
разработки; документация не повышает readiness production. Исторические схемы и
свидетельства не удаляются, но не задают вторую целевую архитектуру.

| Phase | Реализовано / состояние | Оставшаяся граница |
|---|---|---|
| 0 | Адресные evidence существующего кода и клиентов | Не повторять полный исторический аудит |
| 1 | Baseline/feature branch/локальные SQLite backups | Не изменять production БД и originals |
| 2 | Contracts/Run metadata/Registry | Approval/cache state machine PARTIAL; второй очереди нет |
| 3 | Fake workflow, 19 этапов, queue/recovery/accepted artifacts | Fake не равен TTS/MP4/upload |
| 4 | Официальный Codex: исторический JSON smoke | Сквозной live production wiring NOT_IMPLEMENTED |
| 5–6 | Story/Visual — логические роли Antigravity | BLOCKED / AGE-GATED / DISABLED; не устанавливать/авторизовывать/тестировать модель |
| 7 | Existing ProductionBridge compile/descriptor | Scene import и запуск existing jobs из v2 NOT_IMPLEMENTED |
| 8 | Existing technical validators и trusted acceptance | Реальный technical/creative QA pilot BLOCKED |
| 9 | Offline GitHub CI и отдельный security lane workflow | Регистрация runner запрещена; unattended Drive auth/root/upload NOT_IMPLEMENTED |
| 10 | Live pilot | BLOCKED; отдельное поручение после подтверждения соединений |

Текущий Phase A: bounded checker timeout v2=420 секунд, остальные группы=180;
исходные 407 тестов сохраняются, добавлены быстрые mock-based timeout/evidence
проверки. Настоящий timeout даёт exit 124 и failed status. Полный checker
использует новый внешний evidence directory, commit/tree/source binding и хеши.
Независимый GitHub job имеет предел 30 минут; нормальный прогон проверяется по
elapsed evidence, а не считается гарантированным из суммы per-group лимитов.

Следующее действие после push: остановиться для независимого GitHub review/CI.
Не merge PR #1/main; не регистрировать runner; не запускать production или модели.
Решение о реализации недостающих соединений — отдельная задача владельца.
