"""One host-addressed text or mapping revision through the existing queue/gate."""
from copy import deepcopy
import json

from .editorial import validate_review
from .registry import Registry,StudioError
from .v2_acceptance import fingerprint
from .v2_acquisition import unwrapped
from .v2_ids import derived_id
from .v2_sources import source_manifest
from .v2_run_config import source_inventory


def execution_policy(config):
    value=config.acquisition.model_dump()
    value.pop('scoped_fix',None)
    value.pop('auto_fix_authorized',None)  # Permission is pinned on the fix nodes/host receipt.
    return value


def same_source_lineage(left,right):
    from .v2_run_config import BriefInputs
    return (isinstance(left.inputs,BriefInputs) and isinstance(right.inputs,BriefInputs)
        and source_lineage(left)==source_lineage(right))


def source_lineage(config):
    value=source_manifest(source_inventory(config))
    value.pop('version',None)  # A counter bump alone cannot buy another fix cycle.
    return value


def context(config,node,original):
    """Inputs must be freshly rebuilt by accepted_dependency; no caller payloads."""
    result=deepcopy(original)
    if node['stage'] not in {'scoped_review','scoped_story','scoped_visual'}:
        if node['version']!=config.inputs.version:result['entity_version']=node['version']
        return result
    permit=config.acquisition.scoped_fix
    if permit is None or node['scene_id']!=permit.scene_id:
        raise StudioError('REVISION_SCOPE','No host permission for this Scene')
    values=[unwrapped(v) for v in result['dependency_results'].values()]
    scene=next(v for v in values if 'narration_segments' in v)
    mapping=next(v for v in values if 'mappings' in v and 'scene_sha256' in v)
    if (mapping['scene_sha256']!=fingerprint(scene) or scene['scene_id']!=permit.scene_id
            or permit.segment_id not in {s['segment_id'] for s in scene['narration_segments']}):
        raise StudioError('REVISION_BINDING','Prior Scene/mapping/segment binding differs')
    result.update(project_id=scene['project_id'],scene_id=scene['scene_id'],scene_version=scene['version'],
                  subject_type='scene',subject_id=scene['scene_id'],subject_version=scene['version'],
                  subject_hash=fingerprint(scene),provided_evidence_mode='metadata_only')
    result['scoped_review_request']=permit.model_dump()
    if node['stage']=='scoped_review':return result
    review=next(v for v in values if 'review_id' in v)
    validate_review(review,result)
    issues=[i for i in review['issues'] if i['status']=='open']
    category={'story':{'story_causality','motivation'},'visual':{'visual_relevance'}}[permit.kind]
    if (not config.acquisition.auto_fix_authorized or config.brief.auto_fix_cycles!=1 or len(issues)!=1
            or issues[0]['location']!=permit.segment_id or issues[0]['category'] not in category
            or issues[0]['required_evidence']!='metadata_only' or review['evidence_mode']!='metadata_only'):
        raise StudioError('NEEDS_REVIEW','One open addressed issue with sufficient evidence and explicit host permission is required')
    result['entity_version']=node['version']
    result['scoped_revision']={'kind':permit.kind,'issue_id':issues[0]['issue_id'],'segment_id':permit.segment_id,
        'prior_scene_sha256':fingerprint(scene),'prior_mapping_sha256':fingerprint(mapping),
        'review_sha256':fingerprint(review),'max_cycles':1}
    if permit.kind=='story':
        result['revision_request']={'issue_id':issues[0]['issue_id'],'segment_id':permit.segment_id,
            'instruction':issues[0]['suggested_change'],'prior_scene':scene,'max_revision_cycles':1}
        result['revision_dependencies']={k:v for k,v in result['dependency_results'].items()
                                         if 'narration_segments' in unwrapped(v)}
        result['dependency_results']={k:v for k,v in result['dependency_results'].items()
                                      if k not in result['revision_dependencies']}
        result['scene_version']=scene['version']+1
    else:
        result['mapping_revision']={'prior_mapping':mapping,'segment_id':permit.segment_id,'max_revision_cycles':1}
        result['scene_version']=scene['version']
    return result


def validate_delta(payload,ctx):
    revision=ctx.get('scoped_revision')
    if not revision:return payload
    target=revision['segment_id']
    if revision['kind']=='story':
        expected=deepcopy(ctx['revision_request']['prior_scene']);expected['version']+=1
        parts=[p for p in payload['narration_segments'] if p['segment_id']==target]
        if len(parts)!=1:raise StudioError('REVISION_SCOPE','One existing narration segment is required')
        next(p for p in expected['narration_segments'] if p['segment_id']==target)['text']=parts[0]['text']
    else:
        expected=deepcopy(ctx['mapping_revision']['prior_mapping']);expected['version']+=1
        parts=[p for p in payload['mappings'] if p['segment_id']==target]
        if len(parts)!=1:raise StudioError('REVISION_SCOPE','One existing mapping is required')
        index=next(i for i,p in enumerate(expected['mappings']) if p['segment_id']==target)
        expected['mappings'][index]=parts[0]
    if payload!=expected:raise StudioError('REVISION_SCOPE','Result changed unrelated identity, fields, segments or authority')
    if revision['kind']=='story':
        prior=next(p for p in ctx['revision_request']['prior_scene']['narration_segments'] if p['segment_id']==target)
        changed=next(p for p in payload['narration_segments'] if p['segment_id']==target)
    else:
        prior=next(p for p in ctx['mapping_revision']['prior_mapping']['mappings'] if p['segment_id']==target)
        changed=next(p for p in payload['mappings'] if p['segment_id']==target)
    if prior==changed:raise StudioError('REVISION_SCOPE','Open issue requires an actual addressed change')
    return payload


def reserve(pipeline,task,binding):
    node_config=pipeline.permit.node(task['task_id']);config=node_config[1];node=node_config[2]
    if node['stage'] not in {'scoped_story','scoped_visual'}:return
    with Registry(pipeline.q.root) as db:
        ctx=pipeline.q.result_gate.dependency_context(db,task)
    lineage=derived_id(config.run_id,'scoped_fix','h'+fingerprint(source_lineage(config)))
    value={'task_id':task['task_id'],'attempt_id':task['attempt_id'],'authority':node_config[0],'scope':ctx['scoped_revision'],
        'worker_slot':binding.slot.model_dump(),'input_hashes':binding.task.input_hashes}
    with Registry(pipeline.q.root) as db,db.transaction():
        rows=[json.loads(r[0]) for r in db.db.execute(
            "SELECT payload FROM ai_events WHERE kind='system_scoped_fix_intent' AND task_id=?",(lineage,))]
        if rows and any(v!=value for v in rows):
            raise StudioError('REVISION_LIMIT','The one permitted lineage cycle was already assigned')
        if not rows:pipeline.q.queue._event(db,'system_scoped_fix_intent',value,lineage)
