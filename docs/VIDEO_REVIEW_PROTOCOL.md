# Техническая и художественная проверка

Python QA проверяет контейнер, потоки, тайминг, WAV/ASR, субтитры, MLT и хеши.
Художественную проверку выполняет отдельный reviewer. Старые QA jobs не
подтверждают новое финальное видео после изменения голоса или монтажа.

| Состояние | Необходимое доказательство |
| --- | --- |
| FULL_VIDEO_REVIEW_VERIFIED | Действующий host probe, точная привязка клиента/моделей/аккаунта, проверенный video artifact и его ссылка в materials_actually_reviewed |
| FRAME_AND_TIMELINE_REVIEW_VERIFIED | Отдельная проверка реально доставленных кадров, timeline и сопутствующих материалов; full-video capability не подразумевается |
| UNVERIFIED | Доказательств недостаточно или контрольный тест не выполнялся |
| BLOCKED | Проверка отклонена, истекла, отозвана, материалы изменились или привязка не совпала |

Сейчас Gemini Visual и кадровый fallback не подключены; оба остаются UNVERIFIED.
Локальная граница проверяет свидетельства, но не запускает просмотр видео.
Fake review является симуляцией и никогда не является творческим QA.

Доверенный host оператор задаёт отдельный `video_evidence_root` вне репозитория,
sync-каталогов и model-writable run storage. Права ОС должны разрешать запись в
этот каталог только host оператору. Модель не получает его для записи.
Без явно настроенного корня VERIFIED не принимается.

Host сохраняет receipt JSON обычным файлом в этом каталоге. `evidence_refs`
содержит один относительный путь; ссылки/junction и выход из каталога запрещены.
`result_hash` — SHA-256 исходных байтов receipt. Receipt содержит `evidence_id`,
`binding`, `expected_features` и `observed_features`. Оба набора признаков должны
точно совпадать с host challenge из `app/v2_video.py`: красный 0–3 с, синий 3–6 с,
зелёный 6–9 с, CONTROL-VIDEO-OK и аудиосигнал в 4.5 с. Receipt не может
переопределить ожидаемый challenge. Реальный control-video model call в этом
пакете не выполняется: это только локальная доверенная граница.

Host вызывает `record_video_probe`, а не передаёт probe внутри Review.
Метаданные хранятся в существующей Registry, `ai_v2_capabilities`, по уникальному
evidence_id. Точный повтор идемпотентен; изменённый payload с тем же ID отклоняется.
Новый probe требует нового ID. Отзыв добавляется через `revoke_video_probe`,
исходная запись сохраняется. Модельный Review запрещает дополнительные поля:
он не может сам объявить VERIFIED или передать receipt как подтверждение capability.

`checked_at` и `expires_at` содержат timezone. Максимальная жизнь — 24 часа,
погрешность будущего checked_at — 60 секунд; expires_at строго позже checked_at.
При регистрации и каждом full-video acceptance проверяются срок, неизменившийся
receipt hash, статус VERIFIED и отсутствие отзыва. Тесты используют injectable
clock без sleep.

Binding включает provider, provider_profile_id, account_fingerprint (null, если
используется profile ID), client, client_version, requested_model, returned_model
и control_video_hash. Изменение любого поля требует нового probe.
`accept_review` получает binding и evidence_id от host, сверяет их полностью и
проверяет реальный, несинтетический video artifact через ArtifactStore.verify.
`materials_actually_reviewed` должен содержать точный путь этого видео. Это
проверяет целостность доставки; правдивость фактического просмотра остаётся
обязанностью доверенного host/reviewer. Несовпадение даёт
VIDEO_CAPABILITY_UNVERIFIED без автоматического перехода на кадры.

Для кадровой проверки целевые материалы: keyframes/contact sheet, timeline,
transcript.srt, audio_report.json, edit_plan.json, scene_manifest и technical_qa.
В отчёте перечисляются только реально просмотренные материалы с версиями и
хешами. Кадровая проверка не получает full-video статус.

Категории canon/causality/text → Story; visual/pacing → Visual; mlt → Codex;
file/hash → Python. Неизвестные доказательства означают blocked. Допускается
один review и одна адресная правка, без бесконечных циклов.
