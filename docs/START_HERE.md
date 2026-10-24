1. Откройте PowerShell в AI_VIDEO_02; используйте `studio-v2.cmd` (старый `studio.cmd` сохранён).
2. Выберите явный host JSON: тема, язык, длительность, разрешённые источники и mode; пример — `examples/release_01`.
3. Укажите новый run root вне repo/OneDrive/Google Drive/studio и отдельный input_root.
4. Source-only acquisition принимает Brief + sources/media + явную acquisition policy; Canon/outline/Scenes/mappings поступают через очередь. Для offline нужен внедрённый SyntheticTransport; director требует отдельную host factory. Границы и команды: `docs/SYSTEM_CLOSURE_02.md`. Старый simulation bundle совместим; autonomous сейчас BLOCKED_ELIGIBILITY.
5. Выполните `./studio-v2.cmd --config C:/AI_VIDEO_02_inputs/config.json plan`.
6. Затем `./studio-v2.cmd --config C:/AI_VIDEO_02_inputs/config.json doctor`; устраните конкретные blockers.
7. Только с отдельным разрешением: `./studio-v2.cmd --config C:/AI_VIDEO_02_inputs/config.json --execute-system --authorize start`.
8. Статус: та же команда с `--execute-system status`; пути MP4/MLT/manifest появляются только после принятия файлов.
9. Остановка/продолжение: `--execute-system stop` / `--execute-system resume` с тем же config; принятые этапы сохраняются.
10. Review: `--execute-system review --review C:/AI_VIDEO_02_inputs/owner_review.json`; точные hash/version обязательны.
11. Deliver: `--execute-system deliver`; без host auth — WAITING_AUTH, локальный пакет сохраняется; upload сам не разрешается.
12. Live Story/Visual, Drive remote, runner и CI проверяются отдельно; локальные tests не подтверждают полную автономность.
