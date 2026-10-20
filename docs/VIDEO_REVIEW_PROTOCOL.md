# Техническая и художественная проверка

Существующий Python QA проверяет контейнер, потоки, тайминг, WAV/ASR, субтитры,
MLT и хеши. Он не принимает художественные решения. Старые QA jobs не означают
проверку нового финального видео после внешней замены голоса.

Gemini Visual получает точные версии сцены, WAV duration, transcript, кандидаты,
coverage и edit plan. Результат review_mode, verdict и materials_actually_reviewed
должен честно соответствовать доставленным данным. Full video требует отдельного
контрольного теста: красный 0–3, синий 3–6, зелёный 6–9, CONTROL-VIDEO-OK и
аудиосигнал с известным временем. Сохранить input hash, client/model, команду,
реальный output и timestamp. В этом срезе тест не выполнен: Visual заблокирован.
Файлы/fallback сами по себе не подтверждают просмотр; оба capability UNVERIFIED.

Для кадрового fallback целевой пакет: preview_lowres.mp4, keyframes, contact_sheet,
transcript.srt, audio_report.json, edit_plan.json, scene_manifest и technical_qa.
Он пока NOT_IMPLEMENTED новым фасадом; existing editorial coverage validator
переиспользован. Ни frames, ни fake review не называются full_video.

v2 Review проверяет интервалы и full-video probe flag, existing editorial.Review
дополнительно связывает hash/версию и запрещает simulated ready_for_review.
Истинность probe flag обязан проверять доверенный Coordinator, а не модель;
live acceptance boundary пока не подключена. Категории canon/causality/text →
Story; visual/pacing → Visual; mlt → Codex; file/hash → Python. Доказательства
неизвестны — blocked. Один review и одна правка; бесконечные циклы запрещены.
