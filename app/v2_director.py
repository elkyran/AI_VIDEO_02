"""Existing provider interface, narrow EditPlan scope, ordinary SYSTEM gate."""
from copy import deepcopy
import json
from pathlib import Path
import uuid

from .editorial import EditPlan, validate_result
from .registry import StudioError, write_generated_json
from .v2_acquisition import unwrapped
from .v2_contracts import Result, Task
from .v2_provider_bridge import ProviderBridge
from .v2_acceptance import fingerprint


def fix_controller(context, *, authorized, cycles):
    """One addressed edit cycle, using canonical accepted Review dependencies."""
    from .editorial import Review,validate_review
    values=[unwrapped(v) for v in context.get('dependency_results',{}).values()]
    prior=next((v for v in values if 'plan_id' in v),None)
    review=next((v for v in values if 'review_id' in v),None)
    if prior is None or review is None:raise StudioError('NEEDS_REVIEW','Fix requires accepted exact plan and Review')
    review=Review.model_validate(validate_review(review,{'project_id':prior['project_id'],'subject_type':'edit_plan',
        'subject_id':prior['plan_id'],'subject_version':prior['version'],'subject_hash':fingerprint(prior),
        'provided_evidence_mode':'metadata_only'}))
    issues=[i for i in review.issues if i.status=='open']
    if not issues:return {'action':'reuse_accepted','prior':prior}
    if not authorized or cycles!=1:raise StudioError('NEEDS_REVIEW','One explicit host fix budget is required')
    if len(issues)!=1:raise StudioError('NEEDS_REVIEW','Only one unambiguous addressed edit issue is executable')
    issue=issues[0];locations={p['placement_id'] for p in prior['shot_placements']}
    if issue.category not in {'continuity','repetition','pacing','technical'} or issue.location not in locations:
        owner='gemini_story' if issue.category in {'story_causality','motivation'} else 'gemini_visual' if issue.category=='visual_relevance' else None
        raise StudioError('NEEDS_REVIEW','Requested scope requires '+(owner or 'owner')+'; automatic edit-only controller cannot perform it')
    return {'action':'execute','issue_id':issue.issue_id,'location':issue.location,
        'instruction':issue.suggested_change,'prior_sha256':fingerprint(prior),'max_cycles':1,'owner':'codex'}


def proposal(context):
    values=[unwrapped(v) for v in context.get('dependency_results',{}).values()]
    scene=next((v for v in values if 'narration_segments' in v),None)
    measured=next((v for v in values if 'edit_plan' in v and 'audio' in v),None)
    if scene is None or measured is None:
        raise StudioError('DIRECTOR_MEASUREMENTS_REQUIRED','Director needs accepted Scene and measured WAV proposal')
    return scene,measured


def validate_plan(payload, context):
    scene,measured=proposal(context)
    plan=EditPlan.model_validate(validate_result('edit_plan',payload,context))
    original=EditPlan.model_validate(measured['edit_plan'])
    # Narrow director scope: picture choices/cuts and explanation only. Audio,
    # text, measured duration and profile are host-owned immutable constraints.
    for key in ('project_id','plan_id','scene_id','version','scene_version','voice_version','fps','width',
                'height','duration_ms','voice_placements','audio_events','subtitle_events','simulated'):
        if getattr(plan,key)!=getattr(original,key):
            raise StudioError('DIRECTOR_SCOPE','Director changed pinned measured audio/text/profile/identity')
    if plan.scene_id!=scene['scene_id'] or plan.scene_version!=scene['version']:
        raise StudioError('DIRECTOR_SCOPE','Director plan differs from the accepted Scene')
    from .production_bridge import CAPABILITIES, _frame
    if plan.transitions or plan.transforms or set(plan.required_capabilities)-CAPABILITIES:
        raise StudioError('capability_gap','Director requested unsupported effects')
    catalog={a['id']:a for a in measured['assets']}
    for item in plan.shot_placements:
        asset=catalog.get(item.asset_id)
        if (not asset or asset['kind'] not in {'image','video'} or asset['rights'] not in {'owned','permission','public_domain'}
                or item.transforms or item.fade_in_ms or item.fade_out_ms or item.gain_db
                or asset['kind']=='image' and item.source_in_ms
                or asset['kind']=='video' and item.source_out_ms>asset['duration_ms']):
            raise StudioError('DIRECTOR_SCOPE','Director picture exceeds verified catalog, ranges or capabilities')
        duration=_frame(item.timeline_end_ms,plan.fps)-_frame(item.timeline_start_ms,plan.fps)
        if duration<=0 or duration!=_frame(item.source_out_ms,plan.fps)-_frame(item.source_in_ms,plan.fps):
            raise StudioError('SHOT_TIMING_INVALID','Director cut is incompatible with frame boundaries')
    return plan.model_dump()


class DirectorAdapter:
    is_local=False
    def __init__(self,pipeline,delegate):
        self.pipeline=pipeline
        if getattr(delegate,'is_simulated',False) is not True:
            raise StudioError('DIRECTOR_LIVE_UNVERIFIED','Only an injected offline director is admitted by this package')
        self.is_simulated=True
        self.delegate=ProviderBridge(delegate,provider='codex',opt_in=False,
            capability_check=lambda *_:{'structured_output':True,'cancellation':True},timeout=60)
        self.requests={}
        self.pending={};self.children={}

    def start(self,task,profile,attempt_dir=None):
        contract=Task.model_validate(task['context']['v2_contract'])
        if contract.role!='codex' or contract.expected_output_schema!='edit_plan':
            raise StudioError('DIRECTOR_SCOPE','Only the fixed Codex EditPlan role is supported')
        proposal(task['context'])
        if contract.capability=='system_director_fix':
            config=self.pipeline.permit.node(contract.task_id)[1]
            decision=fix_controller(task['context'],authorized=config.acquisition.auto_fix_authorized,
                cycles=config.brief.auto_fix_cycles)
            task=deepcopy(task);task['context']['targeted_fix']=decision
            if decision['action']=='reuse_accepted':
                handle='reuse_'+uuid.uuid4().hex;self.requests[handle]=task;return handle
        # Coordinator.start runs inside its writer transaction. Do not open a
        # nested Registry writer or send a provider turn until the next poll.
        handle=uuid.uuid4().hex
        self.requests[handle]=deepcopy(task)
        self.pending[handle]=(deepcopy(profile),attempt_dir)
        return handle

    def poll(self,handle):
        if handle.startswith('reuse_'):
            task=self.requests[handle]
            if task.get('_reused'):return []
            task['_reused']=True
            events=[{'type':'completed','result':task['context']['targeted_fix']['prior']}]
        else:
            if handle in self.pending:
                task=self.requests[handle];decision=task['context'].get('targeted_fix')
                if decision and decision['action']=='execute':
                    from .registry import Registry
                    contract=Task.model_validate(task['context']['v2_contract'])
                    binding={'task_id':contract.task_id,'issue_id':decision['issue_id'],'prior_sha256':decision['prior_sha256'],
                        'authority':task['context']['system_authority']}
                    with Registry(self.pipeline.q.root) as registry,registry.transaction():
                        previous=[json.loads(r[0]) for r in registry.db.execute(
                            "SELECT payload FROM ai_events WHERE kind='system_fix_authorization' AND task_id=?",(contract.run_id,))]
                        previous=[v for v in previous if v['authority']==binding['authority']]
                        if any(v!=binding for v in previous):
                            raise StudioError('REVISION_LIMIT','One accepted host-authorized fix cycle is already reserved')
                        if not previous:self.pipeline.q.queue._event(registry,'system_fix_authorization',binding,contract.run_id)
                profile,attempt_dir=self.pending.pop(handle)
                self.children[handle]=self.delegate.start(deepcopy(task),profile,attempt_dir)
            events=self.delegate.poll(self.children[handle])
        for event in events:
            if event.get('type')!='completed':continue
            task=self.requests[handle];contract=Task.model_validate(task['context']['v2_contract'])
            payload=validate_plan(event['result'],task['context'])
            decision=task['context'].get('targeted_fix')
            if decision and decision['action']=='execute':
                prior=next(v for v in task['context']['dependency_results'].values() if 'plan_id' in v)
                expected=deepcopy(prior)
                index=next(i for i,p in enumerate(prior['shot_placements']) if p['placement_id']==decision['location'])
                expected['shot_placements'][index]=payload['shot_placements'][index]
                expected['important_decisions']=payload['important_decisions']
                if payload!=expected:
                    raise StudioError('REVISION_SCOPE','Edit fix changed another placement or unrelated field')
            artifact=self.pipeline.q.store.put_json(contract.run_id,payload,simulated=True)
            result=Result(task_id=contract.task_id,run_id=contract.run_id,acceptance_key=task['attempt_id'],
                expected_output_schema='edit_plan',input_version=contract.input_version,input_hashes=contract.input_hashes,
                payload=payload,artifacts=[artifact],simulated=True).model_dump()
            write_generated_json(self.pipeline.root,f'host_receipts/{contract.task_id}/{task["attempt_id"]}.json',result)
            event.update(result=payload,host_metadata=result,simulated=True,transport='stub')
        return events

    def cancel(self,handle):
        self.pending.pop(handle,None)
        if handle in self.children:return self.delegate.cancel(self.children[handle])

    def recover(self,attempt_dir):
        task=json.loads((Path(attempt_dir)/'request.json').read_text(encoding='utf-8'))
        file=self.pipeline.root/'host_receipts'/task['task_id']/(task['attempt_id']+'.json')
        if file.exists():
            result=json.loads(file.read_text(encoding='utf-8'))
            return {'status':'completed','result':result['payload'],'host_metadata':result}
        # No replay of an unobserved remote request. Owner can inspect/retry the
        # existing attempt explicitly rather than silently sending another turn.
        return {'status':'failed','failure':{'kind':'interrupted','retryable':False,'message':'Director checkpoint missing; inspect the saved attempt'}}


class SystemDispatch:
    """Python host stages and Codex tasks share the existing adapter key only."""
    is_simulated=True
    is_local=False
    def __init__(self,host,director):self.host,self.director=host,director;self.handles={}
    def start(self,task,profile,attempt_dir=None):
        adapter=self.director if task['context']['v2_contract']['role']=='codex' else self.host
        if adapter is None:raise StudioError('DIRECTOR_BINDING_REQUIRED','Supply an explicit trusted director adapter')
        handle=uuid.uuid4().hex;self.handles[handle]=(adapter,adapter.start(task,profile,attempt_dir));return handle
    def poll(self,handle):
        adapter,child=self.handles[handle];return adapter.poll(child)
    def cancel(self,handle):
        adapter,child=self.handles[handle];return adapter.cancel(child)
    def recover(self,attempt_dir):
        task=json.loads((Path(attempt_dir)/'request.json').read_text(encoding='utf-8'))
        adapter=self.director if task['context']['v2_contract']['role']=='codex' else self.host
        if adapter is None:return {'status':'failed','failure':{'kind':'interrupted','retryable':False,'message':'Director binding missing'}}
        result=adapter.recover(attempt_dir)
        if result.get('status')=='running':
            handle=uuid.uuid4().hex;self.handles[handle]=(adapter,result['handle']);result['handle']=handle
        return result
