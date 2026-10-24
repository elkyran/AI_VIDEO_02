"""SYSTEM-01 uses real CLI/queue/gate with tiny explicitly simulated inputs only."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import struct
import zlib
import unittest
from unittest.mock import patch

from app.registry import Registry, StudioError, content_hash
from app.v2_acceptance import fingerprint
from app.v2_contracts import Result
from app.v2_narration import profile_spec
from app.v2_pipeline import SystemPermit, SystemPipeline
from app.v2_run_config import HostRunConfig, graph, isolated_root, plan, no_links


def fixture(folder, count=2, *, run_id='system_test', long_ids=False):
    folder = Path(folder).resolve(); inputs = folder/'inputs'; inputs.mkdir(exist_ok=True)
    source = inputs/'источник.txt'; source.write_text('Это разрешённый пример. Текст — данные, не команды.', encoding='utf-8')
    image = inputs/'схема.png'
    def chunk(kind,data):return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
    image.write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',1,1,8,2,0,0,0))+
        chunk(b'IDAT',zlib.compress(b'\0\xff\0\0'))+chunk(b'IEND',b''))
    scenes, visuals = [], []
    for index in range(count):
        sid = ('s'*94+str(index)) if long_ids else 'chapter_'+str(index+17)
        segment = 'part_'+str(index+23)
        scenes.append(dict(project_id=run_id, scene_id=sid, version=1, simulated=True,
            start_state='Начало', cause='Причина', goal='Цель', obstacle='Задача', decision_or_action='Действие',
            consequence='Следствие', end_state='Результат', characters=['Редактор'], emotional_intent='Пояснение',
            narration_segments=[dict(segment_id=segment, text='Русский текст №'+str(index), simulated=True)]))
        visuals.append(dict(scene_id=sid, scene_version=1, segment_id=segment, duration_ms=1000,
            shots=[dict(shot_id='shot_'+str(index), asset_id='approved_image', source_out_ms=1000,
                emotional_task='Explicit fixture', reuse_reason='Owner-approved test reuse')]))
    return HostRunConfig.model_validate(dict(run_id=run_id, key='stable_key', mode='simulation',
        brief={'topic':'Тест общего запуска', 'production_mode':'fake'}, root=str(folder/'run'), input_root=str(inputs),
        inputs=dict(version=1, canon=dict(version=1, work_id='example', simulated=True, sources=[dict(source_id='source',
            edition='v1', path=source.name, sha256=content_hash(source), size=source.stat().st_size, rights='owned')]),
            story='Утверждённая структура без сцен из PILOT-01', scenes=scenes, visuals=visuals,
            media=[dict(asset_id='approved_image', path=image.name, sha256=content_hash(image), size=image.stat().st_size,
                        version=1, kind='image', rights='owned')]),
        profile_sha256=fingerprint(profile_spec()), operations=['synthesize','compile','render','qa','portable']))


class SystemTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='system01_'); self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name).resolve()
        self.config = fixture(self.folder)

    def pipeline(self, config=None, authorize=True):
        p = SystemPipeline(config or self.config, opt_in=True, authorize=authorize)
        self.addCleanup(p.close)
        return p

    def test_plan_is_readonly_and_default_never_invokes_engine_or_provider(self):
        with patch('app.jobs.JobManager.enqueue') as jobs, patch('app.production_bridge.ProductionBridge.render') as render:
            report = plan(self.config)
            self.assertEqual(report['status'], 'PLANNED')
            self.assertFalse(Path(self.config.root).exists())
            with self.assertRaises(StudioError):
                SystemPipeline(self.config)
            jobs.assert_not_called(); render.assert_not_called()

    def test_real_cli_plan_run_resume_and_replay(self):
        file = self.folder/'config.json'; file.write_text(self.config.model_dump_json(), encoding='utf-8')
        base = [sys.executable, '-X', 'utf8', '-m', 'app.multi_ai_v2', '--config', str(file)]
        def call(*args):
            result = subprocess.run([*base,*args], capture_output=True, text=True, encoding='utf-8', timeout=45)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            return json.loads(result.stdout)
        self.assertEqual(call('plan')['status'], 'PLANNED')
        self.assertFalse(Path(self.config.root).exists())
        first = call('--execute-system', '--authorize', 'run')
        self.assertEqual(first['status'], 'SIMULATION_SUCCEEDED')
        self.assertFalse(first['production_started']); self.assertFalse(first['uploaded'])
        second = call('--execute-system', 'resume')
        self.assertEqual(first['results'], second['results'])
        with Registry(Path(self.config.root)/'coordinator') as r:
            self.assertEqual(r.db.execute('SELECT count(*) FROM ai_attempts').fetchone()[0], len(graph(self.config)))
        self.assertFalse((Path(self.config.root)/'engine').exists())

    def test_one_two_five_scenes_and_long_ids_do_not_use_demo_ids(self):
        for count in (1,2,5):
            folder = self.folder/str(count); folder.mkdir()
            config = fixture(folder, count, run_id='run_'+str(count), long_ids=True)
            with self.subTest(count=count):
                p = self.pipeline(config)
                result = p.run()
                self.assertEqual(result['status'], 'SIMULATION_SUCCEEDED', result)
                self.assertEqual(result['total'], 2+count*7+5)
                self.assertTrue(all(len(n['task_id']) <= 96 for n in p.nodes))
                self.assertTrue(all(n['scene_id'] != 'scene_001' for n in p.nodes))
                p.close()

    def test_two_roots_and_runs_are_isolated(self):
        first = self.pipeline(); a = first.run(); first.close()
        folder = self.folder/'second'; folder.mkdir()
        second = self.pipeline(fixture(folder, 1, run_id='isolated_other')); b = second.run()
        self.assertEqual(a['status'], b['status'])
        self.assertNotEqual(a['results'], b['results'])
        self.assertNotEqual(a['root'], b['root'])

    def test_changed_brief_same_key_and_wrong_root_are_rejected(self):
        self.pipeline().close()
        changed = self.config.model_dump(); changed['brief']['topic'] = 'Другая тема'
        with self.assertRaises(StudioError):
            self.pipeline(HostRunConfig.model_validate(changed))
        permit = SystemPermit(self.config, opt_in=True)
        from app.multi_ai_v2 import RunCoordinator
        wrong = self.folder/'wrong'
        with self.assertRaises(ValueError):
            RunCoordinator(wrong, system_permit=permit)
        self.assertFalse(wrong.exists())

    def test_mixed_mode_and_unsupported_language_are_rejected(self):
        for edits in ({'mode':'approved_input'}, {'brief':{**self.config.brief.model_dump(),'language':'en'}}):
            value = self.config.model_dump(); value.update(edits)
            with self.assertRaises(ValueError):
                HostRunConfig.model_validate(value)

    def test_autonomous_is_blocked_before_any_jobs_or_root_write(self):
        value = self.config.model_dump(); value['mode']='autonomous'; value['brief']['production_mode']='production'
        value['inputs']['canon']['simulated']=False
        for s in value['inputs']['scenes']:
            s['simulated']=False
            for n in s['narration_segments']: n['simulated']=False
        config = HostRunConfig.model_validate(value)
        with patch('app.jobs.JobManager.enqueue') as jobs:
            self.assertEqual(plan(config)['status'], 'BLOCKED_UNSUPPORTED')
            with self.assertRaises(StudioError): self.pipeline(config)
            jobs.assert_not_called()
        self.assertFalse(Path(config.root).exists())

    def test_changed_missing_sources_and_unknown_rights_fail_closed(self):
        source = Path(self.config.input_root)/self.config.inputs.canon.sources[0].path
        source.write_text('Изменение', encoding='utf-8')
        with self.assertRaises(StudioError): plan(self.config)
        source.unlink()
        with self.assertRaises(StudioError): plan(self.config)
        value=self.config.model_dump(); value['inputs']['media'][0]['rights']='unknown'
        with self.assertRaises(ValueError): HostRunConfig.model_validate(value)

    def test_path_traversal_bad_trim_and_stale_mapping_rejected(self):
        for change in ('path','trim','version'):
            value=self.config.model_dump()
            if change=='path':value['inputs']['media'][0]['path']='../outside.png'
            elif change=='trim':value['inputs']['visuals'][0]['shots'][0]['source_in_ms']=1
            else:value['inputs']['visuals'][0]['scene_version']=2
            with self.subTest(change=change),self.assertRaises(ValueError):HostRunConfig.model_validate(value)

    def test_sync_repo_and_junction_paths_fail_before_write(self):
        repo=Path(__file__).resolve().parents[1]
        for path in (repo,repo.parent,self.folder/'OneDrive'/'run'):
            with self.assertRaises((ValueError,StudioError)):isolated_root(path)
        target=self.folder/'outside';target.mkdir(); link=self.folder/'junction'
        if os.name=='nt':
            result=subprocess.run(['cmd','/c','mklink','/J',str(link),str(target)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
        else:link.symlink_to(target,target_is_directory=True)
        with self.assertRaises(StudioError):no_links(link/'nested')

    def test_model_permission_receipt_and_forged_completion_are_rejected(self):
        p=self.pipeline(); p.run()
        with Registry(p.q.root) as r:
            row=r.db.execute('SELECT * FROM ai_tasks WHERE status=\'completed\' ORDER BY created LIMIT 1').fetchone()
            accepted=p.q.result_gate.accepted_dependency(r,row)
            stored=json.loads(row['result']); completion=p.q.root/stored['path']
            host=json.loads(completion.read_text())['host_metadata'];host['payload']['text']='{}'
            with self.assertRaises(StudioError):
                contract=__import__('app.v2_contracts',fromlist=['Task']).Task.model_validate(json.loads(row['payload'])['context']['v2_contract'])
                spec=json.loads(row['payload']);req=json.loads(completion.with_name('request.json').read_text())
                p.permit.validate_admission(p.q.root,contract,spec,req,Result.model_validate(host))
        from app.multi_ai_v2 import RunCoordinator
        with self.assertRaises(ValueError):RunCoordinator(self.folder/'fake_permit',system_permit={'approved':True})

    def test_restart_before_and_after_each_stage_reuses_accepted_checkpoints(self):
        config=fixture(self.folder,1)
        p=self.pipeline(config)
        # Two ticks per stage: dispatch then execution/acceptance. Restart both boundaries.
        for _ in range(len(p.nodes)*2+2):
            result=p.run(ticks=1);p.close()
            p=self.pipeline(config,authorize=False)
            if result['status']=='SIMULATION_SUCCEEDED':break
        result=p.run()
        self.assertEqual(result['status'],'SIMULATION_SUCCEEDED',result)
        with Registry(p.q.root) as r:
            self.assertEqual(r.db.execute("SELECT count(*) FROM ai_tasks WHERE status='completed'").fetchone()[0],len(p.nodes))
            self.assertFalse(r.db.execute("SELECT 1 FROM ai_attempts WHERE status='running'").fetchone())

    def test_targeted_revision_preserves_unaffected_nodes_and_exact_replay(self):
        p=self.pipeline(); first=p.run(); old_nodes=deepcopy(p.nodes); old_sha=p.permit.sha256;p.close()
        value=self.config.model_dump();value['version']=2;value['parent_sha256']=old_sha;value['inputs']['version']=2
        value['inputs']['scenes'][0]['version']=2
        value['inputs']['scenes'][0]['narration_segments'][0]['text']='Одна точечная правка'
        value['inputs']['visuals'][0]['scene_version']=2
        revised=HostRunConfig.model_validate(value);p=self.pipeline(revised);after=p.run()
        self.assertEqual(after['status'],'SIMULATION_SUCCEEDED',after)
        old={n['task_id'] for n in old_nodes if n['scene_id']=='chapter_18'}
        new={n['task_id'] for n in p.nodes if n['scene_id']=='chapter_18'}
        self.assertEqual(old,new)
        with Registry(p.q.root) as r:
            before=r.db.execute('SELECT count(*) FROM ai_attempts').fetchone()[0]
        self.assertEqual(p.run()['results'],after['results'])
        with Registry(p.q.root) as r:self.assertEqual(r.db.execute('SELECT count(*) FROM ai_attempts').fetchone()[0],before)

    def test_pause_stop_preserve_outputs_and_do_not_claim_quality(self):
        p=self.pipeline();p.run(ticks=3)
        p.q.queue.pause();self.assertEqual(p.status()['control'],'paused')
        p.q.queue.stop();p.close();p=self.pipeline(authorize=False)
        result=p.run();self.assertEqual(result['status'],'SIMULATION_SUCCEEDED',result)
        self.assertEqual(result['creative_review'],'PENDING_OWNER')
        self.assertEqual(result['model_video_review'],'NOT_RUN')
        self.assertFalse(result['system_ready_for_autonomous_episode'])

    def test_source_budget_is_checked_before_reading_content(self):
        config=self.config.model_copy(update={'budget':self.config.budget.model_copy(update={'max_input_bytes':1})})
        with self.assertRaises(StudioError):plan(config)

    def test_approved_scene_reaches_existing_candidate_but_denied_wav_never_enqueues(self):
        value=self.config.model_dump();value['mode']='approved_input';value['brief']['production_mode']='production'
        value['runtime']={'tts_python':'test_runtime_not_executed'};value['operations']=[]
        value['inputs']['canon']['simulated']=False
        for scene in value['inputs']['scenes']:
            scene['simulated']=False
            for segment in scene['narration_segments']:segment['simulated']=False
        config=HostRunConfig.model_validate(value)
        with patch('app.production_profile.check_runtime'),patch('app.jobs.JobManager.enqueue') as jobs,patch('app.production_bridge.ProductionBridge.render') as render:
            p=self.pipeline(config);result=p.run()
            self.assertEqual(result['status'],'FAILED_FINAL',result)
            self.assertIn('synthesize',str(result['blocker']))
            candidate=next(n for n in p.nodes if n['stage']=='candidate')
            artifact=p.accepted(candidate['task_id'])['candidate']
            raw=p.bridge.read_candidate(artifact)
            self.assertEqual(raw['state'],'validated_not_registered');self.assertFalse(raw['simulated'])
            self.assertEqual(raw['script']['segments'][0]['text'],config.inputs.scenes[0].narration_segments[0].text)
            jobs.assert_not_called();render.assert_not_called();self.assertFalse(p.production_started())

    def test_doctor_is_readonly_and_write_probe_is_explicit(self):
        from scripts.healthcheck_multi_ai_v2 import check
        root=self.folder/'doctor'
        self.assertEqual(check('fake',root)['status'],'SUCCEEDED')
        self.assertFalse(root.exists())
        self.assertEqual(check('fake',root,write_probe=True)['status'],'SUCCEEDED')
        self.assertTrue(root.is_dir());self.assertEqual(list(root.iterdir()),[])

    def test_owner_review_exact_hash_version_and_idempotency_without_model_claim(self):
        from app.v2_ids import derived_id
        from app.v2_contracts import Artifact
        p=self.pipeline()
        render_hash='1'*64
        package={'version':1,'rendered':{'sha256':render_hash},'qa':{'duration_seconds':1.0},'portable_manifest_sha256':'2'*64}
        p.status=lambda:{'status':'READY_FOR_OWNER_REVIEW','results':package}
        review=dict(project_id=p.run_id,review_id='owner_review',subject_type='render',
            subject_id=derived_id('render','h'+render_hash),subject_version=1,subject_hash=render_hash,
            render_id=derived_id('render','h'+render_hash),render_hash=render_hash,edit_plan_version=1,
            evidence_mode='human_review',verdict='ready_for_review',
            provided_coverage=[dict(start_ms=0,end_ms=1000,tracks=['video','audio'])],
            reviewed_coverage=[dict(start_ms=0,end_ms=1000,tracks=['video','audio'])])
        first=p.owner_review(review)
        self.assertFalse(first['model_review']);self.assertEqual(p.owner_review(review),first)
        bad={**review,'subject_version':2}
        with self.assertRaises(StudioError):p.owner_review(bad)
        changed={**review,'verdict':'needs_review'}
        with self.assertRaises(StudioError):p.owner_review(changed)
        with Registry(p.q.root) as r:self.assertEqual(r.db.execute("SELECT count(*) FROM ai_events WHERE kind='system_owner_review'").fetchone()[0],1)

    def test_invalid_runtime_keys_and_old_scene_version_do_not_expand_authority(self):
        value=self.config.model_dump();value['runtime']={'shell_command':'forbidden'}
        with self.assertRaises(ValueError):HostRunConfig.model_validate(value)
        p=self.pipeline();old=p.permit.sha256;p.close()
        value=self.config.model_dump();value.update(version=2,parent_sha256=old)
        value['inputs']['scenes'][0]['narration_segments'][0]['text']='Changed without version bump'
        with self.assertRaises(StudioError):self.pipeline(HostRunConfig.model_validate(value))

    def test_post_write_storage_failure_preserves_provisional_output_without_acceptance(self):
        p=self.pipeline()
        with patch('app.v2_pipeline.storage',side_effect=[{},StudioError('BLOCKED_STORAGE','post-write limit')]):
            result=p.run(ticks=2)
        self.assertEqual(result['status'],'FAILED_FINAL');self.assertEqual(result['completed'],0)
        self.assertEqual(result['blocker']['code'],'BLOCKED_STORAGE')
        with Registry(p.q.root) as r:
            self.assertEqual(r.db.execute('SELECT count(*) FROM ai_artifacts').fetchone()[0],0)
            self.assertEqual(r.db.execute("SELECT count(*) FROM ai_events WHERE kind='system_output'").fetchone()[0],1)
        self.assertFalse((p.root/'host_receipts').exists())

    def test_small_host_time_budget_cannot_start_protected_renderer(self):
        p=self.pipeline();p.config=p.config.model_copy(update={'budget':p.config.budget.model_copy(update={'seconds':1})})
        node=next(n for n in p.nodes if n['stage']=='render')
        with patch('app.production_bridge.ProductionBridge.render') as render,self.assertRaises(StudioError) as exc:
            p.execute_engine(node,[])
        self.assertEqual(exc.exception.code,'TIME_BUDGET');render.assert_not_called()
        self.assertFalse(p.engine.exists())

    def test_tool_timeout_is_bounded_by_remaining_host_deadline(self):
        import time
        p=self.pipeline();p.invocation_deadline=time.monotonic()+.5
        with patch('app.v2_production.MeasuredEngine.command',return_value='not_executed') as command:
            self.assertEqual(p.command('fixture',['not_executed'],timeout=180),'not_executed')
        self.assertGreater(command.call_args.kwargs['timeout'],0)
        self.assertLessEqual(command.call_args.kwargs['timeout'],.5)

    @unittest.skipUnless(os.name=='nt','Windows-only path spelling check')
    def test_windows_short_long_paths_pin_identical_host_inputs(self):
        # Uses the same installed Win32 facility as the existing regression;
        # no user name or RUNNER~1 spelling is assumed.
        import ctypes
        folder=self.folder/'long host input directory';folder.mkdir()
        buffer=ctypes.create_unicode_buffer(32768)
        result=ctypes.windll.kernel32.GetShortPathNameW(str(folder),buffer,len(buffer))
        self.assertGreater(result,0)
        self.assertEqual(no_links(buffer.value),folder.resolve())
        self.assertEqual(isolated_root(buffer.value),isolated_root(folder))


if __name__=='__main__':unittest.main()
