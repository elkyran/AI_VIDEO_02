# Canonical review и доверенное принятие

Единственный модельный контракт — `app.editorial.Review`. Импорт
`app.v2_contracts.Review` является alias того же класса. CLI output_schema,
editorial validator и checked-in review.schema.json используют одну JSON Schema.
Модель создаёт только creative payload, с evidence_mode и coverage.

| Статус проверки | Необходимое доказательство |
| --- | --- |
| FULL_VIDEO_REVIEW_VERIFIED | Canonical render/video_audio Review, действующий capability probe, отдельный host delivery receipt конкретного файла/ответа и успешный unified Result gate + queue CAS |
| FRAME_AND_TIMELINE_REVIEW_VERIFIED | Только фактически предоставленные кадры/контекст/timeline, подтверждённые editorial validator; полный просмотр и прослушивание не подразумеваются |
| UNVERIFIED | Доказательств недостаточно или контрольный тест не выполнялся |
| BLOCKED | Evidence истекло, отозвано, не совпадает или нарушена целостность |

Gemini Visual и live delivery adapter в этом пакете не подключаются. Fake review
всегда simulated, не устанавливает creative readiness и не проходит full-video gate.
Python QA проверяет техническую целостность; художественные решения остаются reviewer.

`Coordinator(result_gate=None)` сохраняет прежнее поведение. RunCoordinator
подключает V2ResultGate до recovery. Ручной accept_result, automatic _poll и
recovered completion проходят один gate до принятия. Gate проверяет зарегистрированные
task/run/contract/attempt, актуальные входы, schema, provenance, editorial payload и
каждый Artifact. После gate очередь пишет immutable completion и выполняет прежний
CAS. При recovery gate выполняется снова; файл completion сам по себе не даёт acceptance.
Неожиданная ошибка/неверный ответ gate также идут в fail-closed attempt failure.

Host envelope содержит task_id, run_id, acceptance_key (= attempt_id), schema,
input_version/hashes, creative payload, host-measured artifacts, simulated и
host_review_delivery. Эти поля никогда не извлекаются из model payload. Для live
completion envelope обязателен; fake envelope создаёт сам host. Модельные поля
VideoEvidence, delivery receipt, billing/quota или identity внутри creative payload
отклоняются строгим editorial validator.

Metadata_only, contact_sheet и frame_sequence сохраняют ограничения editorial
validator: кадры не доказывают прослушивание, ограниченные материалы не дают render
ready_for_review, coverage не превышает host delivery. Media authorization берётся
из сохранённого host request; поздний task context не может расширить её. Для render/video_audio и для
любого render ready_for_review обязательно отдельное video delivery evidence.
`RunCoordinator.accept_review` — только read-only compatibility helper: проверка
структуры/capability не завершает task, не пишет completion/accepted artifact и не
заменяет accept_result. Его возвращаемый Review не является решением о принятии.

Host оператор задаёт `video_evidence_root` вне репозитория, sync-папок и любого
пересечения с model-writable run storage. Права ОС должны разрешать запись только
host оператору. Без явно настроенного корня VERIFIED не принимается. Все receipt —
обычные файлы под этим корнем; symlink/junction/reparse point и выход из корня запрещены.
SHA-256 проверяется по исходным байтам, включая при replay. Модель не получает этот
корень для записи и не может передать receipt как собственное доказательство.

Capability probe сохраняется immutable в Registry по evidence_id. Точный повтор
идемпотентен; другой payload с тем же ID конфликтует; новый probe требует нового ID.
Receipt содержит evidence_id, binding, expected_features и observed_features. Оба
набора признаков должны совпадать с host challenge из app/v2_video.py: красный 0–3 с,
синий 3–6 с, зелёный 6–9 с, CONTROL-VIDEO-OK, аудиосигнал в 4.5 с. Receipt не задаёт
собственный challenge. Максимальная жизнь probe — 24 часа, будущая погрешность —
60 секунд, timestamps timezone-aware. Отзыв — append-only revoke_video_probe.

Binding включает provider/profile/account fingerprint, client/version,
requested/returned model и control_video_hash. Любое изменение требует нового probe.
Probe доказывает только capability; он не доказывает доставку конкретного preview.

Для каждого полного review host создаёт отдельный HostReviewDelivery. Он связывает
уникальный delivery_id, task/run/attempt, evidence_id и тот же binding, точный video
artifact ID/path/hash/size, input version/hashes, текущую edit plan version,
фактически доставленную coverage и SHA-256 нормализованного canonical Review payload.
Requested/completed/expires timestamps timezone-aware; выполнение запроса ограничено
одним часом, срок после completion — ещё максимум один час, future skew — 60 секунд.
Просроченная или отозванная доставка отклоняется независимо от живого capability.
Отзыв — append-only revoke_video_delivery.

Delivery receipt JSON содержит все поля HostReviewDelivery, кроме receipt_ref и
receipt_sha256 (исключены для отсутствия самоссылки). Содержимое точно сверяется с
host envelope, raw hash — с receipt_sha256. Gate сверяет provider/profile с attempt,
requested model с task contract, binding с probe и rechecks ArtifactStore. Review
subject_id == render_id == video artifact ID; subject_hash == render_hash == video
SHA-256; edit_plan_version совпадает с текущей registered task и принятыми plan
зависимостями. Coverage проверяется настоящим editorial validator по host receipt.
Simulated coverage внутри live Review или host delivery отклоняется.
Это trusted delivery/identity/integrity boundary, а не математическое доказательство
внутреннего восприятия модели. Настоящие модели и видео для этих тестов не запускаются.

Принятые v2_acceptance, v2_artifacts и v2_host_delivery хранятся в ai_tasks.result,
без второй result table/очереди. Replay сверяет immutable completion, точный список
artifact metadata, receipt и каждый файл; изменённый/исчезнувший файл отклоняется,
другой Result конфликтует. Per-attempt Context Pack берёт input_artifacts только
из этих принятых metadata, сверяет их с completion и повторно проверяет hash/size.
Произвольные строки или file lists внутри model payload не становятся file inputs.

Локальные проверки сохраняют pre-commit/dirty evidence отдельно. Clean-final proof
создаёт GitHub CI после push и хранит внешним immutable artifact. Production,
provider login, TTS/render, реальный video review и Drive delivery этим пакетом не запускаются.
