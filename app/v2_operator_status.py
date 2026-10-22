"""Observe/control the existing queue without source/auth/runtime discovery."""
from contextlib import closing
import json
import sqlite3
import time

from .registry import StudioError, canonical_json
from .v2_acceptance import fingerprint
from .v2_run_config import graph, isolated_root, no_links


def readiness(config):
    """Separate runtime observations from static test history and external gates."""
    import sys
    from .v2_run_config import ApprovedInputs
    paths = {key: bool(no_links(value).is_file()) for key, value in config.runtime.items()
             if key in {'tts_python', 'tts_model', 'melt_path', 'ffmpeg_path', 'ffprobe_path'}
             and isinstance(value, str) and value}
    return {'LOCAL_RUNTIME': {'status': 'CONFIGURED_PATHS_EXIST' if len(paths) == 5 and all(paths.values())
                else 'UNVERIFIED' if not paths else 'BLOCKED', 'python': sys.version.split()[0], 'paths_exist': paths,
                'scope': 'existence only; no model, runtime auth or executable probe'},
        'APPROVED_INPUT': {'status': 'TYPED_INPUTS' if isinstance(config.inputs, ApprovedInputs) else 'ACQUISITION_REQUIRED',
            'scope': 'host config structure; plan/doctor additionally verify manifest bytes'},
        'DUAL_WORKER_ROUTING_OFFLINE': {'status': 'OFFLINE_TESTED', 'scope': 'regression history; not a runtime probe',
            'routes_configured': config.worker_routes is not None},
        'STORY_LIVE': 'BLOCKED_ELIGIBILITY', 'VISUAL_LIVE': 'BLOCKED_ELIGIBILITY',
        'DRIVE_REMOTE': 'UNVERIFIED', 'SELF_HOSTED_RUNNER': 'UNVERIFIED',
        'EXACT_SHA_CI': 'UNVERIFIED', 'AUTONOMOUS': False}


def _binding(config):
    root = isolated_root(config.root)
    authority = fingerprint(config.pinned())
    file = no_links(root/'authorizations'/(authority+'.json'))
    if not file.is_file() or json.loads(file.read_text(encoding='utf-8')) != config.pinned():
        raise StudioError('SYSTEM_PERMISSION_REQUIRED', 'Select the exact previously authorized config')
    database = no_links(root/'coordinator/registry.sqlite')
    if not database.is_file():
        raise StudioError('RUN_NOT_PREPARED', 'Prepare this explicit configuration before observing its queue')
    return root, database


def observe(config):
    """Recorded queue facts, not acceptance revalidation or a media/model probe."""
    from .v2_provider_bridge import provider_readiness
    from .v2_delivery_host import drive_host_readiness
    root, database = _binding(config)
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro', uri=True)) as db:
        rows = db.execute('SELECT id,status,result,error FROM ai_tasks WHERE project_id=?', (config.run_id,)).fetchall()
        settings = dict(db.execute('SELECT key,value FROM ai_settings'))
        expansions = [json.loads(row[0]) for row in db.execute(
            "SELECT payload FROM ai_events WHERE kind='system_expansion' ORDER BY sequence")]
    wanted = {node['task_id'] for node in graph(config)}
    final_id=graph(config)[-1]['task_id']
    matching=[v for v in expansions if v.get('authority')==fingerprint(config.pinned())]
    if matching:
        derived=matching[-1]['nodes'];wanted.update(n['task_id'] for n in derived)
        final_id=derived[-1]['task_id']
    tasks = [{'task_id': identifier, 'status': status, 'blocker': json.loads(error) if error else None}
             for identifier, status, result, error in rows if identifier in wanted]
    completed = sum(task['status'] == 'completed' for task in tasks)
    output = next((json.loads(result)['payload'] for identifier, status, result, error in rows
                   if identifier == final_id and status == 'completed' and result), None)
    if output and 'text' in output:
        output = json.loads(output['text'])
    paths = {}
    if output and output.get('portable'):
        folder = no_links(root/output['portable'])
        if folder.is_relative_to(root):
            for name in ('final.mp4', 'project.mlt', 'manifest.json'):
                path = no_links(folder/name)
                if path.is_file(): paths[name] = str(path)
    return {'status': 'RECORDED_LOCAL_PACKAGE' if completed == len(wanted) else 'RECORDED_PROGRESS',
        'run_id': config.run_id, 'mode': config.mode, 'root': str(root), 'completed': completed, 'total': len(wanted),
        'control': json.loads(settings.get('control', '"unknown"')), 'tasks': tasks,
        'paths': paths, 'results': output, 'results_verification': 'NOT_REVALIDATED_READ_ONLY_OBSERVATION',
        'providers': provider_readiness(), 'delivery': drive_host_readiness(),
        'readiness': readiness(config),
        'system_ready_for_autonomous_episode': False, 'model_requests': 0, 'paid_api_requests': 0,
        'production_started': None, 'uploaded': False, 'remote_sync': 'UNVERIFIED',
        'next_action': 'Restore the same approved inputs/runtime before resume; review exact artifacts separately.'}


def request_control(config, command, *, opt_in=False):
    """Use existing control/attempt states. Never infer a child is terminated."""
    from .development import locked
    if opt_in is not True or command not in {'pause', 'stop'}:
        raise StudioError('SYSTEM_PERMISSION_REQUIRED', 'Explicit --execute-system pause/stop required')
    root, database = _binding(config)
    try:
        with locked(root/'coordinator/ai/coordinator.lock'), closing(sqlite3.connect(database.as_uri()+'?mode=rw', uri=True)) as db:
            with db:
                db.execute('UPDATE ai_settings SET value=? WHERE key=?',
                    (canonical_json('stopped' if command == 'stop' else 'paused'), 'control'))
                if command == 'stop':
                    # Same durable states as Coordinator.stop; reservations stay
                    # stopping until the owning adapter verifies cancellation.
                    db.execute("UPDATE ai_tasks SET status='interrupted',current_attempt=NULL,updated=? "
                        "WHERE project_id=? AND current_attempt IN (SELECT id FROM ai_attempts WHERE status IN ('running','stopping'))",
                        (time.time(), config.run_id))
                    db.execute("UPDATE ai_attempts SET status='stopping' WHERE status IN ('running','stopping') "
                        "AND task_id IN (SELECT id FROM ai_tasks WHERE project_id=?)", (config.run_id,))
                db.execute('INSERT INTO ai_events(timestamp,kind,payload) VALUES(?,?,?)',
                    (time.time(), 'control_changed', canonical_json({'control': command, 'source': 'operator',
                        'process_termination_verified': False})))
    except ValueError:
        raise StudioError('COORDINATOR_RUNNING', 'Synchronous operation owns its bounded lock; retry Stop after its deadline/checkpoint') from None
    result = observe(config)
    result.update(status='STOP_REQUESTED' if command == 'stop' else 'PAUSED',
        process_termination_verified=False,
        next_action='Queue dispatch is stopped; inspect/recover owned active attempts before resuming. No process was killed by observation.')
    return result
