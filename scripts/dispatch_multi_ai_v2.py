"""Trusted dispatch entrypoint: workflow inputs are data, never shell code."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.multi_ai_v2 import RunCoordinator
from app.v2_contracts import Brief


def request(value):
    data = json.loads(value)
    for key in ('target_minutes', 'auto_fix_cycles'):
        if key in data: data[key] = int(data[key])
    return Brief.model_validate(data).model_dump()


def main():
    root = os.environ.get('LOCAL_RUN_ROOT')
    run_key = 'github_' + os.environ['GITHUB_RUN_ID']
    if not root: raise ValueError('LOCAL_RUN_ROOT is required; no implicit workspace fallback')
    with RunCoordinator(root) as coordinator:
        state = coordinator.create(request(os.environ['VIDEO_REQUEST_JSON']), run_key)
        state = coordinator.advance(state['run_id'], 60)
        manifest = coordinator.manifest(state['run_id'])
    # Summary deliberately excludes topic, local paths, credentials and raw model text.
    summary = {'run_id': state['run_id'], 'status': state['status'], 'simulated': state['simulated'],
               'ready_for_user': state['ready_for_user'], 'manifest_sha256': manifest['sha256'],
               'drive_result_link': None, 'blockers': state['blockers']}
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a', encoding='utf-8') as stream:
            stream.write('## AI_VIDEO_02\n```json\n' + text + '\n```\n')
    print(text)
    return 0 if state['status'] == 'SIMULATION_SUCCEEDED' else 2


if __name__ == '__main__': raise SystemExit(main())
