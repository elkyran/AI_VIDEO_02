"""Offline structural QA and scene-scoped invalidation, never artistic acceptance."""
from pathlib import Path
import xml.etree.ElementTree as ET

from .editorial import Review
from .operations import subtitle_timing_issues
from .registry import StudioError
from .v2_acceptance import fingerprint
from .production_bridge import _ReadOnlyRegistry


def technical_qa(bridge, artifact):
    data=bridge.read_mlt(artifact)
    plan=data['edit_plan'];compiled=data['compiled']
    tree=ET.parse(compiled['project_mlt']);profile=tree.find('profile')
    checks=[]
    def record(name,passed,evidence):
        checks.append({'name':name,'status':'VERIFIED' if passed else 'BLOCKED','evidence':evidence})
    record('file_hashes',True,'Existing bridge receipt and every staged file rechecked')
    record('mlt_profile',all(profile.get(key)==str(plan[key]) for key in ('width','height'))
        and profile.get('frame_rate_num')==str(plan['fps']), 'Declared MLT profile; not decoded MP4 measurements')
    ordered=sorted(plan['shot_placements'],key=lambda p:p['timeline_start_ms'])
    record('picture_coverage',ordered[0]['timeline_start_ms']==0 and ordered[-1]['timeline_end_ms']==plan['duration_ms']
        and all(a['timeline_end_ms']==b['timeline_start_ms'] for a,b in zip(ordered,ordered[1:])), 'No unplanned picture gaps')
    project=Path(compiled['project_mlt'])
    missing=[]
    for prop in tree.findall(".//property[@name='resource']"):
        if prop.text!='0' and not (project.parent/prop.text).is_file():missing.append(prop.text)
    record('mlt_references',not missing,missing)
    srt=project.with_name('subtitles.srt')
    subtitle_issues=subtitle_timing_issues(srt,plan['duration_ms']/1000,
        [(s['start_ms']/1000,s['end_ms']/1000) for s in plan['subtitle_events']]) if srt.is_file() else ['Missing subtitles']
    record('subtitle_bounds',not subtitle_issues,subtitle_issues)
    return {'status':'VERIFIED' if all(c['status']=='VERIFIED' for c in checks) else 'BLOCKED',
            'scope':'prepared_files_and_declared_timeline_only','checks':checks,
            'decoded_video_duration':None,'measured_resolution':None,'measured_fps':None,
            'audio_silence':None,'av_sync':None,'audio_status':data['audio_status'],
            'full_video_creative_qa':'NOT_RUN','simulated':data['simulated'],'production_started':False}


def route_review(value, *, project_id, subject_id, subject_version, subject_hash):
    from .editorial import validate_review
    review=Review.model_validate(value)
    validate_review(review,{'project_id':project_id,'subject_id':subject_id,'subject_version':subject_version,
                            'subject_hash':subject_hash,'provided_evidence_mode':'metadata_only'})
    technical={'technical','sound','subtitles'}
    return {'technical':[i.model_dump() for i in review.issues if i.category in technical],
            'editorial':[i.model_dump() for i in review.issues if i.category not in technical],
            'creative_qa':'NOT_RUN','source':'canonical_Review','simulated':review.simulated}


def scene_graph(bridge, artifacts):
    nodes={}
    for artifact in artifacts:
        data=bridge.read_mlt(artifact);scene=data['edit_plan']['scene_id']
        prefix=artifact['run_id']+':'+scene
        scene_key=prefix+':scene';edit_key=prefix+':edit';mlt_key=prefix+':mlt'
        if scene_key in nodes:raise StudioError('REVISION_CONFLICT','Only one current version per scene in a graph')
        nodes[scene_key]={'sha256':data['candidate']['sha256'],'depends_on':[],'scene_id':scene}
        voices=[]
        for index,segment in enumerate(data['narration']['segments']):
            key=prefix+':voice:'+segment['block_id'];voices.append(key)
            wav_hash=None
            if data['voice_assets']:
                segment_id=data['edit_plan']['voice_placements'][index]['placement_id'].removeprefix('audio_')
                with _ReadOnlyRegistry(bridge.engine_root) as registry:
                    wav_hash=registry.asset(artifact['run_id'],data['voice_assets'][segment_id]['asset_id'],verify=True)['sha256']
            nodes[key]={'sha256':segment['cache_key'],'depends_on':[],'scene_id':scene,
                        'wav_sha256':wav_hash or (segment['cache_evidence'] or {}).get('sha256')}
        nodes[edit_key]={'sha256':fingerprint(data['edit_plan']),'depends_on':[scene_key,*voices],'scene_id':scene}
        nodes[mlt_key]={'sha256':artifact['sha256'],'depends_on':[edit_key],'scene_id':scene}
    nodes['final_assembly']={'sha256':None,'depends_on':[k for k in nodes if k.endswith(':mlt')],'scene_id':None}
    return {'nodes':nodes,'simulated':any(a['simulated'] for a in artifacts),'production_started':False}


def invalidation_plan(before, after):
    old,new=before['nodes'],after['nodes']
    changed={k for k in old.keys()|new.keys() if old.get(k)!=new.get(k)}
    while True:
        more={k for k,v in new.items() if set(v['depends_on'])&changed}
        if more<=changed:break
        changed|=more
    return {'invalidate':sorted(changed),'retain':sorted((old.keys()&new.keys())-changed),
            'action':'plan_only_no_deletion','production_started':False}
