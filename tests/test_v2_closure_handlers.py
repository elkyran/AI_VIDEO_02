"""Actual generic handlers; only runtime/native process IO is replaced."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.contracts import ProjectBrief
from app.operations import ProductionBlock, ProductionScript
from app.jobs import JobManager
from app.registry import Registry, StudioError, write_generated_json
from app.v2_acceptance import fingerprint
from app.v2_ids import derived_id
from app.v2_pipeline import SystemPipeline
from app.v2_production import MeasuredEngine


class GenericRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='closure_recovery_');self.addCleanup(self.temp.cleanup)
        self.p=SystemPipeline.__new__(SystemPipeline);p=self.p
        p.root=Path(self.temp.name)/'run';p.engine=p.root/'engine';p.run_id='recovery'
        p.config=SimpleNamespace(budget=SimpleNamespace(seconds=1,attempts=2),acquisition=SimpleNamespace(retryable_job_errors=['TIMEOUT']))
        with Registry(p.engine) as registry:
            registry.initialize(ProjectBrief(project_id=p.run_id,title='Fixture',work_title='Owned',character='Guide'))
            (p.engine/'image.png').write_bytes(b'owned fixture')
            registry.register_asset(p.run_id,'image','image.png',kind='image',rights='owned',base_revision=1,idempotency_key='image')
        self.candidate={'script':ProductionScript(project_id=p.run_id,title='Scene',scope='No tools',overlays_enabled=False,
            segments=[ProductionBlock(id='line',title='Line',text='Text',visual_asset_id='image')]).model_dump()}
        self.sid=derived_id('script','h'+fingerprint(self.candidate['script']))
        relative='scripts/'+self.sid+'.json';write_generated_json(p.engine,relative,self.candidate['script'])
        p.register(self.sid,relative,'text');self.manager=JobManager(p.engine)
        with Registry(p.engine) as registry:base=registry._project(p.run_id)['revision']
        self.receipt=self.manager.enqueue(p.run_id,'synthesize',{'script_asset_id':self.sid},base,'same_key',start=False)
        # The existing coordinator event log, not a new recovery database.
        from app.multi_ai_v2 import RunCoordinator
        p.q=RunCoordinator(p.root/'coordinator');self.addCleanup(p.q.close)

    def state(self,**values):
        if not values:return
        with Registry(self.p.engine) as registry:
            registry.db.execute('UPDATE jobs SET '+','.join(k+'=?' for k in values)+' WHERE id=?',
                [*values.values(),self.receipt['job_id']])

    def ownership(self,birth='owned'):
        """Controlled native receipt fixture; real object path tested separately."""
        from app.v2_spawn_receipts import binding
        from app.v2_job_recovery import record
        job=self.manager.status(self.receipt['job_id']);base=binding(self.p,job)
        attempt=job['attempts']+1 if job['state']=='pending' else job['attempts']
        value={**base,'expected_attempt':attempt,'intent_key':fingerprint({**base,'expected_attempt':attempt}),
               'spawn_token':'fixture-token'}
        record(self.p,'system_spawn_intent',job['job_id'],value)
        record(self.p,'system_spawn_receipt',job['job_id'],{**value,'worker_pid':job['worker_pid'] or 99999999,
            'birth':birth,'scope':'EXACT_SPAWN_OBJECT','identity_source':'controlled test native boundary'})

    def test_dead_running_same_request_reaches_existing_resume_once(self):
        self.state(state='running',worker_pid=99999999,attempts=1)
        # A host receipt for this attempt predates the observed process exit.
        self.ownership('owned-birth')
        def finished(manager,job_id):
            self.state(state='done',worker_pid=None,attempts=2)
        original=JobManager.resume;calls=[]
        def resume(manager,job_id):calls.append(job_id);return original(manager,job_id)
        with patch('app.production_profile.check_runtime',return_value={}), \
             patch('app.multi_ai_preview.process_identity',return_value=False), \
             patch('app.jobs.process_alive',return_value=False), \
             patch.object(JobManager,'_spawn',autospec=True,side_effect=finished) as spawn, \
             patch.object(JobManager,'resume',new=resume), \
             patch('app.v2_pipeline.verify_audio',return_value={'verified_receipt_boundary':True}):
            self.assertEqual(self.p.synthesize(self.candidate,'same_key'),{'verified_receipt_boundary':True})
            self.p.synthesize(self.candidate,'same_key')
        self.assertEqual(calls,[self.receipt['job_id']]);self.assertEqual(spawn.call_count,1)

    def test_terminal_unknown_foreign_and_ambiguous_intent_fail_closed(self):
        from app.v2_job_recovery import guarded_resume,record
        manager=self.manager;identifier=self.receipt['job_id']
        record(self.p,'system_worker_identity',identifier,{'attempts':1,'worker_pid':99999999,'birth':'owned'})
        cases=[({'invalidated':1},False,'JOB_STALE'),({'cancel_requested':1},False,'CANCELLED'),
            ({'attempts':2,'state':'failed','error':json.dumps({'code':'TIMEOUT'})},False,'RETRY_LIMIT'),({},None,'JOB_LIVENESS_UNVERIFIED'),({},'foreign','JOB_LIVENESS_UNVERIFIED'),
            ({'state':'failed','error':json.dumps({'code':'RIGHTS_UNCONFIRMED'})},False,'JOB_TERMINAL'),
            ({'state':'blocked','error':json.dumps({'code':'CAPABILITY_MISSING'})},False,'JOB_TERMINAL')]
        for update,identity,code in cases:
            with self.subTest(code=code,identity=identity):
                self.state(state='running',worker_pid=99999999,attempts=1,cancel_requested=0,invalidated=0,error=None)
                self.state(**update)
                self.ownership()
                with patch('app.multi_ai_preview.process_identity',return_value=identity),patch.object(JobManager,'_spawn') as spawn:
                    with self.assertRaises(StudioError) as error:guarded_resume(self.p,manager,manager.status(identifier))
                self.assertEqual(error.exception.code,code);spawn.assert_not_called()
        self.state(state='running',worker_pid=99999999,attempts=1,cancel_requested=0,invalidated=0,error=None)
        self.ownership()
        with patch('app.multi_ai_preview.process_identity',return_value='owned'),patch.object(JobManager,'resume') as resume:
            self.assertFalse(guarded_resume(self.p,manager,manager.status(identifier)));resume.assert_not_called()
        with patch('app.multi_ai_preview.process_identity',return_value=False),patch('app.jobs.process_alive',return_value=False), \
             patch.object(JobManager,'_spawn',side_effect=RuntimeError('crash after resume intent')) as spawn:
            with self.assertRaises(RuntimeError):guarded_resume(self.p,manager,manager.status(identifier))
        self.assertEqual(spawn.call_count,1)
        self.state(state='running',worker_pid=99999999,attempts=1)
        self.ownership()
        with patch('app.multi_ai_preview.process_identity',return_value=False),patch.object(JobManager,'resume') as resume:
            with self.assertRaises(StudioError) as error:guarded_resume(self.p,manager,manager.status(identifier))
        self.assertEqual(error.exception.code,'JOB_RECOVERY_HOLD');resume.assert_not_called()

    def test_live_final_attempt_polls_and_async_resume_observes_new_worker(self):
        from app.v2_job_recovery import guarded_resume,record
        identifier=self.receipt['job_id']
        self.state(state='running',worker_pid=12345,attempts=2)
        self.ownership('current')
        record(self.p,'system_worker_identity',identifier,{'attempts':2,'worker_pid':12345,'birth':'current'})
        with patch('app.multi_ai_preview.process_identity',return_value='current'),patch.object(JobManager,'resume') as resume:
            self.assertFalse(guarded_resume(self.p,self.manager,self.manager.status(identifier)));resume.assert_not_called()
        self.state(state='failed',worker_pid=None,attempts=1,error=json.dumps({'code':'TIMEOUT'}))
        self.ownership('current')
        observed=[]
        def sleep(_):
            if not observed:self.state(state='running',worker_pid=12345,attempts=2);observed.append('running')
            else:self.state(state='done',worker_pid=None);observed.append('done')
        def spawned(manager,job_id):
            manager.spawn_observer('intent',job_id,None)
            manager.spawn_observer('spawned',job_id,SimpleNamespace(pid=12345))
        def identity(pid):return False if self.manager.status(identifier)['state']=='failed' else 'current'
        with patch.object(JobManager,'_spawn',autospec=True,side_effect=spawned) as spawn, \
             patch('app.v2_spawn_receipts.spawned_identity',return_value='current'), \
             patch('app.multi_ai_preview.process_identity',side_effect=identity), \
             patch('app.v2_pipeline.time.sleep',side_effect=sleep),patch('app.v2_pipeline.verify_audio',return_value={'receipt':True}):
            self.assertEqual(self.p.synthesize(self.candidate,'same_key'),{'receipt':True})
        self.assertEqual(observed,['running','done']);self.assertEqual(spawn.call_count,1)
        self.assertEqual(spawn.call_args.args[1],identifier)

    def test_explicit_retryable_failure_uses_the_actual_helper_and_binding_tamper_is_rejected(self):
        from app.v2_job_recovery import guarded_resume,existing_request
        self.state(state='failed',error=json.dumps({'code':'TIMEOUT'}),attempts=1)
        self.ownership()
        with patch.object(JobManager,'_spawn') as spawn:
            self.assertTrue(guarded_resume(self.p,self.manager,self.manager.status(self.receipt['job_id'])))
        spawn.assert_called_once_with(self.receipt['job_id'])
        with Registry(self.p.engine) as db:
            db.db.execute("UPDATE requests SET request_hash=? WHERE key='same_key'",('f'*64,))
        with self.assertRaises(StudioError):existing_request(self.p,self.manager,self.sid,self.candidate,'same_key')


class GenericQATests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='closure_qa_');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.engine=MeasuredEngine();self.engine.root=self.root;self.engine.engine=self.root
        (self.root/'runtime.json').write_text(json.dumps({'ffprobe_path':'no-native-ffprobe','ffmpeg_path':'no-native-ffmpeg'}))
        (self.root/'project.mlt').write_text('<mlt><producer><property name="resource">0</property></producer></mlt>')
        (self.root/'edit_plan.json').write_text(json.dumps({'subtitle_events':[{'start_ms':0,'end_ms':1000}]}))
        (self.root/'subtitles.srt').write_text('1\n00:00:00,000 --> 00:00:01,000\nOwned text\n',encoding='utf-8')
        self.compiled={'project_mlt':str(self.root/'project.mlt'),'duration_seconds':1}
        self.probe={'streams':[{'codec_type':'video','width':1920,'height':1080,'r_frame_rate':'30/1','start_time':'0'},
            {'codec_type':'audio','start_time':'0'}],'format':{'duration':'1'}}
        self.decode='mean_volume: -18.0 dB\nmax_volume: -2.0 dB\n'

    def run_qa(self):
        def native(argv,stdout,**kwargs):
            stdout.write(json.dumps(self.probe) if argv[0]=='no-native-ffprobe' else self.decode)
            return SimpleNamespace(returncode=0)
        with patch('app.v2_production.subprocess.run',side_effect=native):
            return self.engine.qa(self.compiled,self.root/'synthetic_not_created.mp4')

    def test_nonfinite_stream_start_and_expected_duration_are_rejected(self):
        for target in ['stream','expected']:
            for value in ['NaN','Infinity','-Infinity']:
                with self.subTest(target=target,value=value):
                    self.probe['streams'][0]['start_time']=value if target=='stream' else '0'
                    self.compiled['duration_seconds']=float(value) if target=='expected' else 1
                    with self.assertRaises(StudioError) as error:self.run_qa()
                    self.assertEqual(error.exception.code,'TECHNICAL_QA_FAILED')

    def test_actual_qa_happy_path_and_native_error_stream_format_duration_subtitles(self):
        result=self.run_qa();self.assertEqual(result['status'],'VERIFIED')
        self.assertEqual(result['full_video_creative_qa'],'NOT_RUN')
        original=deepcopy(self.probe)
        for change in ['audio','width','fps','duration','start','black','silent','decode']:
            with self.subTest(change=change):
                self.probe=deepcopy(original);self.decode='mean_volume: -18.0 dB\nmax_volume: -2.0 dB\n'
                if change=='audio':self.probe['streams'].pop()
                if change=='width':self.probe['streams'][0]['width']=1280
                if change=='fps':self.probe['streams'][0]['r_frame_rate']='25/1'
                if change=='duration':self.probe['format']['duration']='1.3'
                if change=='start':self.probe['streams'][0]['start_time']='0.3'
                if change=='black':self.decode+='black_start: 0'
                if change=='silent':self.decode='mean_volume: -90.0 dB\nmax_volume: -90.0 dB'
                if change=='decode':self.decode+='Error while decoding'
                with self.assertRaises(StudioError):self.run_qa()
        self.probe=original;self.decode='mean_volume: -18.0 dB\nmax_volume: -2.0 dB\n'
        (self.root/'subtitles.srt').write_text('1\n00:00:00,000 --> 00:00:02,000\nOut of bounds\n')
        with self.assertRaises(StudioError):self.run_qa()
        with patch('app.v2_production.subprocess.run',return_value=SimpleNamespace(returncode=2)):
            with self.assertRaises(StudioError) as error:self.engine.qa(self.compiled,'not-created.mp4')
        self.assertEqual(error.exception.code,'LOCAL_TOOL_FAILED')


class GenericMeasuredEditTests(unittest.TestCase):
    def test_actual_measured_edit_preserves_text_order_silence_and_rejects_speech_tail(self):
        from tests.test_v2_production import ProductionCandidateTests
        from app.v2_contracts import Task
        from app.v2_visuals import SegmentVisuals
        from app.registry import canonical_json,content_hash
        import wave
        ProductionCandidateTests.setUp(self)
        artifact=ProductionCandidateTests.candidate(self)
        raw=self.bridge.read_candidate(artifact);scene,_=self.bridge.accepted_scene(self.run_id,self.task_id,1)
        identifier='measured_candidate'
        hashes={'candidate':fingerprint(artifact)}
        contract=Task(task_id=identifier,run_id=self.run_id,role='python',capability='handler_fixture',input_version=1,
            input_hashes=hashes,depends_on=[self.task_id],assigned_worker='host',provider_profile_id='simulation',
            expected_output_schema='note',idempotency_key=identifier)
        self.q.queue.enqueue(dict(task_id=identifier,project_id=self.run_id,role='director',resource_class='cloud',
            expected_output='note',input_versions=hashes,depends_on=[self.task_id],context={'v2_contract':contract.model_dump(),
                'fixture_result':{'schema_version':1,'simulated':True,'text':canonical_json({'candidate':artifact})}}))
        self.q.queue.resume()
        for _ in range(8):self.q.queue.tick()
        task=next(t for t in self.q.queue.snapshot()['tasks'] if t['task_id']==identifier)
        self.assertEqual(task['status'],'completed',task)
        p=SystemPipeline.__new__(SystemPipeline);p.root=self.root;p.engine=self.engine;p.run_id=self.run_id;p.q=self.q;p.bridge=self.bridge
        p.config=SimpleNamespace(inputs=SimpleNamespace(visuals=[SegmentVisuals.model_validate(m) for m in self.mapping]))
        audio={'segments':[]}
        for i,mapping in enumerate(raw['scene_block_mapping']):
            path=self.engine/('controlled_'+str(i)+'.wav')
            with wave.open(str(path),'wb') as stream:
                stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(22050)
                stream.writeframes((1000+i).to_bytes(2,'little')*4410+b'\0\0'*1102)
            audio['segments'].append({'block_id':mapping['block_id'],'path':path.name,'wav_sha256':content_hash(path)})
        before=[content_hash(self.engine/r['path']) for r in audio['segments']]
        plan,assets=p.measured_edit(scene.model_dump(),audio,{'dependencies':[identifier]})
        self.assertEqual(plan.duration_ms,400)
        self.assertEqual([p.timeline_start_ms for p in plan.voice_placements],[0,200])
        self.assertEqual([s.text for s in plan.subtitle_events],[n.text for n in scene.narration_segments])
        self.assertEqual(before,[content_hash(self.engine/r['path']) for r in audio['segments']])
        self.assertTrue(any(a['kind']=='audio' for a in assets))
        path=self.engine/'nonzero_tail.wav'
        with wave.open(str(path),'wb') as stream:
            stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(22050);stream.writeframes(b'\xe8\x03'*5512)
        bad=deepcopy(audio);bad['segments'][0].update(path=path.name,wav_sha256=content_hash(path))
        with self.assertRaises(StudioError) as error:p.measured_edit(scene.model_dump(),bad,{'dependencies':[identifier]})
        self.assertEqual(error.exception.code,'NARRATION_TRUNCATION')


class DirectorScopeTests(unittest.TestCase):
    def test_director_cannot_change_measured_voice_profile_catalog_or_capabilities(self):
        from app.editorial import demo_result
        from app.v2_director import validate_plan
        scene=demo_result('scene',{'project_id':'scope'})
        plan=demo_result('edit_plan',{'project_id':'scope'});plan.update(width=1920,height=1080)
        context={'dependency_results':{'scene':scene,'measured':{'edit_plan':plan,'audio':{'verified':'test boundary'},
            'assets':[{'id':'fixture_video','kind':'video','rights':'owned','duration_ms':6000}]}},'project_id':'scope'}
        self.assertEqual(validate_plan(plan,context),plan)
        for change in ['voice','profile','asset','range','effect','frame']:
            with self.subTest(change=change):
                bad=deepcopy(plan)
                if change=='voice':bad['voice_version']='different'
                if change=='profile':bad['width']=320
                if change=='asset':bad['shot_placements'][0]['asset_id']='foreign'
                if change=='range':bad['shot_placements'][0].update(source_in_ms=5500,source_out_ms=7000)
                if change=='effect':bad['transitions']=[{'kind':'unsupported'}]
                if change=='frame':bad['shot_placements'][0]['source_out_ms']+=20
                with self.assertRaises((StudioError,ValueError)):validate_plan(bad,context)
