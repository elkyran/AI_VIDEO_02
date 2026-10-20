# Границы безопасности

Существующие production база, Ruslan WAV/cache/Timeline/MLT и ручные правки
сохраняются. V2 отказывается работать в legacy queue и синхронизируемой папке.
Пути разрешаются через existing workspace_path; traversal и protected manual/
originals запрещены. Это allowlist записи внутри конкретного root, а не sandbox
для произвольной программы. Символические ссылки проверяются через resolve.

Официальный Codex smoke запускается массивом аргументов, stdin, timeout,
read-only sandbox, shell_tool=false, web_search=disabled; API env отфильтрованы,
forced_login_method=chatgpt. Credential-файлы не читаются. Secret redaction —
дополнительная защита, а не разрешение помещать секреты в brief. Модель не получает
учётные данные и весь репозиторий. Полные локальные логи не загружаются в Actions.

Workflow только private + workflow_dispatch, contents:read, pinned checkout,
clean:false и persist-credentials:false. Это scaffold без зарегистрированного
runner и без реального production dispatch. Не добавлять pull_request/fork trigger
на self-hosted машину с аккаунтами. Проверять изменяемый код перед dispatch.
Не изменялись firewall, глобальные настройки, billing, службы и scheduled tasks.

Git исключает credentials, .env (кроме пустого .env.example), модели, WAV/MP4,
активные SQLite и backups. Хранить в Git только redacted JSON evidence и docs.
Backup ZIP/SQLite остаются локальными и исключёнными. Push --mirror запрещён.
