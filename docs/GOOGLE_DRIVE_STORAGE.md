# Google Drive как хранилище

GOOGLE_DRIVE_ROOT — явный корень смонтированной папки AI_VIDEO_02 в Drive.
LOCAL_RUN_ROOT и LOCAL_CACHE_ROOT должны находиться на локальном диске вне
синхронизации. Активный registry.sqlite никогда не копируется в Drive как рабочая БД.
Снимки SQLite создаются только backup API после согласования состояния.

Целевая структура: runs/RUN_ID/{sources,source_index,canon,story,voice,visuals,
edit,previews,reviews,final,manifests,backups}. В первом срезе ArtifactStore хранит
неизменяемые JSON по SHA-256 в runs/RUN_ID/artifacts; полная доставка медиа ещё
NOT_IMPLEMENTED. copy_to_drive проверяет allowlist, источник, размер и хеш,
закрывает staging-файл до назначения, не перезаписывает конфликтующий файл.
На файловой системе без hardlink вернёт ошибку; существующие данные сохраняются.

Результат локального mount-copy — COPIED_VERIFIED, remote_sync=UNVERIFIED,
uploaded=false. Это проверено на временных папках, а не в облаке. Наличие файла
в смонтированной папке не подтверждает синхронизацию с Google. Настоящий uploaded
требует отдельной проверки удалённого объекта/размера/хеша через официальный
маршрут. Такая проверка выполнена ниже для маленького connector manifest;
ёмкость 5 ТБ и производственный unattended upload не подтверждены.

Во время работы Google Drive был подключён в Codex. Создана папка AI_VIDEO_02 и
загружен проверочный manifest (373 байта); raw readback совпал побайтово и по
SHA-256. Evidence: reports/multi_ai_v2/evidence/20261020T131633Z/drive_probe.json.
Это VERIFIED для connector upload, а не для будущего unattended runner.
Подключение Codex само по себе не делает unattended runner авторизованным API;
для него ещё нужен официальный путь авторизации либо настроенный Drive for
desktop и явный root. Реальная квота хранения 5 ТБ не измерена.
Credential stores и OAuth никогда не попадают в Git, task pack или Drive.
