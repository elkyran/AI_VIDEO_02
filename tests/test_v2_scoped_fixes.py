from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.v2_acceptance import fingerprint
from app.v2_acquisition_contracts import ScopedFixPermit
from app.v2_pipeline import SystemPipeline
from app.v2_narration import narration_descriptors
from app.registry import Registry,StudioError,content_hash
from tests.closure_review_support import config,services,call,snapshot,save,InjectedCrash


class ScopedFixTests(unittest.TestCase):
    def exercise(self,kind):
        with tempfile.TemporaryDirectory(prefix='scoped_'+kind+'_') as temp:
            cfg=config(temp);host,native=services(native=True);path=Path(temp)/'host.json'
            cfg.acquisition.auto_fix_authorized=False
            first=call(self,cfg,path,host,'--execute-system','--authorize','start')
            self.assertEqual(first['status'],'SIMULATION_SUCCEEDED',first)
            # Reuse the actual CLI-created handler instance for read-only gate
            # inspection after its lock is released. Avoid two extra startups;
            # all execution/restart/replay still goes through ordinary launch.
            p=native.last_pipeline
            prior_nodes=deepcopy(p.nodes)
            originals={n['scene_id']:p.accepted(n['task_id']) for n in p.nodes if n['stage']=='acquire_scene'}
            old_audio={n['scene_id']:(n['task_id'],p.accepted(n['task_id'])) for n in p.nodes if n['stage']=='wav'}
            old_candidates={n['scene_id']:p.bridge.read_candidate(p.accepted(n['task_id'])['candidate'])
                for n in p.nodes if n['stage']=='speech_candidate'}
            before=snapshot(cfg.root);p.close()
            sid=next(s for s in originals if s.endswith('n1'));other=next(s for s in originals if s!=sid)
            target=originals[sid]['narration_segments'][0]['segment_id']
            cfg.parent_sha256=fingerprint(cfg.pinned());cfg.version=2
            cfg.acquisition.auto_fix_authorized=True
            cfg.acquisition.scoped_fix=ScopedFixPermit(kind=kind,scene_id=sid,segment_id=target)
            requests_before=len([c for c in native.commands if len(c)>1 and c[1].endswith('synthesize_script.py')])
            from app.multi_ai import Coordinator
            original=Coordinator.enqueue_batch;crash=[]
            def committed(queue,specs,**kwargs):
                result=original(queue,specs,**kwargs)
                if kwargs['receipt']['authority']==fingerprint(cfg.pinned()) and not crash:
                    crash.append(snapshot(cfg.root));raise InjectedCrash('revision expansion committed before flags')
                return result
            with patch.object(Coordinator,'enqueue_batch',new=committed):
                with self.assertRaises(InjectedCrash):call(self,cfg,path,host,'--execute-system','--authorize','start')
            self.assertEqual(len(crash),1)
            second=call(self,cfg,path,host,'--execute-system','resume')
            self.assertEqual(second['status'],'SIMULATION_SUCCEEDED',second)
            p=native.last_pipeline
            node=next(n for n in p.nodes if n['stage']=='scoped_'+kind)
            fixed=p.accepted(node['task_id'])
            self.assertEqual(fixed['version'],2)
            self.check_negatives(p,cfg,node,fixed,kind)
            current_audio={n['scene_id']:(n['task_id'],p.accepted(n['task_id'])) for n in p.nodes if n['stage']=='wav'}
            candidates={n['scene_id']:p.bridge.read_candidate(p.accepted(n['task_id'])['candidate'])
                for n in p.nodes if n['stage']=='speech_candidate'}
            self.assertEqual(old_audio[other],current_audio[other])
            for record in old_audio[other][1]['audio']['segments']:
                self.assertEqual(content_hash(p.engine/record['path']),record['wav_sha256'])
            self.assertEqual(p.config.inputs.scenes[1].model_dump(),originals[other])
            old_recipe=narration_descriptors(old_candidates[sid],p.engine)
            new_recipe=narration_descriptors(candidates[sid],p.engine)
            if kind=='story':
                self.assertNotEqual(old_audio[sid][0],current_audio[sid][0])
                self.assertNotEqual(old_recipe['segments'][0]['cache_key'],new_recipe['segments'][0]['cache_key'])
                self.assertEqual(old_recipe['segments'][1]['cache_key'],new_recipe['segments'][1]['cache_key'])
                self.assertEqual(fixed['narration_segments'][1],originals[sid]['narration_segments'][1])
                old_segments=old_audio[sid][1]['audio']['segments'];new_segments=current_audio[sid][1]['audio']['segments']
                self.assertNotEqual(old_segments[0]['wav_sha256'],new_segments[0]['wav_sha256'])
                self.assertEqual(old_segments[1]['wav_sha256'],new_segments[1]['wav_sha256'])
                self.assertEqual(content_hash(p.engine/new_segments[1]['path']),old_segments[1]['wav_sha256'])
            else:
                self.assertEqual(old_audio,current_audio)
                self.assertEqual(old_recipe,new_recipe)
                self.assertEqual(requests_before,len([c for c in native.commands if len(c)>1 and c[1].endswith('synthesize_script.py')]))
                self.assertEqual(p.config.inputs.scenes[0].model_dump(),originals[sid])
                self.assertEqual(fixed['mappings'][0]['shots'][0]['asset_id'],'alternate_image')
            after=snapshot(cfg.root);p.close();calls=len(host.worker_transport.calls);commands=len(native.commands)
            for stage in ('candidate','edit'):
                self.assertNotEqual(next(n['task_id'] for n in prior_nodes if n['stage']==stage and n.get('scene_id')==sid),
                    next(n['task_id'] for n in p.nodes if n['stage']==stage and n.get('scene_id')==sid))
            for name,meta in before['accepted_artifacts'].items():self.assertEqual(meta,after['accepted_artifacts'][name])
            final=call(self,cfg,path,host,'--execute-system','resume')
            self.assertEqual(final['results'],second['results']);self.assertEqual(calls,len(host.worker_transport.calls))
            self.assertEqual(commands,len(native.commands));self.assertEqual(after,snapshot(cfg.root))
            dropped=cfg.model_copy(deep=True);dropped.parent_sha256=fingerprint(cfg.pinned());dropped.version=3
            dropped.acquisition.scoped_fix=None
            with self.assertRaises(StudioError) as lost:
                SystemPipeline(dropped,opt_in=True,authorize=True,host_services=host)
            self.assertEqual(lost.exception.code,'REVISION_LINEAGE')
            dropped.inputs.version+=1
            with self.assertRaises(StudioError) as lost:
                SystemPipeline(dropped,opt_in=True,authorize=True,host_services=host)
            self.assertEqual(lost.exception.code,'REVISION_LINEAGE')
            self.assertEqual(after,snapshot(cfg.root))
            with Registry(Path(cfg.root)/'coordinator') as db:
                self.assertEqual(db.db.execute("SELECT count(*) FROM ai_events WHERE kind='system_scoped_fix_intent'").fetchone()[0],1)
            fixed_calls=[c for c in host.worker_transport.calls if c['binding']['task']['capability']=='system_scoped_'+kind]
            self.assertEqual(len(fixed_calls),1)
            self.assertEqual(fixed_calls[0]['binding']['slot']['role'],'gemini_story' if kind=='story' else 'gemini_visual')
            for task,row in before['tasks'].items():
                if row['status']=='completed':self.assertEqual(row,after['tasks'][task])
            save('scoped_'+kind,{'before':before,'after':after,'replay':snapshot(cfg.root),
                'crash_after_revision_expansion':crash[0],
                'fixed_task':node['task_id'],'fixed_sha256':fingerprint(fixed),'prior_scene_sha256':fingerprint(originals[sid]),
                'old_audio_ids':{s:a[0] for s,a in old_audio.items()},'new_audio_ids':{s:a[0] for s,a in current_audio.items()},
                'worker_turns':len(fixed_calls),'native_tools_started':0})

    def check_negatives(self,p,cfg,node,fixed,kind):
        from app.editorial import validate_result
        from app.v2_scoped_fixes import context as scoped_context
        from app.v2_provider_protocol import SessionBinding,synthetic_stdout,parse_output,ProviderLimits
        with Registry(p.q.root) as db:
            row=db.db.execute('SELECT * FROM ai_tasks WHERE id=?',(node['task_id'],)).fetchone()
            task=p.q.queue._task(row)
            ctx={**task['context'],**p.q.result_gate.dependency_context(db,task)}
        self.assertEqual(validate_result(node['schema'],fixed,ctx),fixed)
        raw={'dependency_results':{d:p.accepted(d) for d in node['dependencies']}}
        review=next(v for v in raw['dependency_results'].values() if 'review_id' in v)
        wrong=deepcopy(raw);next(v for v in wrong['dependency_results'].values() if 'review_id' in v)['subject_hash']='f'*64
        with self.assertRaises(StudioError):scoped_context(cfg,node,wrong)
        wrong=deepcopy(raw);next(v for v in wrong['dependency_results'].values() if 'review_id' in v)['issues'][0]['required_evidence']='video_audio'
        with self.assertRaises(StudioError):scoped_context(cfg,node,wrong)
        bad=[]
        v=deepcopy(fixed);v['scene_id']='foreign_scene';bad.append(v)
        v=deepcopy(fixed);v['version']=1;bad.append(v)
        v=deepcopy(fixed);v['new_media_authority']=['outside'];bad.append(v)
        if kind=='story':
            v=deepcopy(fixed);v['narration_segments'][1]['text']='unrequested';bad.append(v)
            v=deepcopy(fixed);v['fact_refs']=['foreign_edition:unknown'];bad.append(v)
        else:
            v=deepcopy(fixed);v['mappings'][0]['shots'][0]['asset_id']='not_approved';bad.append(v)
            v=deepcopy(fixed);v['mappings'][1]['duration_ms']+=1;bad.append(v)
        for payload in bad:
            with self.subTest(kind=kind,payload_hash=fingerprint(payload)):
                with self.assertRaises((ValueError,StudioError)):validate_result(node['schema'],payload,ctx)
        wrong=deepcopy(ctx);wrong['acquisition_scope']['role']='gemini_visual' if kind=='story' else 'gemini_story'
        with self.assertRaises(StudioError):validate_result(node['schema'],fixed,wrong)
        turn=next(c for c in p.services.worker_transport.calls if c['binding']['task']['task_id']==node['task_id'])
        binding=SessionBinding.model_validate(turn['binding']);creative=turn['context']
        self.assertEqual(parse_output(synthetic_stdout(binding,fixed,'json'),binding=binding,output_format='json',
            context=creative,limits=ProviderLimits(),elapsed=0),fixed)
        stale=binding.model_copy(update={'slot':binding.slot.model_copy(update={'auth_generation':binding.slot.auth_generation+1})})
        with self.assertRaises((ValueError,StudioError)):
            parse_output(synthetic_stdout(stale,fixed,'json'),binding=binding,output_format='json',context=creative,
                limits=ProviderLimits(),elapsed=0)
        with self.assertRaises((ValueError,StudioError)):
            parse_output(b'{broken',binding=binding,output_format='json',context=creative,limits=ProviderLimits(),elapsed=0)
        with self.assertRaises(ValueError):ScopedFixPermit.model_validate({**cfg.acquisition.scoped_fix.model_dump(),'max_cycles':2})
        from app.v2_scoped_fixes import reserve
        # Budget belongs to the durable lineage, not a fresh random attempt ID.
        second={**task,'attempt_id':'different_attempt'}
        with self.assertRaises(StudioError) as exhausted:reserve(p,second,binding)
        self.assertEqual(exhausted.exception.code,'REVISION_LIMIT')

    def test_story_one_segment_revision_from_ordinary_launcher_preserves_independent_scene_and_replays(self):self.exercise('story')
    def test_visual_one_mapping_revision_from_ordinary_launcher_reuses_exact_wav_and_replays(self):self.exercise('visual')
