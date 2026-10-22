"""Small offline fixtures shared by the new scoped/recovery crash matrix."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import sys
import uuid

from app.editorial import Review
from app.registry import Registry,content_hash
from app.v2_acceptance import fingerprint
from app.v2_host_services import HostServices
from app.v2_ids import derived_id
from app.v2_narration import profile_spec
from app.v2_provider_bridge import SyntheticTransport
from app.v2_run_config import InputFile
from scripts.launch_multi_ai_v2 import main as launch
from tests.test_v2_acquisition import source_config,synthetic_reply
from tests.test_v2_closure_integration import NativeIO


class InjectedCrash(BaseException):pass


def config(folder,count=2):
    value=source_config(folder,count)
    value.acquisition.auto_fix_authorized=True
    image=Path(value.input_root)/'alternate.png';image.write_bytes((Path(value.input_root)/'image.png').read_bytes()+b'fixture alternate')
    value.inputs.media.append(InputFile(asset_id='alternate_image',path=image.name,sha256=content_hash(image),
        size=image.stat().st_size,version=1,kind='image',rights='owned'))
    voice=profile_spec()['voice'];value.runtime={'tts_python':sys.executable,
        'tts_model':str(Path(__file__).resolve().parents[1]/voice['model_ref']),
        'melt_path':sys.executable,'ffprobe_path':'no-native-probe','ffmpeg_path':'no-native-decode',
        'tts_engine':'piper','tts_normalization_scope':voice['normalization_scope'],
        'pronunciation_lexicon':profile_spec()['pronunciation_lexicon']}
    value.runtime.update({'tts_'+k:v for k,v in voice['settings'].items() if k!='speaker_id'})
    return value


def reply(binding,ctx):
    capability=binding.task.capability
    if capability=='system_scoped_review':
        scene=next(v for v in ctx['dependency_results'].values() if 'narration_segments' in v)
        request=ctx['scoped_review_request']
        payload=Review(project_id=scene['project_id'],review_id=derived_id('review',scene['scene_id'],request['kind']),
            subject_type='scene',subject_id=scene['scene_id'],subject_version=scene['version'],
            subject_hash=fingerprint(scene),simulated=True,issues=[dict(issue_id='scoped_issue',severity='warning',
                category='motivation' if request['kind']=='story' else 'visual_relevance',location=request['segment_id'],
                observation='Metadata-only text or mapping mismatch',effect_on_viewer='Needs precise metadata',
                suggested_change='Address only this segment using approved inputs',required_evidence='metadata_only')]).model_dump()
        return {'payload':payload}
    if capability=='system_scoped_story':
        payload=deepcopy(ctx['revision_request']['prior_scene']);payload['version']+=1
        next(p for p in payload['narration_segments'] if p['segment_id']==ctx['revision_request']['segment_id'])['text']+=' Исправлено.'
        return {'payload':payload}
    if capability=='system_scoped_visual':
        payload=deepcopy(ctx['mapping_revision']['prior_mapping']);payload['version']+=1
        next(p for p in payload['mappings'] if p['segment_id']==ctx['mapping_revision']['segment_id'])['shots'][0]['asset_id']='alternate_image'
        return {'payload':payload}
    result=synthetic_reply(binding,ctx)
    if binding.task.expected_output_schema=='scene':
        second=deepcopy(result['payload']['narration_segments'][0])
        second.update(segment_id=derived_id('second',binding.task.scene_id),text='Вторая независимая строка.')
        result['payload']['narration_segments'].append(second)
    if binding.task.expected_output_schema=='segment_visuals' and ctx.get('entity_version'):
        result['payload']['version']=ctx['entity_version']
    return result


def services(responder=reply,*,native=False,count=2):
    io=NativeIO();io.duration=60//(count*2);io.vary_text=True
    return HostServices(worker_transport=SyntheticTransport(responder=responder),handler_io=io if native else None),io


def call(case,config,path,services,*args):
    path.write_text(config.model_dump_json(),encoding='utf-8')
    with redirect_stdout(io.StringIO()) as stream:
        code=launch(['--config',str(path),*args],host_services=services)
    value=json.loads(stream.getvalue());case.assertEqual(code,0,value)
    return value


def snapshot(root):
    root=Path(root)
    with Registry(root/'coordinator') as db:
        tasks=[dict(r) for r in db.db.execute('SELECT id,status,current_attempt,result,payload FROM ai_tasks ORDER BY id')]
        attempts=[dict(r) for r in db.db.execute('SELECT * FROM ai_attempts ORDER BY id')]
        events=[dict(r) for r in db.db.execute("SELECT kind,task_id,payload FROM ai_events WHERE kind IN ('system_expansion','system_scoped_fix_intent') ORDER BY sequence")]
    artifacts={};completions={};stage_files={}
    for row in tasks:
        result=json.loads(row['result']) if row['result'] else {}
        for artifact in result.get('v2_artifacts',[]):
            path=root/'coordinator'/artifact['path']
            artifacts[artifact['path']]={'expected_sha256':artifact['sha256'],'expected_bytes':artifact['size'],
                'sha256':content_hash(path) if path.is_file() else None,
                'bytes':path.stat().st_size if path.is_file() else None}
        if result.get('path'):
            path=root/'coordinator'/result['path']
            completions[result['path']]=content_hash(path) if path.is_file() else None
        payload=result.get('payload',{})
        if 'text' in payload:
            try:output=json.loads(payload['text'])
            except (ValueError,TypeError):output={}
            for relative,meta in output.get('files',{}).items():
                path=root/relative
                stage_files[relative]={'expected':meta,'sha256':content_hash(path) if path.is_file() else None,
                    'bytes':path.stat().st_size if path.is_file() else None}
    package_files={p.relative_to(root).as_posix():content_hash(p) for p in (root/'packages').rglob('*') if p.is_file()}
    jobs=[]
    if (root/'engine/registry.sqlite').is_file():
        with Registry(root/'engine') as db:
            if db.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone():
                jobs=[dict(r) for r in db.db.execute('SELECT id,operation,state,attempts,input_revision,parameters,result FROM jobs ORDER BY id')]
    return {'tasks':{r['id']:{'status':r['status'],'attempt':r['current_attempt'],
        'input_sha256':fingerprint(json.loads(r['payload'])),
        'result_sha256':fingerprint(json.loads(r['result'])) if r['result'] else None} for r in tasks},
        'attempts':attempts,'events':events,'jobs':jobs,'accepted_artifacts':artifacts,'completion_files':completions,
        'stage_files':stage_files,'package_files':package_files,
        'counts':{'tasks':len(tasks),'attempts':len(attempts),'jobs':len(jobs),'accepted_artifacts':len(artifacts)}}


def save(name,value):
    target=os.environ.get('SG_REVIEW_EVIDENCE')
    if target:
        path=Path(target)/(name+'_'+uuid.uuid4().hex+'.json')
        with path.open('x',encoding='utf-8') as file:json.dump(value,file,ensure_ascii=False,indent=2)
