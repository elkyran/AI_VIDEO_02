"""Trusted optional JobManager observer; no process discovery or JSON callbacks."""
import json
import os
from pathlib import Path
import uuid
import sqlite3
import sys
import time

from .jobs import JobManager
from .registry import Registry, StudioError
from .v2_acceptance import fingerprint


def spawned_identity(process):
    """Identity from the exact newly created object/handle, never a registry PID."""
    try:
        if type(process.pid) is not int or process.pid<=0 or process.poll() is not None:return None
        if os.name=='nt':
            import ctypes
            from ctypes import wintypes
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.GetProcessId.argtypes=[wintypes.HANDLE];kernel.GetProcessId.restype=wintypes.DWORD
            kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
            kernel.GetProcessTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)]
            handle=int(process._handle)
            if kernel.GetProcessId(handle)!=process.pid:return None
            code=wintypes.DWORD();times=[wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)) or code.value!=259:return None
            if not kernel.GetProcessTimes(handle,*(ctypes.byref(t) for t in times)):return None
            birth=str((times[0].dwHighDateTime<<32)|times[0].dwLowDateTime)
        else:
            # PID originates only in this trusted Popen object. No SQLite PID can
            # enter this initial-ownership path; poll brackets the /proc lookup.
            fields=Path(f'/proc/{process.pid}/stat').read_text().split(') ',1)[1].split()
            if fields[0]=='Z':return None
            birth=fields[19]
        return birth if process.poll() is None else None
    except (OSError,AttributeError,TypeError,ValueError,IndexError):return None


def binding(pipeline,job):
    with Registry(pipeline.engine) as db:
        requests=[(r['key'],r['request_hash'],json.loads(r['response'])) for r in db.db.execute(
            'SELECT key,request_hash,response FROM requests WHERE project_id=?',(job['project_id'],))]
    selected=[(key,sha) for key,sha,value in requests if value.get('job_id')==job['job_id']]
    if len(selected)!=1 or job['project_id']!=pipeline.run_id:
        raise StudioError('JOB_OWNERSHIP_UNVERIFIED','Exact job/request binding is required')
    key,sha=selected[0]
    expected=fingerprint({'op':'enqueue','operation':job['operation'],'parameters':job['parameters'],
                          'base_revision':job['input_revision']-1})
    if sha!=expected:raise StudioError('JOB_OWNERSHIP_UNVERIFIED','Job request hash changed')
    return {'engine':str(Path(pipeline.engine).resolve()),'run_root':str(Path(pipeline.root).resolve()),
        'run_id':pipeline.run_id,'job_id':job['job_id'],'operation':job['operation'],
        'input_revision':job['input_revision'],'request_key':key,'request_sha256':sha}


class SpawnObserver:
    def __init__(self,pipeline,*,deadline=float('inf')):
        self.pipeline=pipeline;self.intents={};self.deadline=deadline

    def __call__(self,phase,job_id,process):
        from .v2_job_recovery import checkpoint,record
        p=self.pipeline
        if phase in {'admit','intent'} and time.monotonic()>=self.deadline:
            raise StudioError('TIME_BUDGET','Deadline expired before job/spawn admission')
        if phase=='admit':
            # Called INSIDE the existing enqueue/resume writer transaction. A
            # read-only connection sees the stable pre-insertion queue snapshot.
            connection=sqlite3.connect((Path(p.engine)/'registry.sqlite').as_uri()+'?mode=ro',uri=True)
            try:busy=connection.execute("SELECT 1 FROM jobs WHERE state IN ('pending','running') AND id!=? LIMIT 1",(job_id,)).fetchone()
            finally:connection.close()
            if busy:raise StudioError('JOB_QUEUE_OWNERSHIP','Another active job prevents atomic SYSTEM admission')
            return
        job=JobManager(p.engine).status(job_id);base=binding(p,job)
        if phase=='intent':
            with Registry(p.engine) as db:
                active=[r[0] for r in db.db.execute("SELECT id FROM jobs WHERE state IN ('pending','running')")]
            if active!=[job_id] or job['state']!='pending':
                raise StudioError('JOB_QUEUE_OWNERSHIP','SYSTEM requires exactly its one active per-run job')
            intent_key=fingerprint({**base,'expected_attempt':job['attempts']+1})
            intent={**base,'expected_attempt':job['attempts']+1,'intent_key':intent_key,'spawn_token':uuid.uuid4().hex}
            with Registry(p.q.root) as db,db.transaction():
                row=db.db.execute("SELECT payload FROM ai_events WHERE kind='system_spawn_intent' AND task_id=? ORDER BY sequence DESC LIMIT 1",(job_id,)).fetchone()
                if row and json.loads(row[0])['intent_key']==intent_key:
                    raise StudioError('JOB_RECOVERY_HOLD','Spawn intent already exists; no ambiguous repeat')
                p.q.queue._event(db,'system_spawn_intent',intent,job_id)
            self.intents[job_id]=intent
            if time.monotonic()>=self.deadline:
                raise StudioError('TIME_BUDGET','Deadline expired while saving spawn intent; preserved for inspection')
        elif phase=='spawned':
            intent=self.intents.pop(job_id,None)
            if not intent or any(intent[k]!=v for k,v in base.items()):
                raise StudioError('JOB_RECOVERY_HOLD','Created process lost its exact durable spawn intent')
            expected=[sys.executable,'-m','app.worker','--workspace',str(Path(p.engine).resolve()),'--job',job_id]
            if getattr(process,'args',None)!=expected:
                raise StudioError('JOB_OWNERSHIP_UNVERIFIED','Created process argv differs from fixed job/workspace')
            birth=spawned_identity(process)
            if birth is None:
                record(p,'system_spawn_unknown',job_id,{'intent_key':intent['intent_key'],'identity':'UNKNOWN'})
                raise StudioError('JOB_OWNERSHIP_UNVERIFIED','Created process exited or birth identity is unavailable')
            record(p,'system_spawn_receipt',job_id,{**intent,'worker_pid':process.pid,'birth':birth,
                'scope':'EXACT_SPAWN_OBJECT','identity_source':'Popen process handle' if os.name=='nt' else 'Popen process object /proc'})
        else:raise ValueError('Unknown trusted spawn observation phase')


def exact_receipt(pipeline,job):
    from .v2_job_recovery import checkpoint
    receipt=checkpoint(pipeline,'system_spawn_receipt',job['job_id'])
    intent=checkpoint(pipeline,'system_spawn_intent',job['job_id'])
    base=binding(pipeline,job)
    expected_attempt=job['attempts']+1 if job['state']=='pending' else job['attempts']
    if (not receipt or not intent or receipt.get('scope')!='EXACT_SPAWN_OBJECT'
            or any(receipt.get(k)!=v for k,v in base.items())
            or receipt.get('expected_attempt')!=expected_attempt
            or receipt.get('intent_key')!=fingerprint({**base,'expected_attempt':expected_attempt})
            or receipt.get('intent_key')!=intent.get('intent_key')
            or receipt.get('spawn_token')!=intent.get('spawn_token')
            or not receipt.get('birth') or type(receipt.get('worker_pid')) is not int
            or job['worker_pid'] is not None and receipt['worker_pid']!=job['worker_pid']):
        raise StudioError('JOB_OWNERSHIP_UNVERIFIED','No exact spawned-object receipt for this job/attempt')
    return receipt
