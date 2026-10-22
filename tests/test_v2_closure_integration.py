"""Source-only ordinary entry, real handlers, only final tool IO replaced."""
from contextlib import contextmanager, ExitStack, redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

from app import operations
from app.jobs import JobManager
from app.registry import Registry, content_hash
from app.v2_director import proposal
from app.v2_host_services import HostServices
from app.v2_narration import profile_spec
from app.v2_provider_bridge import SyntheticTransport
from scripts.launch_multi_ai_v2 import main as launch
from tests.test_v2_acquisition import source_config,synthetic_reply


class OfflineDirector:
    is_simulated=True
    def __init__(self):self.tasks=[];self.runs={}
    def start(self,task,profile,attempt_dir=None):
        self.tasks.append(deepcopy(task));handle=str(len(self.tasks));self.runs[handle]=task;return handle
    def poll(self,handle):
        task=self.runs.pop(handle,None)
        if task is None:return []
        _,measured=proposal(task['context']);plan=deepcopy(measured['edit_plan'])
        decision=task['context'].get('targeted_fix')
        if decision and decision['action']=='execute':
            plan=deepcopy(next(v for v in task['context']['dependency_results'].values() if 'plan_id' in v))
            index=next(i for i,p in enumerate(plan['shot_placements']) if p['placement_id']==decision['location'])
            plan['shot_placements'][index]['shot']['visual_function']=decision['instruction']
        plan['important_decisions'].append('Transport stub structured director decision')
        return [{'type':'completed','result':plan,'simulated':True}]
    def cancel(self,handle):self.runs.pop(handle,None)


class NativeIO:
    def __init__(self):self.commands=[];self.stages=[];self.duration=60
    @contextmanager
    def __call__(self,pipeline,node):
        self.stages.append(node['stage']);voice=profile_spec()['voice'];actual_hash=operations.content_hash
        model=Path(__file__).resolve().parents[1]/voice['model_ref']
        def tool(ctx,name):return str(model) if name=='tts_model' else sys.executable
        def ctx_run(ctx,argv,**kwargs):
            self.commands.append([str(a) for a in argv]);request=json.loads(Path(argv[-1]).read_text(encoding='utf-8'))
            out=Path(request['output']);out.parent.mkdir(parents=True,exist_ok=True)
            with wave.open(str(out),'wb') as stream:
                stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(voice['sample_rate_hz'])
                stream.writeframes(b'\xe8\x03'*(voice['sample_rate_hz']*self.duration))
            value={'duration_seconds':self.duration,'cache_hit':False,
                'text_hash':hashlib.sha256(request['text'].encode()).hexdigest(),'wav_hash':content_hash(out),
                'model_hash':voice['model_sha256'],'model_config_hash':voice['model_config_sha256'],
                'generation_settings':voice['settings'],'engine_version':voice['engine_version'],
                'sample_rate_hz':voice['sample_rate_hz']}
            log=ctx.directory/kwargs['log_name'];log.write_text(json.dumps(value),encoding='utf-8');return log
        def process(argv,stdout,**kwargs):
            argv=[str(a) for a in argv];self.commands.append(argv)
            if '-consumer' in argv:
                target=Path(argv[argv.index('-consumer')+1].removeprefix('avformat:'))
                target.write_bytes(b'SYNTHETIC TOOL IO; NOT A VIDEO\n'+hashlib.sha256(Path(argv[1]).read_bytes()).digest()*80)
            elif '-show_streams' in argv:
                # Final assembly and each scene have the same controlled duration.
                stdout.write(json.dumps({'streams':[{'codec_type':'video','width':1920,'height':1080,
                    'r_frame_rate':'30/1','start_time':'0'},{'codec_type':'audio','start_time':'0'}],
                    'format':{'duration':str(json.loads(Path(argv[-1]).with_name('edit_plan.json').read_text())['duration_ms']/1000)}}))
            else:stdout.write('mean_volume: -18.0 dB\nmax_volume: -2.0 dB\n')
            return SimpleNamespace(returncode=0)
        with ExitStack() as stack:
            stack.enter_context(patch('app.production_profile.check_runtime',return_value={}))
            stack.enter_context(patch.object(operations.Context,'tool',new=tool))
            stack.enter_context(patch.object(operations.Context,'run',new=ctx_run))
            stack.enter_context(patch.object(operations,'content_hash',side_effect=lambda p:voice['model_sha256'] if Path(p)==model else actual_hash(p)))
            stack.enter_context(patch.object(JobManager,'_spawn',new=lambda manager,job_id:manager.work(job_id)))
            stack.enter_context(patch('app.v2_production.subprocess.run',side_effect=process))
            yield


class ClosureIntegrationTests(unittest.TestCase):
    def test_source_only_launcher_invokes_actual_handlers_director_gate_portable_and_restart(self):
        with tempfile.TemporaryDirectory(prefix='closure_handlers_e2e_') as temp:
            config=source_config(temp);config.acquisition.director_required=True
            config.brief.target_minutes=1
            config.runtime={'tts_python':sys.executable,'tts_model':'host-resource-intercepted',
                'melt_path':sys.executable,'ffprobe_path':'no-native-probe','ffmpeg_path':'no-native-decode'}
            voice=profile_spec()['voice']
            config.runtime.update(tts_engine='piper',tts_normalization_scope=voice['normalization_scope'],
                pronunciation_lexicon=profile_spec()['pronunciation_lexicon'])
            config.runtime.update({'tts_'+k:v for k,v in voice['settings'].items() if k!='speaker_id'})
            path=Path(temp)/'config.json';path.write_text(config.model_dump_json(),encoding='utf-8')
            director=OfflineDirector();native=NativeIO()
            services=HostServices(worker_transport=SyntheticTransport(responder=synthetic_reply),
                director_factory=lambda:director,handler_io=native)
            def call(*args):
                with redirect_stdout(io.StringIO()) as out:code=launch(['--config',str(path),*args],host_services=services)
                value=json.loads(out.getvalue());self.assertEqual(code,0,value);return value
            from app.multi_ai import Coordinator
            complete=Coordinator._complete;crashed=[]
            def crash_after_receipt(queue,registry,attempt,payload,host_metadata=None):
                if host_metadata and host_metadata['expected_output_schema']=='edit_plan' and not crashed:
                    crashed.append(attempt['id']);raise RuntimeError('receipt-before-completion crash')
                return complete(queue,registry,attempt,payload,host_metadata)
            with patch.object(Coordinator,'_complete',new=crash_after_receipt):
                with self.assertRaisesRegex(RuntimeError,'receipt-before-completion'):call('--execute-system','--authorize','start')
            self.assertEqual(len(director.tasks),1)
            value=call('--execute-system','resume')
            self.assertEqual(value['status'],'SIMULATION_SUCCEEDED',value)
            self.assertFalse(value['production_started']);self.assertFalse(value['uploaded'])
            self.assertEqual(len(director.tasks),1)
            from app.v2_acceptance import fingerprint
            manifest=json.loads((Path(config.root)/value['results']['portable']/'manifest.json').read_text(encoding='utf-8'))
            lineage=manifest['acquisition_lineage'];self.assertEqual(lineage['brief_sha256'],fingerprint(config.brief.model_dump()))
            self.assertEqual(lineage['citations'][0]['source_sha256'],config.inputs.sources[0].sha256)
            self.assertEqual(lineage['citations'][0]['edition'],config.inputs.sources[0].edition)
            scene,measured=proposal(director.tasks[0]['context'])
            self.assertEqual(measured['edit_plan']['duration_ms'],60000)
            self.assertEqual(measured['edit_plan']['subtitle_events'][0]['text'],scene['narration_segments'][0]['text'])
            for stage in ['candidate','wav','edit','director_apply','compile','render','qa','portable']:
                self.assertIn(stage,native.stages)
            before=(len(native.commands),len(director.tasks),len(services.worker_transport.calls))
            replay=call('--execute-system','resume');self.assertEqual(value['results'],replay['results'])
            self.assertEqual(before,(len(native.commands),len(director.tasks),len(services.worker_transport.calls)))
            inspected=call('--execute-system','inspect-artifacts');self.assertEqual(inspected['status'],'RECORDED_LOCAL_PACKAGE')
            with Registry(Path(config.root)/'coordinator') as db:
                rows=[row for row in db.db.execute('SELECT result,payload FROM ai_tasks') if json.loads(row['payload'])['expected_output']=='edit_plan']
                self.assertEqual(len(rows),1);self.assertEqual(json.loads(rows[0][0])['v2_acceptance']['status'],'ACCEPTED')
                self.assertEqual(db.db.execute('SELECT count(*) FROM ai_attempts WHERE task_id=(SELECT task_id FROM ai_attempts WHERE id=?)',
                    (crashed[0],)).fetchone()[0],1)

    def test_one_edit_fix_is_addressed_other_scene_is_preserved_and_second_cycle_is_rejected(self):
        from app.v2_acceptance import fingerprint
        from app.v2_director import fix_controller
        from app.registry import StudioError
        with tempfile.TemporaryDirectory(prefix='closure_targeted_') as temp:
            config=source_config(temp,2);config.acquisition.director_required=True;config.acquisition.auto_fix_authorized=True
            config.runtime={'tts_python':sys.executable,'tts_model':'resource-boundary','melt_path':sys.executable,
                'ffprobe_path':'no-native-probe','ffmpeg_path':'no-native-decode','tts_engine':'piper',
                'tts_normalization_scope':profile_spec()['voice']['normalization_scope'],
                'pronunciation_lexicon':profile_spec()['pronunciation_lexicon']}
            config.runtime.update({'tts_'+k:v for k,v in profile_spec()['voice']['settings'].items() if k!='speaker_id'})
            def responder(binding,context):
                reply=synthetic_reply(binding,context)
                if binding.task.expected_output_schema=='review':
                    plan=next(v for v in context['dependency_results'].values() if 'plan_id' in v)
                    if plan['scene_id'].endswith('n1'):
                        reply['payload']['issues']=[dict(issue_id='edit_issue',severity='warning',category='continuity',
                            location=plan['shot_placements'][0]['placement_id'],observation='Metadata-only picture intention',
                            effect_on_viewer='Needs a precise intention',
                            suggested_change='Explicit corrected visual intention',required_evidence='metadata_only')]
                return reply
            native=NativeIO();native.duration=30;director=OfflineDirector()
            from app.v2_pipeline import SystemPipeline
            services=HostServices(worker_transport=SyntheticTransport(responder=responder),director_factory=lambda:director,handler_io=native)
            p=SystemPipeline(config,opt_in=True,authorize=True,host_services=services)
            try:
                value=p.run();self.assertEqual(value['status'],'SIMULATION_SUCCEEDED',value)
                fixes=[t for t in director.tasks if t['context'].get('targeted_fix')]
                self.assertEqual(len(fixes),1);self.assertEqual(len(director.tasks),3)
                with self.assertRaises(StudioError):fix_controller(fixes[0]['context'],authorized=True,cycles=2)
                originals={n['scene_id']:p.accepted(n['task_id']) for n in p.nodes if n['stage']=='director'}
                corrected={n['scene_id']:p.accepted(n['task_id']) for n in p.nodes if n['stage']=='director_fix'}
                sid=next(s for s in originals if s.endswith('n2'));self.assertEqual(originals[sid],corrected[sid])
                first=next(s for s in originals if s.endswith('n1'))
                self.assertNotEqual(fingerprint(originals[first]),fingerprint(corrected[first]))
                count=len(director.tasks);self.assertEqual(p.run()['status'],'SIMULATION_SUCCEEDED');self.assertEqual(count,len(director.tasks))
            finally:p.close()
