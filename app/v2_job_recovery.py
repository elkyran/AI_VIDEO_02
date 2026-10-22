"""Conservative caller guards around the existing JobManager.resume helper."""
import json

from .registry import Registry, StudioError
from .v2_acceptance import fingerprint


def checkpoint(pipeline, kind, job_id):
    with Registry(pipeline.q.root) as registry:
        row=registry.db.execute('SELECT payload FROM ai_events WHERE kind=? AND task_id=? ORDER BY sequence DESC LIMIT 1',
            (kind,job_id)).fetchone()
    return json.loads(row[0]) if row else None


def record(pipeline, kind, job_id, payload):
    with Registry(pipeline.q.root) as registry, registry.transaction():
        pipeline.q.queue._event(registry,kind,payload,job_id)


def existing_request(pipeline, manager, script_id, candidate, key):
    with Registry(pipeline.engine) as registry:
        prior=registry.db.execute('SELECT request_hash,response FROM requests WHERE project_id=? AND key=?',
            (pipeline.run_id,key)).fetchone()
        if not prior:return None
        receipt=json.loads(prior['response']);job=manager.status(receipt['job_id'])
        request={'op':'enqueue','operation':'synthesize','parameters':{'script_asset_id':script_id},
            'base_revision':job['input_revision']-1}
        script=registry.asset(pipeline.run_id,script_id,verify=True)
        from .registry import workspace_path
        if (prior['request_hash']!=fingerprint(request) or receipt['project_id']!=pipeline.run_id
                or receipt['revision']!=job['input_revision'] or job['project_id']!=pipeline.run_id
                or job['operation']!='synthesize' or job['parameters']!=request['parameters']
                or json.loads(workspace_path(pipeline.engine,script['path']).read_text(encoding='utf-8'))!=candidate['script']):
            raise StudioError('IDEMPOTENCY_CONFLICT','Existing job/request/script binding differs; preserved')
    return job


def observe_new_worker(pipeline, job):
    # Kept for callers of the old observation hook. A status PID can never create
    # ownership. Only the trusted _spawn observer may persist a strong receipt.
    return None


def guarded_resume(pipeline, manager, job):
    if job['invalidated'] or job['state']=='stale':
        raise StudioError('JOB_STALE','Inputs were invalidated; keep the old job and authorize a new version')
    if job['cancel_requested'] or job['state']=='cancelled' or (job.get('error') or {}).get('code')=='CANCELLED':
        raise StudioError('CANCELLED','Cancellation is terminal for automatic recovery')
    if job['state']=='done':return False  # Generic caller still verifies all WAV/job hashes.
    if job['state'] not in {'pending','running','failed','blocked'}:
        raise StudioError('JOB_TERMINAL','State is outside automatic recovery scope')
    from .v2_spawn_receipts import exact_receipt,binding
    try:identity=exact_receipt(pipeline,job)
    except StudioError:
        if checkpoint(pipeline,'system_spawn_intent',job['job_id']):
            raise StudioError('JOB_RECOVERY_HOLD','Spawn has an intent without its exact current receipt') from None
        raise
    from .multi_ai_preview import process_identity
    current=process_identity(identity['worker_pid'])
    if current is None or current is not False and current!=identity['birth']:
        raise StudioError('JOB_LIVENESS_UNVERIFIED','Unknown or foreign process identity; no restart')
    if current is not False:return False
    if job['state']=='pending':
        raise StudioError('JOB_RECOVERY_HOLD','Exact child exited before claiming; no safe pending restart')
    if job['state'] in {'failed','blocked'}:
        policy=getattr(pipeline.config,'acquisition',None)
        allowed=policy.retryable_job_errors if policy else []
        if (job.get('error') or {}).get('code') not in allowed:
            raise StudioError('JOB_TERMINAL','Failure is outside the explicit retryable error policy')
    if job['attempts']>=min(3,pipeline.config.budget.attempts):
        raise StudioError('RETRY_LIMIT','Host attempt budget exhausted; no automatic restart')
    token=fingerprint(binding(pipeline,job))  # Stable across attempts: at most one automatic resume.
    with Registry(pipeline.q.root) as registry,registry.transaction():
        intents=[json.loads(r[0]) for r in registry.db.execute(
            "SELECT payload FROM ai_events WHERE kind='system_job_resume_intent' AND task_id=?",(job['job_id'],))]
        if any(v['intent_key']==token for v in intents):
            raise StudioError('JOB_RECOVERY_HOLD','A durable resume intent already exists; inspect its ambiguous checkpoint')
        if manager.status(job['job_id'])!=job:
            raise StudioError('JOB_RECOVERY_HOLD','Job changed during recovery admission')
        pipeline.q.queue._event(registry,'system_job_resume_intent',{'intent_key':token,
            'input_revision':job['input_revision'],'attempts':job['attempts']},job['job_id'])
    # Cross-database atomicity is not claimed. If interrupted after this intent,
    # replay holds rather than invoking the permissive core helper a second time.
    result=manager.resume(job['job_id'])
    record(pipeline,'system_job_resume_observed',job['job_id'],{'intent_key':token,'state':result['state']})
    observe_new_worker(pipeline,result)
    return True
