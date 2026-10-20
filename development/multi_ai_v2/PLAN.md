# MULTI-AI-V2 — замена целевой схемы Multi-AI

Поручение владельца от 20.10.2026 задаёт Gemini Story + независимый Gemini Visual
+ Codex Production + Python Coordinator. Прежняя схема 4 GPT + 4 Gemini остаётся
историческим доступным режимом; её документы и свидетельства не удаляются.
T00–T12 и MAI-01…12 не меняются. Статус нового пакета хранится отдельно в STATUS.json;
это статус разработки, не новая производственная очередь.

| Phase | Действие | Проверка первого среза |
|---|---|---|
| 0 | Адресный аудит кода/состояния/клиентов | reports/multi_ai_v2/AUDIT.md; read-only базы |
| 1 | Baseline, branch, backup | baseline-before-multi-ai-v2; feature/multi-ai-coordinator-v2; ZIP и SQLite backup API локально |
| 2 | Contracts/Run metadata | app/v2_contracts.py; схемы; ai_v2_runs рядом с existing ai_tasks; нормализация состояния, approval/cache пока partial |
| 3 | Fake workflow | app/multi_ai_v2.py; 19 этапов, существующие validators/queue; restart, no duplicates, quota tests |
| 4 | Codex | scripts/probe_multi_ai_v2.py: официальный account diagnostic + один real schema smoke; live production wiring не завершён |
| 5 | Story | BLOCKED: установленный agy CLI, официальный вход, model/JSON smoke; Gemini CLI Free API не подставлять |
| 6 | Visual | BLOCKED: Cursor Agent CLI/billing/media/identity и второй официальный Antigravity boundary не подтверждены |
| 7 | Production bridge | Повторно проверен существующий bridge; реальный Scene import и запуск jobs из v2 ещё не реализованы |
| 8 | QA | Existing технические validators переиспользованы; control video/editorial live review заблокированы Visual |
| 9 | GitHub/Drive | Workflow/scripts подготовлены; runners=0; connector upload/readback manifest VERIFIED; unattended auth/root не подтверждены |
| 10 | Пилот | BLOCKED; не запускать полный выпуск и не выдавать Fake notes за TTS/MP4/upload |

Следующее действие: официальный agy CLI и вход основного Google-аккаунта, без
credential копирования и без смены первого входа ради второго. После help/auth
и безопасного smoke связать adapter с существующим Coordinator. Отдельно доказать
Visual или выбрать explicit manual; отсутствие независимого Visual не скрывать.
Для удалённого fake dispatch зарегистрировать current-user Windows runner с label
ai-video-02. Настроить Drive root/официальную авторизацию и проверку cloud объекта.
Производственный Scene import требует реализации до живого 60–90-секундного пилота.

У каждого этапа — конкретные файлы, test JSON и версия; статус частичного этапа
не повышается из-за документации. Не повторять весь исторический аудит.
