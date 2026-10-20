---
source_file: "scripts/check_multi_ai_render.py"
type: "code"
community: "Community 23"
location: "L91"
tags:
  - graphify/code
  - graphify/EXTRACTED
  - community/Community_23
---

# prepare_preview()

## Connections
- [[generated/code/nodes/app_production_bridge_productionbridge_compile.md|dot-compile()]] - `calls` [INFERRED]
- [[generated/code/nodes/app_production_bridge_productionbridge_render.md|dot-render()]] - `calls` [INFERRED]
- [[generated/code/nodes/scripts_check_multi_ai_render_rationale_92.md|Explicit heavy local action; caller must serialize it with other heavy jobs.]] - `rationale_for` [EXTRACTED]
- [[generated/code/nodes/scripts_check_multi_ai_render_py_path.md|Path_12]] - `references` [EXTRACTED]
- [[generated/code/nodes/app_production_bridge_productionbridge.md|ProductionBridge]] - `calls` [EXTRACTED]
- [[generated/code/nodes/app_multi_ai_preview_worker.md|_worker()]] - `calls` [EXTRACTED]
- [[generated/code/nodes/scripts_check_multi_ai_render.md|check_multi_ai_render.py]] - `contains` [EXTRACTED]
- [[generated/code/nodes/scripts_check_multi_ai_render_create_technical_fixture.md|create_technical_fixture()]] - `calls` [EXTRACTED]
- [[generated/code/nodes/app_multi_ai_preview.md|multi_ai_preview.py]] - `imports` [EXTRACTED]
- [[generated/code/nodes/scripts_check_multi_ai_render_run_smoke.md|run_smoke()]] - `calls` [EXTRACTED]
- [[generated/code/nodes/uuid.md|uuid]] - `calls` [EXTRACTED]

#graphify/code #graphify/EXTRACTED #community/Community_23