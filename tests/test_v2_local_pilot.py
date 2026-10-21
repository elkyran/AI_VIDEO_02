"""Host admission and fail-closed local-pilot boundaries; no TTS/render/model calls."""
from copy import deepcopy
import json
import hashlib
from pathlib import Path
import tempfile
import subprocess
import sys
import wave
import unittest
from unittest.mock import patch

from app.contracts import ProjectBrief
from app.jobs import JobManager
from app.registry import Registry, StudioError, content_hash, write_generated_json
from app.v2_acceptance import fingerprint
from app.v2_contracts import Artifact, Result, Task
from app.v2_local_pilot import (PilotPermit, LocalPilot, OPERATIONS, original_inputs, storage_check,
                               safe_root, verify_audio, verify_portable, initialize)
from app.v2_narration import profile_spec
from app.v2_narration import narration_descriptors


class LocalPilotTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='pilot_boundaries_')
        self.addCleanup(self.temporary.cleanup)
        self.root=(Path(self.temporary.name)/'pilot').resolve();self.root.mkdir()
        self.run_id='pilot_unit'
        inputs=original_inputs(self.run_id)
        write_generated_json(self.root,'authorization.json',{'scope':'PILOT-01','root':str(self.root),
            'run_id':self.run_id,'operations':OPERATIONS,'inputs':inputs,'input_sha256':fingerprint(inputs),
            'profile_sha256':fingerprint(profile_spec())})
        with Registry(self.root/'engine') as registry:
            registry.initialize(ProjectBrief(project_id=self.run_id,title='Unit',work_title='Original',character='Host'))
        self.space=patch('app.v2_local_pilot.shutil.disk_usage',return_value=type('Usage',(),{'free':10*1024**3})())
        self.space.start();self.addCleanup(self.space.stop)

    def pilot(self):
        pilot=LocalPilot(self.root,opt_in=True);self.addCleanup(pilot.close)
        return pilot

    def test_no_permission_and_existing_root_never_start_jobs(self):
        with patch.object(JobManager,'enqueue') as enqueue:
            with self.assertRaises(StudioError):LocalPilot(self.root)
            with self.assertRaises(StudioError):initialize(self.root,self.run_id)
            with self.assertRaises(FileExistsError):initialize(self.root,self.run_id,opt_in=True)
            enqueue.assert_not_called()

    def test_cli_admission_uses_canonical_host_permit_without_tts(self):
        command=[sys.executable,'-X','utf8','-m','app.v2_local_pilot','admit','--root',str(self.root),'--execute-local-pilot']
        result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        scene,task_id=json.loads(result.stdout.strip().splitlines()[-1])
        self.assertFalse(scene['simulated']);self.assertEqual(scene['scene_id'],'scene_001')
        with Registry(self.root/'engine') as registry:
            self.assertIsNone(registry.db.execute("SELECT name FROM sqlite_master WHERE name='jobs'").fetchone())

    def test_root_overlap_sync_and_traversal_are_rejected(self):
        from app.v2_local_pilot import REPO
        for path in (REPO,REPO/'pilot',REPO.parent,Path(self.temporary.name)/'OneDrive'/'pilot'):
            with self.subTest(path=path),self.assertRaises((ValueError,StudioError)):safe_root(path)
        permit=PilotPermit(self.root,opt_in=True)
        self.assertEqual(permit.root,self.root)

    def test_foreign_coordinator_root_fails_before_any_write(self):
        from app.multi_ai_v2 import RunCoordinator
        permit=PilotPermit(self.root,opt_in=True)
        foreign=self.root/'foreign'
        with self.assertRaises(ValueError):RunCoordinator(foreign,local_pilot=permit)
        self.assertFalse(foreign.exists())

    def test_low_storage_fails_before_runtime_or_jobs(self):
        with patch('app.v2_local_pilot.shutil.disk_usage',return_value=type('Usage',(),{'free':4*1024**3})()), \
                patch.object(JobManager,'enqueue') as enqueue:
            with self.assertRaises(StudioError) as caught:storage_check(self.root)
            self.assertEqual(caught.exception.code,'BLOCKED_STORAGE');enqueue.assert_not_called()

    def test_original_host_scene_uses_existing_gate_and_restart_replay_without_attempts(self):
        pilot=self.pilot()
        scene,task_id=pilot.admit('scene_001',1)
        with Registry(pilot.q.root) as registry:
            row=registry.db.execute('SELECT * FROM ai_tasks WHERE id=?',(task_id,)).fetchone()
            accepted=pilot.q.result_gate.accepted_dependency(registry,row)
            self.assertFalse(accepted['payload']['simulated'])
            self.assertEqual(row['attempts'],1)
            count=registry.db.execute('SELECT count(*) FROM ai_attempts').fetchone()[0]
        pilot.close();restarted=self.pilot()
        self.assertEqual((scene,task_id),restarted.admit('scene_001',1))
        with Registry(restarted.q.root) as registry:
            self.assertEqual(registry.db.execute('SELECT count(*) FROM ai_attempts').fetchone()[0],count)

    def test_stale_authorization_hash_or_profile_blocks_admission(self):
        pilot=self.pilot()
        path=self.root/'authorization.json'
        raw=json.loads(path.read_text(encoding='utf-8'));raw['input_sha256']='0'*64
        path.write_text(json.dumps(raw),encoding='utf-8')
        with self.assertRaises(StudioError):pilot.admit('scene_001',1)

    def test_foreign_run_root_fake_as_real_and_stale_input_hashes(self):
        pilot=self.pilot();scene,task_id=pilot.admit('scene_001',1)
        with Registry(pilot.q.root) as registry:
            row=registry.db.execute('SELECT * FROM ai_tasks WHERE id=?',(task_id,)).fetchone()
            task=pilot.q.queue._task(row)
            completion=json.loads((pilot.q.root/json.loads(row['result'])['path']).read_text(encoding='utf-8'))
        contract=Task.model_validate(task['context']['v2_contract'])
        result=Result.model_validate(completion['host_metadata'])
        for defect in ('root','run','fake','hash'):
            altered=result.model_copy(deep=True);changed=contract.model_copy(deep=True)
            root=pilot.q.root
            if defect=='root':root=self.root/'foreign'
            if defect=='run':changed=changed.model_copy(update={'run_id':'other'})
            if defect=='fake':altered=altered.model_copy(update={'simulated':True})
            if defect=='hash':changed=changed.model_copy(update={'input_hashes':{'scene':'0'*64}})
            with self.subTest(defect=defect),self.assertRaises(StudioError):
                pilot.permit.validate_admission(root,changed,task,task,altered)

    def test_no_global_gate_unlock_for_local_capability(self):
        pilot=self.pilot();_,task_id=pilot.admit('scene_001',1)
        with Registry(pilot.q.root) as registry:
            row=registry.db.execute('SELECT * FROM ai_tasks WHERE id=?',(task_id,)).fetchone()
            pilot.q.result_gate.local_pilot=None
            with self.assertRaises(StudioError) as caught:pilot.q.result_gate.accepted_dependency(registry,row)
            self.assertEqual(caught.exception.code,'PILOT_PERMISSION_REQUIRED')

    def test_changed_scene_with_old_task_key_is_conflict_and_original_is_preserved(self):
        pilot=self.pilot();scene,task_id=pilot.admit('scene_001',1)
        with Registry(pilot.q.root) as registry:
            row=registry.db.execute('SELECT * FROM ai_tasks WHERE id=?',(task_id,)).fetchone()
            task=pilot.q.queue._task(row);original=row['result']
        spec={key:task[key] for key in ('task_id','project_id','role','resource_class','expected_output','depends_on','input_versions','context','max_attempts')}
        spec=deepcopy(spec);spec['context']['manual_scene']['narration_segments'][0]['text']='Other input'
        with self.assertRaises(StudioError) as caught:pilot.q.queue.enqueue(spec)
        self.assertEqual(caught.exception.code,'IDEMPOTENCY_CONFLICT')
        with Registry(pilot.q.root) as registry:
            self.assertEqual(registry.db.execute('SELECT result FROM ai_tasks WHERE id=?',(task_id,)).fetchone()[0],original)

    def test_completed_job_from_foreign_run_is_not_a_wav_receipt(self):
        candidate={'simulated':False,'script':{'segments':[]}}
        status={'project_id':'foreign','operation':'synthesize','state':'done','invalidated':0}
        with patch.object(JobManager,'status',return_value=status):
            with self.assertRaises(StudioError) as caught:verify_audio(self.root/'engine',self.run_id,candidate,'job_other')
        self.assertEqual(caught.exception.code,'AUDIO_RECEIPT_INVALID')

    def test_wrong_physical_wav_hash_is_rejected_even_with_matching_job_manifest(self):
        from app.operations import ProductionScript,ProductionBlock
        engine=self.root/'engine'
        script=ProductionScript(project_id=self.run_id,title='Unit',scope='Fixture',
            segments=[ProductionBlock(id='block',title='Unit',text='Оригинальный текст.',visual_asset_id='image_1')]).model_dump()
        write_generated_json(engine,'script.json',script)
        with Registry(engine) as registry:
            registry.register_asset(self.run_id,'script','script.json',kind='text',rights='owned',base_revision=1,idempotency_key='script')
            asset=registry.asset(self.run_id,'script',verify=True)
        candidate={'simulated':False,'script':script}
        predicted=narration_descriptors(candidate,engine)['segments'][0]
        wav=engine/predicted['cache_path'];wav.parent.mkdir(parents=True)
        with wave.open(str(wav),'wb') as stream:
            stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(22050)
            stream.writeframes(b'\x01\x01'*2205)
        write_generated_json(engine,'jobs/job_unit/block.request.json',
            {'text':predicted['spoken_text'],'output':str(wav),'config':predicted['settings']})
        voice=profile_spec()['voice']
        record={'path':predicted['cache_path'],'block_id':'block','text':predicted['text'],
            'text_hash':hashlib.sha256(predicted['spoken_text'].encode()).hexdigest(),'wav_hash':'0'*64,
            'model_hash':voice['model_sha256'],'model_config_hash':voice['model_config_sha256'],
            'generation_settings':voice['settings'],'engine_version':voice['engine_version'],
            'sample_rate_hz':22050,'duration_seconds':.1,'cache_hit':False}
        write_generated_json(engine,'jobs/job_unit/audio_manifest.json',{'project_id':self.run_id,
            'script_asset_id':'script','script_sha256':asset['sha256'],'segments':[record]})
        status={'project_id':self.run_id,'operation':'synthesize','state':'done','invalidated':0,
            'parameters':{'script_asset_id':'script'},'result':{'manifest':'jobs/job_unit/audio_manifest.json',
            'manifest_sha256':content_hash(engine/'jobs/job_unit/audio_manifest.json'),'script_sha256':asset['sha256']}}
        with patch.object(JobManager,'status',return_value=status):
            with self.assertRaises(StudioError) as caught:verify_audio(engine,self.run_id,candidate,'job_unit')
        self.assertEqual(caught.exception.code,'AUDIO_RECEIPT_INVALID')

    def test_corrupted_mp4_output_blocks_replay_and_is_not_overwritten(self):
        pilot=self.pilot();video=self.root/'unit.mp4';video.write_bytes(b'original fixture')
        value={'compiled':{},'video':str(video),'video_sha256':content_hash(video),'audio_receipts':[]}
        with patch.object(pilot.bridge,'verify_compiled'):
            self.assertEqual(pilot.verify_prepared(value),value)
            video.write_bytes(b'manual edit')
            with self.assertRaises(StudioError):pilot.verify_prepared(value)
            self.assertEqual(video.read_bytes(),b'manual edit')

    def test_portable_mlt_absolute_reference_and_unlisted_database_are_rejected(self):
        folder=self.root/'portable';folder.mkdir()
        mlt=folder/'project.mlt';mlt.write_text('<mlt><producer><property name="resource">C:/outside/media.wav</property></producer></mlt>')
        def manifest():
            write_generated_json(folder,'manifest.json',{'files':{'project.mlt':{'sha256':content_hash(mlt),'bytes':mlt.stat().st_size}}})
        manifest()
        with self.assertRaises(StudioError):verify_portable(folder)
        mlt.write_text('<mlt/>')
        data={'files':{'project.mlt':{'sha256':content_hash(mlt),'bytes':mlt.stat().st_size}}}
        (folder/'manifest.json').write_text(json.dumps(data))
        self.assertEqual(verify_portable(folder),data)
        (folder/'registry.sqlite').write_bytes(b'unlisted')
        with self.assertRaises(StudioError):verify_portable(folder)

    def test_corrupted_checkpoint_fails_without_replacing_manual_data(self):
        pilot=self.pilot()
        pilot.save_checkpoint('unit','key',{'small':'original'})
        event=pilot.events('unit','key')[0]
        path=pilot.q.store.verify(Artifact.model_validate(event['artifact']))
        path.write_text('manual replacement',encoding='utf-8')
        with self.assertRaises(ValueError):pilot.checkpoint('unit','key')
        self.assertEqual(path.read_text(),'manual replacement')

    def test_portable_delivery_replay_returns_accepted_receipt_without_new_qa_or_render(self):
        pilot=self.pilot()
        for name in ('portable','portable_copy'):
            folder=self.root/name;folder.mkdir()
            (folder/'project.mlt').write_bytes(b'<mlt/>');(folder/'final.mp4').write_bytes(b'unit video bytes')
            files={p.name:{'sha256':content_hash(p),'bytes':p.stat().st_size} for p in folder.iterdir()}
            write_generated_json(folder,'manifest.json',{'files':files})
        prior={'status':'accepted_unit','portable_manifest_sha256':content_hash(self.root/'portable/manifest.json')}
        pilot.save_checkpoint('pilot_delivery','local',prior)
        with patch.object(pilot,'film',return_value={}),patch.object(pilot,'qa') as qa:
            self.assertEqual(pilot.deliver(),prior);qa.assert_not_called()
            (self.root/'portable_copy/final.mp4').write_bytes(b'manual edit')
            with self.assertRaises(StudioError):pilot.deliver()

    def test_revision_changes_exactly_one_segment_and_keeps_neighbor_scenes(self):
        scenes=original_inputs(self.run_id)['scenes']
        before=scenes[:3];after=[scenes[0],scenes[3],scenes[2]]
        self.assertEqual(before[0],after[0]);self.assertEqual(before[2],after[2])
        differences=[(i,j) for i in range(3) for j in range(2)
                     if before[i]['narration_segments'][j]!=after[i]['narration_segments'][j]]
        self.assertEqual(differences,[(1,0)])
