# CORE FIX PACK — передача ChatGPT Reviewer

Base: `068ff7fbf11ef48072287546955db489e63166f7`. Ветка: feature/multi-ai-coordinator-v2.
Проверено: 353 tests run, 352 passed, 1 skipped, 0 failed/errors.
Skip: существующий test_native_real_wav_fit_preserves_spoken_request_and_source,
Requires installed local Piper. Реальная TTS не запускалась.

## Результат A–H

- Один незавершённый run на root; active_run_id хранится в SQLite и восстанавливается.
  Terminal освобождает root. Команды другого run отклоняются до управления очередью.
  Проверка ownership и действие сериализованы существующим lock, нового scheduler нет.
- Review не содержит full_video_probe_verified. TrustedVideoProbe хранится в
  ai_v2_capabilities; provider/client version/model/control hash обязаны совпасть.
  Без VERIFIED trusted evidence full_video отклоняется без downgrade.
- Evidence — exclusive UTC+UUID child of --output parent; exclusive writes;
  manifest.json включает SHA-256/bytes всех остальных файлов и полную Git/source/schema
  привязку. Собственный SHA manifest записан здесь, чтобы избежать self-reference.
- source binding рекурсивно включает app/scripts/tests Python, scripts PowerShell,
  schemas JSON, pyproject.toml, .env.example и production_profile.json.
  Schema drift генерирует временные схемы, checked-in schemas не перезаписываются.
- Artifact привязан к runs/run_id; Result сверяет run_id и simulated каждого Artifact.
  Traversal, escape через symlink/junction, неверный hash/size и overwrite отклоняются.
- Hardlink fallback: exclusive destination, streaming copy, flush/fsync, size/SHA,
  cleanup только собственного incomplete файла по file identity. Existing не удаляется.
  remote_sync остаётся UNVERIFIED; production upload не выполнялся.
- Healthcheck --mode fake проверяет Python/deps/root/schema/SQLite/write/conflicts;
  --mode production дополнительно проверяет профиль/FFmpeg/модель/routes и всегда
  блокируется на неподтверждённых Gemini. --require-runner/--require-drive fail closed.
- Exit codes app.multi_ai_v2: 0 SUCCEEDED/SIMULATION_SUCCEEDED; 2 IN_PROGRESS;
  3 WAITING_AUTH; 4 WAITING_QUOTA; 5 BLOCKED_UNSUPPORTED; 6 FAILED_RETRYABLE;
  7 FAILED_FINAL; 8 STALE; 9 ERROR; 10 CANCELLED (reserved).
  Pause/stop остаются resumable, поэтому незавершённый run не даёт exit 0.

## Проверки

Команда: `.venv-core/Scripts/python.exe -X utf8 scripts/check_multi_ai_v2.py --output reports/multi_ai_v2/core_fix_pack/evidence`

| Группа | Tests | Skips | Seconds | Exit |
|---|---:|---:|---:|---:|
| v2 | 28 | 0 | 41.922 | 0 |
| contracts | 14 | 0 | 1.016 | 0 |
| queue | 50 | 0 | 12.625 | 0 |
| adapters | 16 | 0 | 0.453 | 0 |
| mai05 | 107 | 0 | 36.969 | 0 |
| panel | 12 | 0 | 3.594 | 0 |
| legacy | 126 | 1 | 11.421 | 0 |
| compileall | None | 0 | 0.266 | 0 |
| check_git_export | None | 0 | 3.125 | 0 |
| diff_check | None | 0 | 0.062 | 0 |

Все точные argv, elapsed/exit/counts — в REPORT.json и checks.json.
Compileall, read-only check_git_export (включая Git history) и git diff --check passed.
Сканер используется через audit_export: обычный старый main check_git_export писал
бы в исторический отчёт, поэтому его записи сознательно не вызываются.

Evidence: `reports/multi_ai_v2/core_fix_pack/evidence/20261020T145144384909Z_a3e25ab87d9848eba8ed2e82fa297c34`
Manifest SHA-256: `ade40d30410ae37e9cb1aad16957f354dbc387efbfd1f0fd5279d03b248e3b18`
Git commit at check: `abb2552fa40b6d2c8794e4c581ddfe39bf7a503b`
Git tree at check: `3f910329e75a48df08f3b3fc1cfd3238d43ce510`
Dirty: `True` — это честный pre-commit snapshot новых тестов/locking fix.
Source manifest: `0d3eae67f9e283b1712424cd94673e5acab0c1c145c02f0e2e07ad6220581889`
Schema manifest: `42a6be7129325f52638f9cc87fd7e4fc1f73befd498d7d71a63857e528a7983b`
Elapsed sum: 111.453 seconds. Process exit: 0.

Тесты повторного запуска checker и offline probe подтвердили разные каталоги,
полную сохранность первого и совпадение manifest hashes. Первый полный evidence
прогон также сохранён; финальный прогон относится к последнему source hash.
Локальный .gitattributes только нового каталога отчёта запрещает text-конверсию
байтов evidence при Git checkout; исходные/общие Git-настройки не изменены.
Полные безопасные логи включены вместе с их SHA; каждый файл manifest проверен.

## Границы и замечания Reviewer

Все четыре защищённых файла ChatGPT не изменены. Старые Coordinator/Registry,
production engine/profile, T00–T12, Ruslan/WAV/MLT/media и прежний evidence не менялись.
Хеши обеих рабочих SQLite совпадают с сохранённым baseline. Main не изменялся,
merge/force-push/reset/clean не выполнялись. Модельные запросы=0, paid API=0,
production_started=false. Antigravity/Gemini/Cursor/runner не устанавливались.

Вне разрешённого списка scripts/dispatch_multi_ai_v2.py сохраняет старый exit 0/2;
его workflow-совместимость должен учесть Reviewer. Документ VIDEO_REVIEW_PROTOCOL
старого среза ещё описывает удалённый model flag; актуальны schema и этот отчёт.
Эти файлы намеренно не исправлялись в CORE FIX PACK. Blockers пакета: нет.

Коммиты 1–3: 5df2ff7, 6b9d566, abb2552. Четвёртый содержит regression tests,
последнюю locking-защиту и эти evidence; точный SHA сообщён при передаче.
После push работа останавливается до нового поручения.
