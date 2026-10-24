# Cloud-only проверка хранилища — 2026-10-24

## Решение владельца

Работать только в GitHub/облаке, без локальных файлов и запуска.
Story/Visual пока оставить выключенными. Облачного Windows-сервера пока нет.
Продолжать код и проверки; создание настоящего выпуска не начиналось.

## Выполненные проверки

| Проверка | Результат |
|---|---|
| Новый Google Drive | Браузер показывает 5 ТБ; папка AI_VIDEO_02 доступна |
| Совпадение Drive-коннектора с выбранным аккаунтом | Подтверждено доступом к той же папке и owner metadata нового документа |
| Изолированная папка результатов | Создана runs/github_ci_2026-10-24 |
| Создание и запись native Google Doc | PASS через коннектор, без локального документа |
| Readback содержания | PASS; заголовки, текст и ссылки получены обратно |
| Перемещение и проверка папки | PASS; документ находится в выбранной папке, sharing=false |
| ZIP GitHub Artifact -> Drive upload_file | BLOCKED_TOOL_LOCAL_PATH: file_uri интерпретируется как Windows filename, error123 |
| ZIP/MP4 unattended runner upload | NOT_VERIFIED |
| Story/Visual live | DISABLED; официальный маршрут и аккаунты не подтверждены |
| Новый TTS/render/video | NOT_RUN |

[Папка результатов](https://drive.google.com/drive/folders/1PAAs7C6uD_FDfsBahkQCfpffUuoJtuc9).
[Проверенный облачный отчёт](https://docs.google.com/document/d/1AEKqO1uEQF-J-08SOJxUWcpibuQNtzOs1pd9LxOOl3g/edit).

Архив GitHub Artifact12554791132 был получен серверным GitHub-коннектором как
file reference. Попытка передать structured reference Drive upload_file выявила
ограничение локального-path интерфейса. После ошибки содержимое папки проверено:
ZIP отсутствовал. Две одинаковые ошибки сохранены в истории этого сеанса; этот
маршрут дальше не повторяется. На пользовательский ПК архив не скачивался.
Подписанные download URLs и секреты в отчёт не включены.

Это PASS для native Docs через интерактивный коннектор, а не успешная
загрузка ZIP/MP4 и не unattended Drive host authorization.
Состояние app.v2_drive/live provider/runtime не повышается из-за этой проверки.
Новый Drive предоставляет хранилище, но не установленный Piper/MLT renderer.

## CI

Ранее выполненные PR9 и PR6 CI остаются доказательствами своих точных версий:
- [40077604785](https://github.com/elkyran/AI_VIDEO_02/actions/runs/40077604785);
- [40078748459](https://github.com/elkyran/AI_VIDEO_02/actions/runs/40078748459).

Каждый:642 tests=640 passed+2 existing environment skips, failures/errors=0.
В этом пакете меняются только документация и облачный отчёт.
Неизменённый Python-код без основания не пересобирался и не тестировался повторно.

## Новый подтверждённый внешний gate

Проверено по официальному FAQ24октября2026:
agy -p предназначен для прямых скриптов и CI, но wrapping agy внутри сторонних
coding agents/applications и account rotation не поддерживаются consumer plans.
Для стороннего coding agent Google рекомендует Gemini Enterprise или AI Studio API key.
Это означает, что обычный Google-вход не подтверждает допустимость нынешней
двухаккаунтной интеграции. Live adapter оставлен выключенным по решению владельца.

Источники:
- [Google FAQ](https://www.antigravity.google/docs/faq/#can-i-use-antigravity-cli-headless-mode-in-third-party-coding-agents-or-automate-account-switching)
- [Remote Control](https://antigravity.google/docs/remote-control)

## Дальнейшая граница

Для настоящей сборки нужен отдельно выбранный облачный Windows-host с runtime/model
и host-bound Drive authorization. Запускать его, подключать credentials, покупать
сервер/API, менять производственный профиль или включать Story/Visual в этом пакете
не требуется и не выполнялось. Официальный supported route должен быть подтверждён
до live реализации; offline tests не заменяют такой допуск.
