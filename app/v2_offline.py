"""One-brief offline integration OVER RunCoordinator; no new queue or live clients."""
import json
from pathlib import Path
import struct
import zlib

from pydantic import Field, field_validator

from .contracts import relative_path
from .registry import Registry, StudioError, content_hash, workspace_path, canonical_json
from .v2_acceptance import fingerprint
from .v2_contracts import Artifact, Contract
from .v2_sources import CanonBible, source_manifest, verify_sources
from .v2_visuals import SegmentVisuals
from .v2_production import OfflineProductionBridge
from .v2_offline_qa import technical_qa, scene_graph
from .v2_release import prepare_release, verify_release, delivery_plan

OPERATION_KEY='MULTI-AI-V2-BULK-OFFLINE-01'


class OfflineInputs(Contract):
    engine_relative: str
    source_root_relative: str
    canon: CanonBible
    source_manifest_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    visual_mapping: list[SegmentVisuals] = Field(min_length=1)

    @field_validator('engine_relative','source_root_relative')
    @classmethod
    def relative(cls,value):
        relative_path(value)
        return value


def _read(coordinator, artifact):
    return json.loads(coordinator.store.verify(Artifact.model_validate(artifact)).read_text(encoding='utf-8'))


def _events(coordinator, kind, run_id):
    with Registry(coordinator.root) as registry:
        return [json.loads(row[0]) for row in registry.db.execute(
            'SELECT payload FROM ai_events WHERE kind=? AND task_id=? ORDER BY sequence',(kind,run_id))]


def _record(coordinator, kind, run_id, payload):
    with Registry(coordinator.root) as registry,registry.transaction():
        coordinator.queue._event(registry,kind,payload,run_id)


def _write_fixture(root,relative,data):
    path=workspace_path(root,relative,for_write=True)
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if path.read_bytes()!=data:raise StudioError('OUTPUT_EXISTS','Preserve existing/manual fixture file')
    else:
        with path.open('xb') as stream:stream.write(data)
    return path


def _demo_inputs(coordinator,run_id,scene):
    """Tiny original PNG/text, not a film clip, WAV or generated speech."""
    from .contracts import ProjectBrief
    engine_relative=f'runs/{run_id}/engine'
    engine=workspace_path(coordinator.root,engine_relative,for_write=True)
    def chunk(name,data):return struct.pack('!I',len(data))+name+data+struct.pack('!I',zlib.crc32(name+data)&0xffffffff)
    png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',16,16,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\x00'+b'\x30\x70\x90'*16)*16))+chunk(b'IEND',b'')
    _write_fixture(engine,'fixtures/original.png',png)
    with Registry(engine) as registry:
        registry.initialize(ProjectBrief(project_id=run_id,title='Original offline fixture',work_title='Original fixture',character='Traveler'))
        registry.register_asset(run_id,'fixture_image','fixtures/original.png',kind='image',rights='owned',
                                base_revision=1,idempotency_key='bulk_fixture_image')
    source_relative=f'runs/{run_id}/sources'
    source=_write_fixture(coordinator.root,source_relative+'/fixture.txt',
                          b'Original synthetic fixture: a traveler asks a guide for a safe route.\n')
    bible=CanonBible.model_validate({'version':1,'work_id':'original_fixture','sources':[
        dict(source_id='fixture',edition='v1',path='fixture.txt',sha256=content_hash(source),size=source.stat().st_size,rights='fixture')],
        'facts':[dict(fact_id='route',edition='v1',text='The fixture names a traveler and a guide.',
            status='owner_verified',citations=[dict(source_id='fixture',edition='v1',start_byte=0,end_byte=65)])]})
    mappings=[dict(scene_id=scene.scene_id,scene_version=scene.version,segment_id=s.segment_id,duration_ms=3000,
        shots=[dict(shot_id='shot_'+s.segment_id,asset_id='fixture_image',source_out_ms=3000,
            emotional_task='Synthetic metadata mapping, not an artistic choice.',
            reuse_reason='Single original fixture reused only to exercise integration.')]) for s in scene.narration_segments]
    return OfflineInputs(engine_relative=engine_relative,source_root_relative=source_relative,canon=bible,
        source_manifest_sha256=fingerprint(source_manifest(bible)),visual_mapping=mappings)


def _bridge(coordinator,run_id,spec):
    spec=OfflineInputs.model_validate(spec)
    base=workspace_path(coordinator.root,f'runs/{run_id}')
    engine=workspace_path(coordinator.root,spec.engine_relative)
    sources=workspace_path(coordinator.root,spec.source_root_relative)
    if not engine.is_relative_to(base) or not sources.is_relative_to(base) or engine==base:
        raise StudioError('PATH_UNSAFE','Offline inputs must stay under their owning dedicated run')
    verify_sources(sources,spec.canon,spec.source_manifest_sha256)
    return OfflineProductionBridge(coordinator,engine,source_bundle={'root':sources,
        'canon':spec.canon.model_dump(),'manifest_sha256':spec.source_manifest_sha256}),spec


def prepare_offline(coordinator,brief,key,*,inputs=None,fixture_demo=False):
    with coordinator.queue._lock:
        state=coordinator.create(brief,key)
        if state['mode']!='fake':return state
        if inputs is not None and fixture_demo:raise ValueError('Choose explicit inputs or fixture demo')
        if inputs is not None:inputs=OfflineInputs.model_validate(inputs).model_dump()
        intent={'fixture_demo':fixture_demo,'inputs':inputs}
        events=_events(coordinator,'offline_prepare_requested',state['run_id'])
        if events:
            if _read(coordinator,events[0]['artifact'])!=intent:
                raise StudioError('IDEMPOTENCY_CONFLICT','Run already binds different offline inputs')
        else:
            artifact=coordinator.store.put_json(state['run_id'],intent,simulated=True)
            _record(coordinator,'offline_prepare_requested',state['run_id'],{'artifact':artifact.model_dump()})
        return resume_offline(coordinator,state['run_id'])


def resume_offline(coordinator,run_id):
    with coordinator.queue._lock:
        state=coordinator.status(run_id)
        if state['mode']!='fake':return state
        intent_events=_events(coordinator,'offline_prepare_requested',run_id)
        if not intent_events:
            raise StudioError('OFFLINE_INPUTS_REQUIRED','Use prepare with explicit inputs or --fixture-demo')
        intent=_read(coordinator,intent_events[0]['artifact'])
        state=coordinator.advance(run_id,60)
        if state['status']!='SIMULATION_SUCCEEDED':return state
        with Registry(coordinator.root) as registry:
            row=registry.db.execute('SELECT * FROM ai_scenes WHERE project_id=? AND scene_id=? ORDER BY version DESC LIMIT 1',
                                    (run_id,'scene_001')).fetchone()
        if not row or not row['source_task_id']:
            raise StudioError('SCENE_REACCEPTANCE_REQUIRED','Manual Scene needs trusted acceptance')
        task_id=row['source_task_id']
        from .editorial import Scene
        scene=Scene.model_validate_json(row['payload'])
        specs=_events(coordinator,'offline_inputs_prepared',run_id)
        if specs:spec=OfflineInputs.model_validate(_read(coordinator,specs[0]['artifact']))
        else:
            if intent['fixture_demo']:spec=_demo_inputs(coordinator,run_id,scene)
            elif intent['inputs']:spec=OfflineInputs.model_validate(intent['inputs'])
            else:raise StudioError('VISUAL_REQUIRED','Explicit approved visual mapping is required; no random asset fallback')
            artifact=coordinator.store.put_json(run_id,spec.model_dump(),simulated=True)
            _record(coordinator,'offline_inputs_prepared',run_id,{'artifact':artifact.model_dump()})
        bridge,spec=_bridge(coordinator,run_id,spec)
        mappings=[m.model_dump() for m in spec.visual_mapping]
        if intent['fixture_demo']:
            for m in mappings:m['scene_version']=scene.version
        candidate=bridge.candidate(run_id,task_id,scene.version,mappings)
        mlt=bridge.prepare_mlt(candidate)
        qa=technical_qa(bridge,mlt)
        if qa['status']!='VERIFIED':raise StudioError('TECHNICAL_QA_BLOCKED','Prepared file QA failed')
        additions=[]
        for value,category in ((spec.canon.model_dump(),'canon'),(source_manifest(spec.canon),'manifests'),(qa,'reviews')):
            artifact=coordinator.store.put_json(run_id,value,simulated=True)
            additions.append(coordinator.store.put_file(run_id,coordinator.store.verify(artifact),category=category,
                version=scene.version,simulated=True).model_dump())
        for source in spec.canon.sources:
            path=workspace_path(coordinator.root,spec.source_root_relative+'/'+source.path)
            additions.append(coordinator.store.put_file(run_id,path,category='sources',version=scene.version,simulated=True).model_dump())
        release=prepare_release(bridge,mlt,additional_artifacts=additions)
        summary={'operation_key':OPERATION_KEY,'run_id':run_id,'status':'SIMULATION_SUCCEEDED','offline_status':'VERIFIED',
            'scope':'offline_fixture_integration_only','simulated':True,'ready_for_user':False,
            'candidate':candidate,'mlt':mlt,'release':release,'technical_qa':qa,
            'scene_graph':scene_graph(bridge,[mlt]),'delivery':delivery_plan(coordinator,release),
            'blockers':['BLOCKED_LOCAL_RUNTIME: real Piper execution not validated',
                        'Antigravity AGE-GATED / DISABLED','Real render and full-video creative QA NOT_RUN',
                        'Production script registration/submission requires separate approval',
                        'Runner registration and remote Drive readback remain BLOCKED/UNVERIFIED'],
            'model_requests':0,'paid_api_requests':0,'production_started':False,'remote_sync':'UNVERIFIED','uploaded':False}
        summary=json.loads(canonical_json(summary))
        artifact=coordinator.store.put_json(run_id,summary,simulated=True)
        prior=_events(coordinator,'offline_pipeline_prepared',run_id)
        if not prior or prior[-1]['artifact']!=artifact.model_dump():
            _record(coordinator,'offline_pipeline_prepared',run_id,{'artifact':artifact.model_dump()})
        return {**summary,'manifest_artifact':artifact.model_dump()}


def inspect_offline(coordinator,run_id):
    events=_events(coordinator,'offline_pipeline_prepared',run_id)
    if not events:return coordinator.status(run_id)
    value=_read(coordinator,events[-1]['artifact'])
    specs=_events(coordinator,'offline_inputs_prepared',run_id)
    bridge,_=_bridge(coordinator,run_id,_read(coordinator,specs[0]['artifact']))
    bridge.read_mlt(value['mlt']);verify_release(coordinator,value['release'])
    return {**value,'manifest_artifact':events[-1]['artifact']}
