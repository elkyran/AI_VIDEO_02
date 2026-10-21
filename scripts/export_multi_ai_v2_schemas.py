"""Generate checked-in schemas from existing and new contracts; no live clients."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.v2_contracts import Brief, Task, Result, Artifact, Review
from app.editorial import Scene, EditPlan
from app.v2_run_config import HostRunConfig
from app.v2_provider_protocol import DualWorkerConfig


def main():
    folder = Path(__file__).resolve().parents[1] / 'config/multi_ai_v2_schemas'
    folder.mkdir(exist_ok=True)
    for name, model in {'brief': Brief, 'task': Task, 'result': Result, 'artifact': Artifact,
                        'scene': Scene, 'edit_plan': EditPlan, 'review': Review, 'host_run_config': HostRunConfig,
                        'dual_worker_config': DualWorkerConfig}.items():
        (folder / (name + '.schema.json')).write_text(json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__': main()
