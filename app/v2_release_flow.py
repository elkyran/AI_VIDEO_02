"""Explicit synthetic Brief rehearsal using the existing SYSTEM handlers.

This is a host fixture handoff, not a live Canon/visual provider protocol. The
original Brief plan stays blocked for live acquisition; no fixture is invented.
"""
import json
from pathlib import Path

from .registry import StudioError, write_generated_json
from .v2_acceptance import fingerprint
from .v2_release_plan import reconcile_inputs, persist_plan
from .v2_run_config import ApprovedInputs, BriefInputs, HostRunConfig, graph, no_links, isolated_root


def save_exact(root, relative, value):
    """Exclusive creation with exact byte replay; preserve conflicting evidence."""
    expected = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n').encode('utf-8')
    file = no_links(Path(root)/relative)
    if file.exists():
        if file.read_bytes() != expected:
            raise StudioError('HOST_RECEIPT_INVALID', 'Immutable host document differs on replay')
        return file
    try:
        return write_generated_json(Path(root), relative, value)
    except StudioError as exc:
        if exc.code != 'OUTPUT_EXISTS' or file.read_bytes() != expected:
            raise
        return file


def synthetic_handoff(config, fixture_path, *, persist=False):
    if config.mode != 'simulation' or config.worker_routes is None:
        raise StudioError('SYNTHETIC_INPUTS_REQUIRED', 'Explicit simulation and both synthetic routes are required')
    if config.version != 1 and isinstance(config.inputs, BriefInputs):
        raise StudioError('SYNTHETIC_INPUTS_REQUIRED', 'Use the materialized config for a later addressed revision')
    path = no_links(fixture_path)
    if path.stat().st_size > min(config.budget.max_input_bytes, 2*1024*1024):
        raise StudioError('INPUT_BUDGET', 'Synthetic acquisition bundle exceeds the explicit input budget')
    raw = json.loads(path.read_text(encoding='utf-8-sig'))
    if type(raw) is not dict or set(raw) != {'inputs', 'worker_results'} or type(raw['worker_results']) is not dict:
        raise StudioError('SYNTHETIC_INPUTS_REQUIRED', 'Only typed inputs and explicit worker results are permitted')
    supplied = ApprovedInputs.model_validate(raw['inputs'])
    acquisition = isinstance(config.inputs, BriefInputs)
    if acquisition:
        inputs = reconcile_inputs(config, canon=supplied.canon.model_dump(), story=supplied.story,
            scenes=[s.model_dump() for s in supplied.scenes], visuals=[v.model_dump() for v in supplied.visuals])
        materialized = HostRunConfig.model_validate({**config.model_dump(), 'inputs': inputs,
            'root': str(isolated_root(config.root)/'execution')})
    else:
        if supplied != config.inputs:
            raise StudioError('SYNTHETIC_INPUTS_REQUIRED', 'Fixture bundle differs from the exact host-approved inputs')
        inputs, materialized = supplied, config
    workers = {}
    expected_scenes = {s.scene_id for s in inputs.scenes}
    if set(raw['worker_results']) != expected_scenes:
        raise StudioError('SYNTHETIC_INPUTS_REQUIRED', 'Every requested Scene needs its own explicit Story and Visual fixtures')
    for node in graph(materialized):
        if node.get('provider_role'):
            values = raw['worker_results'][node['scene_id']]
            if type(values) is not dict or set(values) != {'story', 'visual'}:
                raise StudioError('SYNTHETIC_INPUTS_REQUIRED', 'Both independent fixture outputs must be supplied')
            key = 'story' if node['provider_role'] == 'gemini_story' else 'visual'
            workers[node['task_id']] = {'payload': values[key]}
    if persist and acquisition:
        saved = persist_plan(config, opt_in=True)
        root = isolated_root(config.root)
        receipt = {'kind': 'explicit_synthetic_handoff_not_live_acquisition',
            'original_plan_sha256': saved['plan_sha256'], 'original_config_sha256': fingerprint(config.pinned()),
            'fixture_sha256': fingerprint(raw), 'materialized_config': materialized.pinned(),
            'model_requests': 0, 'paid_api_requests': 0, 'production_started': False}
        save_exact(root, 'reconciliation/'+fingerprint(receipt)+'.json', receipt)
        save_exact(root, 'execution_config.json', materialized.pinned())
        save_exact(root, 'synthetic_inputs.json', raw)
    return materialized, workers
