"""Temporary fixtures only; no auth, models, production or cloud requests."""
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch
import hashlib
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError
from app.ai_adapters import FakeAdapter
from app.multi_ai_v2 import RunCoordinator, STAGES, local_root
from app.registry import Registry, StudioError
from app.v2_contracts import Artifact, Brief, Review, Result, Task, issue_owner
from app.v2_storage import ArtifactStore, EvidenceRun, source_binding


def render_review(artifact):
    return Review(project_id=artifact.run_id, review_id='review1', subject_type='render',
        subject_id=artifact.artifact_id, subject_version=1, subject_hash=artifact.sha256,
        evidence_mode='video_audio', render_id=artifact.artifact_id, render_hash=artifact.sha256,
        edit_plan_version=1).model_dump()


class V2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='v2_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'local'

    def coordinator(self, **kwargs):
        q = RunCoordinator(self.root, **kwargs)
        self.addCleanup(q.close)
        return q

    def test_full_fake_graph_and_context(self):
        q = self.coordinator()
        s = q.create({'topic': 'Control fixture'}, 'one')
        done = q.advance(s['run_id'], 30)
        self.assertEqual(done['status'], 'SIMULATION_SUCCEEDED')
        self.assertEqual(len(done['tasks']), len(STAGES))
        self.assertFalse(done['ready_for_user'])
        self.assertEqual(done['video_qa'], 'NOT_RUN')
        self.assertEqual(done['model_requests'], 0)
        self.assertTrue(all(t['attempt'] == 1 for t in done['tasks']))
        pack = self.root / 'runs' / s['run_id'] / 'context' / done['tasks'][0]['task_id']
        self.assertEqual(len([p for p in pack.iterdir() if p.is_file()]), 8)
        self.assertEqual(len(list(pack.iterdir())), 9)  # Includes the new per-attempt Context Pack.
        self.assertEqual(len(list((pack/'attempts').glob('*/input_artifacts.json'))), 1)
        self.assertLess((pack / 'task.md').stat().st_size, 12000)

    def test_idempotent_run_and_no_duplicate_dispatch(self):
        q = self.coordinator()
        s = q.create({'topic': 'x'}, 'one')
        q.advance(s['run_id'], 30)
        q.create({'topic': 'x'}, 'one')
        done = q.advance(s['run_id'], 10)
        self.assertTrue(all(t['attempt'] == 1 for t in done['tasks']))
        with self.assertRaises(StudioError): q.create({'topic': 'changed'}, 'one')

    def test_restart_reconciles_and_reuses_completed_results(self):
        q = self.coordinator()
        s = q.create({'topic': 'x'}, 'one')
        q.advance(s['run_id'], 6)
        prior = {t['task_id']: t['result_ref'] for t in q.status(s['run_id'])['tasks'] if t['status'] == 'SUCCEEDED'}
        q.close()
        q = self.coordinator()
        q.create({'topic': 'x'}, 'one')
        done = q.advance(s['run_id'], 30)
        self.assertEqual(done['status'], 'SIMULATION_SUCCEEDED')
        for t in done['tasks']:
            if t['task_id'] in prior:
                self.assertEqual(t['result_ref'], prior[t['task_id']])
                self.assertEqual(t['attempt'], 1)

    def test_quota_pause_no_rotation(self):
        run = 'run_' + __import__('hashlib').sha256(b'one').hexdigest()[:24]
        fake = FakeAdapter('gemini', scripts={run + '_source_index': {'outcome': 'quota_exhausted'}})
        q = self.coordinator(adapters={'gemini': fake, 'codex': FakeAdapter('codex')})
        s = q.create({'topic': 'x'}, 'one')
        done = q.advance(s['run_id'], 4)
        self.assertEqual(done['tasks'][0]['status'], 'WAITING_QUOTA')
        snapshot = q.queue.snapshot()
        self.assertFalse(snapshot['rotation_on_quota'])
        self.assertEqual(snapshot['selected_profiles']['gemini'], 'gemini-1')
        self.assertEqual(len(snapshot['attempts']), 1)

    def test_production_is_fail_closed(self):
        q = self.coordinator()
        s = q.create({'topic': 'x', 'production_mode': 'production'}, 'live')
        self.assertEqual(q.advance(s['run_id'])['status'], 'BLOCKED_UNSUPPORTED')
        self.assertEqual(q.queue.snapshot()['attempts'], [])

    def test_active_db_cannot_be_synced(self):
        with self.assertRaises(ValueError): local_root(self.root / 'OneDrive' / 'db')

    def test_legacy_queue_not_repurposed(self):
        from app.multi_ai import Coordinator
        with Coordinator(self.root) as q: q.enqueue({'task_id': 'old'})
        with self.assertRaises(ValueError): RunCoordinator(self.root)

    def test_no_traversal_or_secret_in_context(self):
        q = self.coordinator()
        with self.assertRaises(ValidationError): q.create({'topic': 'x'}, '../evil')
        s = q.create({'topic': 'Bearer synthetic.value'}, 'one')
        paths = list((self.root / 'runs' / s['run_id'] / 'context').glob('*/task.md'))
        self.assertTrue(all('synthetic.value' not in p.read_text(encoding='utf-8') for p in paths))

    def test_artifact_hash_and_drive_copy(self):
        store = ArtifactStore(self.root)
        artifact = store.put_json('run1', {'value': 1}, simulated=True)
        drive = Path(self.temp.name) / 'drive'; drive.mkdir()
        receipt = store.copy_to_drive(artifact, drive)
        self.assertFalse(receipt['uploaded'])
        self.assertEqual(receipt['remote_sync'], 'UNVERIFIED')
        self.assertEqual(store.copy_to_drive(artifact, drive), receipt)
        (drive / artifact.path).write_text('corrupt')
        with self.assertRaises(ValueError): store.copy_to_drive(artifact, drive)

    def test_corrupt_and_missing_artifacts(self):
        store = ArtifactStore(self.root)
        artifact = store.put_json('run1', {'x': 1}, simulated=True)
        p = store.verify(artifact); p.write_text('bad')
        with self.assertRaises(ValueError): store.verify(artifact)
        p.unlink()
        with self.assertRaises(ValueError): store.verify(artifact)

    def test_drive_unavailable_and_path_allowlist(self):
        store = ArtifactStore(self.root)
        a = store.put_json('run1', {}, simulated=True)
        with self.assertRaises(ValueError): store.copy_to_drive(a, Path(self.temp.name) / 'absent')
        with self.assertRaises((ValueError, StudioError)): store.put_json('../../outside', {}, simulated=True)

    def test_strict_contracts_and_honest_review(self):
        with self.assertRaises(ValidationError): Brief(topic='x', unexpected=1)
        with self.assertRaises(ValidationError): Brief(topic='x', auto_fix_cycles=2)
        with self.assertRaises(ValidationError):
            Review(review_mode='full_video', verdict='pass', materials_actually_reviewed=['frames'], issues=[],
                   full_video_probe_verified=True)
        self.assertEqual(issue_owner('canon'), 'gemini_story')
        self.assertEqual(issue_owner('mlt'), 'codex')

    def test_remote_entrypoint_process_restart(self):
        repo = Path(__file__).resolve().parents[1]
        env = {**os.environ, 'LOCAL_RUN_ROOT': str(self.root), 'GITHUB_RUN_ID': '42',
               'VIDEO_REQUEST_JSON': json.dumps({'topic': 'Literal $(exit) ; no shell', 'production_mode': 'fake'})}
        summaries = []
        for _ in range(2):
            p = subprocess.run([sys.executable, '-X', 'utf8', 'scripts/dispatch_multi_ai_v2.py'],
                               cwd=repo, env=env, capture_output=True, text=True, encoding='utf-8', timeout=40)
            self.assertEqual(p.returncode, 0, p.stderr)
            summaries.append(json.loads(p.stdout))
        self.assertEqual(summaries[0], summaries[1])
        self.assertEqual(summaries[0]['status'], 'SIMULATION_SUCCEEDED')
        with Registry(self.root) as registry:
            self.assertEqual(registry.db.execute('SELECT COUNT(*) FROM ai_attempts').fetchone()[0], len(STAGES))

    def test_single_active_run_and_terminal_release(self):
        q = self.coordinator()
        first = q.create({'topic': 'A'}, 'A')
        with self.assertRaises(StudioError) as caught: q.create({'topic': 'B'}, 'B')
        self.assertEqual(caught.exception.code, 'ACTIVE_RUN_EXISTS')
        q.advance(first['run_id'], 30)
        second = q.create({'topic': 'B'}, 'B')
        self.assertNotEqual(first['run_id'], second['run_id'])
        with self.assertRaises(StudioError) as caught: q.advance(first['run_id'])
        self.assertEqual(caught.exception.code, 'RUN_NOT_ACTIVE')

    def test_pause_and_stop_cannot_control_another_run(self):
        q = self.coordinator()
        old = q.create({'topic': 'old'}, 'old'); q.advance(old['run_id'], 30)
        current = q.create({'topic': 'current'}, 'current'); q.advance(current['run_id'], 1)
        for command in (q.advance, q.pause, q.stop):
            before = q.queue.snapshot()['control']
            with self.assertRaises(StudioError): command(old['run_id'])
            self.assertEqual(q.queue.snapshot()['control'], before)
        q.pause(current['run_id'])
        self.assertEqual(q.queue.snapshot()['control'], 'paused')
        q.stop(current['run_id'])
        self.assertEqual(q.queue.snapshot()['control'], 'stopped')

    def test_restart_retains_active_run_id(self):
        q = self.coordinator(); first = q.create({'topic': 'A'}, 'A'); q.close()
        q = self.coordinator()
        with Registry(self.root) as registry:
            self.assertEqual(registry.db.execute('SELECT active_run_id FROM ai_v2_control').fetchone()[0], first['run_id'])
        with self.assertRaises(StudioError): q.create({'topic': 'B'}, 'B')

    def test_concurrent_create_has_one_owner(self):
        from concurrent.futures import ThreadPoolExecutor
        q=self.coordinator()
        def create(index):
            try: return q.create({'topic':str(index)},'run'+str(index))['run_id']
            except StudioError as exc: return exc.code
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(create,range(4)))
        owners=[x for x in results if x!='ACTIVE_RUN_EXISTS']
        self.assertEqual(len(owners),1)
        with Registry(self.root) as registry:
            self.assertEqual(registry.db.execute('SELECT active_run_id FROM ai_v2_control').fetchone()[0],owners[0])

    def test_trusted_review_rejects_self_attestation_and_missing_probe(self):
        q = self.coordinator()
        artifact=q.store.put_json('video_run',{},simulated=False)
        value = render_review(artifact)
        binding = dict(provider='gemini', client_version='1', model='explicit', control_video_hash='1'*64)
        with self.assertRaises(StudioError): q.accept_review(value, **binding)
        with self.assertRaises(ValidationError):
            q.accept_review({**value, 'full_video_probe_verified': True}, **binding)

    def test_artifact_run_and_result_provenance(self):
        artifact = ArtifactStore(self.root).put_json('runA', {'x': 1}, simulated=True)
        base = dict(task_id='taskA', run_id='runA', acceptance_key='attemptA', expected_output_schema='note',
                    payload={'text':'fixture','simulated':True}, input_version=1, input_hashes={}, artifacts=[artifact], simulated=True)
        Result(**base)
        for change in ({'run_id':'runB'}, {'simulated':False}):
            with self.assertRaises(ValidationError): Result(**{**base, **change})
        real = Artifact(**{**artifact.model_dump(), 'simulated':False})
        with self.assertRaises(ValidationError): Result(**{**base,'artifacts':[real]})
        with self.assertRaises(ValidationError):
            Artifact(**{**artifact.model_dump(),'path':artifact.path.replace('runA','runB')})

    def test_artifact_symlink_escape(self):
        store = ArtifactStore(self.root)
        artifact = store.put_json('runA', {'x':1}, simulated=True)
        target = store.verify(artifact)
        other = self.root/'runs/runB'; other.mkdir(parents=True)
        (other/'file.json').write_bytes(target.read_bytes())
        link = self.root/'runs/runA/escape'
        try: link.symlink_to(other, target_is_directory=True)
        except OSError:
            if os.name != 'nt': raise
            import _winapi
            _winapi.CreateJunction(str(other), str(link))  # No global privilege change.
        try:
            forged = Artifact(**{**artifact.model_dump(),'path':'runs/runA/escape/file.json'})
            with self.assertRaises((ValueError,StudioError)): store.verify(forged)
            self.assertTrue(target.is_file())
        finally:
            if link.is_symlink(): link.unlink()
            else: link.rmdir()  # Remove only the test-created junction, never its target.

    def test_existing_artifact_is_not_overwritten(self):
        store = ArtifactStore(self.root)
        artifact = store.put_json('runA', {'x':1}, simulated=True)
        path = store.verify(artifact); path.write_text('manual conflict')
        with self.assertRaises(ValueError): store.put_json('runA', {'x':1}, simulated=True)
        self.assertEqual(path.read_text(), 'manual conflict')

    def test_drive_fallback_and_partial_cleanup(self):
        store = ArtifactStore(self.root); a = store.put_json('runA', {'x':1}, simulated=True)
        drive = Path(self.temp.name)/'drive'; drive.mkdir()
        with patch('app.v2_storage.os.link', side_effect=OSError('hardlinks unsupported')):
            receipt = store.copy_to_drive(a, drive)
        self.assertEqual(receipt['remote_sync'],'UNVERIFIED')
        destination=drive/a.path
        destination.write_text('existing manual artifact')
        with patch('app.v2_storage.os.link', side_effect=OSError('hardlinks unsupported')):
            with self.assertRaises(ValueError): store.copy_to_drive(a, drive)
        self.assertEqual(destination.read_text(),'existing manual artifact')
        # Only our test-created destination is removed, to simulate a fresh attempt.
        destination.unlink()
        import shutil
        original=shutil.copyfileobj; calls=0
        def break_fallback(src,dst,*args):
            nonlocal calls
            calls+=1
            if calls==2: dst.write(b'partial'); raise OSError('copy failed')
            return original(src,dst,*args)
        with patch('app.v2_storage.os.link',side_effect=OSError('no hardlink')), patch('app.v2_storage.shutil.copyfileobj',side_effect=break_fallback):
            with self.assertRaises(OSError): store.copy_to_drive(a,drive)
        self.assertFalse(destination.exists())
        self.assertEqual(list(drive.rglob('*.tmp')),[])

    def test_schema_drift_without_rewriting_sources(self):
        from app.editorial import Scene, EditPlan
        from app.editorial import Review as CanonicalReview
        from app.ai_cli_runtime import output_schema
        self.assertIs(Review, CanonicalReview)
        self.assertEqual(Review.model_json_schema(), output_schema('review'))
        repo=Path(__file__).resolve().parents[1]
        checked=repo/'config/multi_ai_v2_schemas'
        before={p.name:p.read_bytes() for p in checked.glob('*.json')}
        generated=Path(self.temp.name)/'schemas'; generated.mkdir()
        for name,model in {'brief':Brief,'task':Task,'result':Result,'artifact':Artifact,'review':Review,'scene':Scene,'edit_plan':EditPlan}.items():
            path=generated/(name+'.schema.json')
            path.write_text(json.dumps(model.model_json_schema(),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            self.assertEqual(json.loads(path.read_text()),json.loads(before[path.name]))
        self.assertEqual(before,{p.name:p.read_bytes() for p in checked.glob('*.json')})

    def test_full_source_binding_scope(self):
        root=Path(self.temp.name)/'source'; root.mkdir()
        paths=['app/nested/a.py','scripts/deep/a.py','tests/deep/a.py','scripts/deep/tool.ps1',
               'config/multi_ai_v2_schemas/sub/one.json','pyproject.toml','.env.example','config/production_profile.json']
        for name in paths:
            p=root/name; p.parent.mkdir(parents=True,exist_ok=True);p.write_text('initial')
        initial=source_binding(root)
        self.assertEqual(set(initial['source_hashes']),set(paths))
        for name in paths:
            p=root/name;p.write_text('changed')
            self.assertNotEqual(source_binding(root)['source_manifest_sha256'],initial['source_manifest_sha256'])
            p.write_text('initial')
        schema=root/paths[4];schema.write_text('schema change')
        self.assertNotEqual(source_binding(root)['schema_manifest_sha256'],initial['schema_manifest_sha256'])

    def test_repeated_immutable_checker_and_probe(self):
        repo=Path(__file__).resolve().parents[1]; parent=Path(self.temp.name)/'evidence'
        for script,extra in [('check_multi_ai_v2.py',['--groups','adapters']),
                             ('probe_multi_ai_v2.py',['--offline','--codex-home',str(self.root)])]:
            snapshots=[]
            for _ in range(2):
                p=subprocess.run([sys.executable,'-X','utf8','scripts/'+script,'--output',str(parent),*extra],
                                 cwd=repo,capture_output=True,text=True,encoding='utf-8',timeout=60)
                self.assertEqual(p.returncode,0,p.stderr+p.stdout)
                last=json.loads(p.stdout.splitlines()[-1]); folder=Path(last['evidence_directory'])
                self.assertEqual(hashlib.sha256((folder/'manifest.json').read_bytes()).hexdigest(),last['manifest_sha256'])
                snapshots.append((folder,{str(f):f.read_bytes() for f in folder.rglob('*') if f.is_file()}))
            self.assertNotEqual(snapshots[0][0],snapshots[1][0])
            for name,data in snapshots[0][1].items():self.assertEqual(Path(name).read_bytes(),data)
            manifest=json.loads((snapshots[1][0]/'manifest.json').read_text())
            for name,item in manifest['files'].items():
                self.assertEqual(hashlib.sha256((snapshots[1][0]/name).read_bytes()).hexdigest(),item['sha256'])
            self.assertEqual(manifest['model_requests'],0)

    def test_cli_exit_status_and_run_ownership(self):
        repo=Path(__file__).resolve().parents[1];brief=Path(self.temp.name)/'brief.json'
        brief.write_text(json.dumps({'topic':'A'}))
        def call(*args):
            p=subprocess.run([sys.executable,'-X','utf8','-m','app.multi_ai_v2','--root',str(self.root),*args],
                             cwd=repo,capture_output=True,text=True,encoding='utf-8',timeout=40)
            return p.returncode,json.loads(p.stdout)
        code,created=call('create','--brief',str(brief),'--key','A')
        self.assertEqual((code,created['status']),(2,'IN_PROGRESS')); run_id=created['run_id']
        for command in ('pause','stop','status'):
            code,r=call(command,'--run-id',run_id);self.assertEqual((code,r['status']),(2,'IN_PROGRESS'))
        code,r=call('advance','--run-id','unknown');self.assertEqual((code,r['status']),(9,'ERROR'))
        code,r=call('advance','--run-id',run_id,'--ticks','60');self.assertEqual((code,r['status']),(0,'SIMULATION_SUCCEEDED'))
        code,r=call('manifest','--run-id',run_id);self.assertEqual((code,r['status']),(0,'SIMULATION_SUCCEEDED'))

    def test_fake_and_production_healthcheck_modes(self):
        repo=Path(__file__).resolve().parents[1]
        for mode,expected in [('fake',0),('production',5)]:
            p=subprocess.run([sys.executable,'-X','utf8','scripts/healthcheck_multi_ai_v2.py','--mode',mode,'--root',str(self.root)],
                             cwd=repo,capture_output=True,text=True,encoding='utf-8',timeout=40)
            self.assertEqual(p.returncode,expected,p.stderr+p.stdout)
            r=json.loads(p.stdout);self.assertEqual(r['model_requests'],0)
        from scripts.healthcheck_multi_ai_v2 import check
        for root in (repo,repo/'workspace/studio',self.root/'OneDrive/db'):
            self.assertEqual(check('fake',root)['status'],'ERROR')

    def test_cli_non_success_statuses_are_nonzero(self):
        repo=Path(__file__).resolve().parents[1]
        cases=[('waiting_access','authentication','WAITING_AUTH',3),
               ('waiting_access','quota_exhausted','WAITING_QUOTA',4),
               ('interrupted','interrupted','FAILED_RETRYABLE',6),
               ('failed','invalid_input','FAILED_FINAL',7),('stale','stale_inputs','STALE',8)]
        for index,(legacy,kind,expected,code) in enumerate(cases):
            root=Path(self.temp.name)/('case'+str(index))
            with RunCoordinator(root) as q:
                run=q.create({'topic':'status fixture'},'case')['run_id']
                with Registry(root) as registry:
                    registry.db.execute('UPDATE ai_tasks SET status=?,error=? WHERE id=?',
                        (legacy,json.dumps({'kind':kind}),run+'_source_index'))
            p=subprocess.run([sys.executable,'-X','utf8','-m','app.multi_ai_v2','--root',str(root),'status','--run-id',run],
                             cwd=repo,capture_output=True,text=True,encoding='utf-8',timeout=30)
            self.assertEqual((p.returncode,json.loads(p.stdout)['status']),(code,expected),p.stderr)


class ReviewFixTests(unittest.TestCase):
    setUp = V2Tests.setUp
    coordinator = V2Tests.coordinator
    def result_fixture(self):
        from app.editorial import demo_result
        q=self.coordinator()
        run=q.create({'topic':'acceptance fixture'},'accept')['run_id']
        q.advance(run,1)
        with Registry(self.root) as registry:
            row=registry.db.execute('SELECT * FROM ai_tasks WHERE id=?',(run+'_source_index',)).fetchone()
            task=q.queue._task(row)
        contract=task['context']['v2_contract']
        payload=demo_result('note',task['context'])
        artifact=q.store.put_json(run,payload,simulated=True)
        result=dict(task_id=task['task_id'],run_id=run,acceptance_key=task['current_attempt_id'],
                    expected_output_schema='note',payload=payload,input_version=contract['input_version'],
                    input_hashes=contract['input_hashes'],artifacts=[artifact.model_dump()],simulated=True)
        return q,result

    def test_result_acceptance_uses_queue_cas_and_is_idempotent_after_restart(self):
        q,value=self.result_fixture()
        accepted=q.accept_result(value)
        self.assertEqual(q.accept_result(value),accepted)
        q.close();q=self.coordinator()
        self.assertEqual(q.accept_result(value),accepted)
        with Registry(self.root) as registry:
            self.assertEqual(registry.db.execute('SELECT COUNT(*) FROM ai_artifacts').fetchone()[0],1)
            self.assertEqual(registry.db.execute('SELECT status FROM ai_tasks WHERE id=?',(value['task_id'],)).fetchone()[0],'completed')
            self.assertIsNone(registry.db.execute("SELECT name FROM sqlite_master WHERE name='ai_v2_results'").fetchone())
        changed=deepcopy(value);changed['payload']['text']='different valid note'
        with self.assertRaises(StudioError) as caught:q.accept_result(changed)
        self.assertEqual(caught.exception.code,'IDEMPOTENCY_CONFLICT')

    def test_result_rejects_unknown_and_foreign_registered_tasks(self):
        q,value=self.result_fixture()
        with self.assertRaises(StudioError) as caught:q.accept_result({**value,'task_id':'unknown'})
        self.assertEqual(caught.exception.code,'TASK_NOT_FOUND')
        # Both runs are registered: the old task cannot be relabelled as the new run.
        q.advance(value['run_id'],30)
        other_run=q.create({'topic':'other run'},'other')['run_id']
        other=q.store.put_json(other_run,value['payload'],simulated=True)
        foreign={**value,'run_id':other_run,'artifacts':[other.model_dump()]}
        with self.assertRaises(StudioError) as caught:q.accept_result(foreign)
        self.assertEqual(caught.exception.code,'RESULT_RUN_MISMATCH')

    def test_result_rejects_stale_version_hash_schema_and_attempt(self):
        q,value=self.result_fixture()
        cases=[({'input_version':2},'STALE_RESULT'),({'input_hashes':{'brief':'0'*64}},'STALE_RESULT'),
               ({'expected_output_schema':'scene'},'SCHEMA_INVALID'),({'acceptance_key':'old_attempt'},'RESULT_NOT_CURRENT')]
        for changes,code in cases:
            with self.subTest(changes=changes),self.assertRaises(StudioError) as caught:q.accept_result({**value,**changes})
            self.assertEqual(caught.exception.code,code)
        with Registry(self.root) as registry:registry.db.execute("UPDATE ai_tasks SET status='stale' WHERE id=?",(value['task_id'],))
        with self.assertRaises(StudioError) as caught:q.accept_result(value)
        self.assertEqual(caught.exception.code,'STALE_RESULT')

    def test_result_rejects_provenance_foreign_artifact_and_tamper(self):
        q,value=self.result_fixture()
        live=deepcopy(value);live['simulated']=False;live['payload']['simulated']=False
        live['artifacts'][0]['simulated']=False
        with self.assertRaises(StudioError):q.accept_result(live)
        foreign=q.store.put_json('other',{},simulated=True)
        with self.assertRaises(ValidationError):q.accept_result({**value,'artifacts':[foreign.model_dump()]})
        q.store.verify(Artifact.model_validate(value['artifacts'][0])).write_text('tampered')
        with self.assertRaises(ValueError):q.accept_result(value)
        with Registry(self.root) as registry:
            self.assertEqual(registry.db.execute('SELECT status FROM ai_tasks WHERE id=?',(value['task_id'],)).fetchone()[0],'running')

    def test_result_invalid_payload_never_passes_queue_validation(self):
        q,value=self.result_fixture()
        with self.assertRaises(ValueError):q.accept_result({**value,'payload':{'simulated':True,'unknown':1}})

    def video_fixture(self):
        from app.v2_video import CONTROL_FEATURES
        self.now=datetime(2026,10,20,12,tzinfo=timezone.utc)
        self.host=Path(self.temp.name)/'host_evidence';self.host.mkdir()
        q=self.coordinator(video_evidence_root=self.host,clock=lambda:self.now)
        binding=dict(provider='gemini',provider_profile_id='visual-1',account_fingerprint='a'*64,
                     client='fixture',client_version='1',requested_model='requested',returned_model='returned',control_video_hash='1'*64)
        receipt={'evidence_id':'evidence1','binding':binding,'expected_features':deepcopy(CONTROL_FEATURES),
                 'observed_features':deepcopy(CONTROL_FEATURES)}
        self.receipt=self.host/'receipt.json';self.receipt.write_text(json.dumps(receipt),encoding='utf-8')
        probe=dict(evidence_id='evidence1',**binding,result_hash=hashlib.sha256(self.receipt.read_bytes()).hexdigest(),
                   checked_at=self.now.isoformat(),expires_at=(self.now+timedelta(hours=1)).isoformat(),
                   status='VERIFIED',evidence_refs=['receipt.json'])
        # Opaque temporary delivery fixture; no decoding, model call or creative QA.
        path=self.root/'runs/video_run/artifacts/video.webm';path.parent.mkdir(parents=True)
        raw=b'opaque video delivery fixture';path.write_bytes(raw)
        artifact=Artifact(artifact_id='video1',run_id='video_run',version=1,path='runs/video_run/artifacts/video.webm',
                          sha256=hashlib.sha256(raw).hexdigest(),size=len(raw),simulated=False)
        review=render_review(artifact)
        kwargs=dict(evidence_id=probe['evidence_id'],video_artifact=artifact,**binding)
        return q,probe,review,kwargs

    def assert_unverified(self,q,review,kwargs):
        with self.assertRaises(StudioError) as caught:q.accept_review(review,**kwargs)
        self.assertEqual(caught.exception.code,'VIDEO_CAPABILITY_UNVERIFIED')

    def test_video_probe_immutable_idempotent_and_revocable(self):
        q,probe,review,kwargs=self.video_fixture()
        self.assert_unverified(q,review,kwargs)
        self.assertEqual(q.record_video_probe(probe),'evidence1')
        self.assertEqual(q.record_video_probe(probe),'evidence1')
        self.assertEqual(q.accept_review(review,**kwargs).evidence_mode,'video_audio')
        for changes in ({'status':'FAILED'},{'client_version':'2'},{'result_hash':'3'*64}):
            with self.assertRaises(StudioError) as caught:q.record_video_probe({**probe,**changes})
            self.assertEqual(caught.exception.code,'EVIDENCE_CONFLICT')
        q.revoke_video_probe('evidence1','host revoked');self.assert_unverified(q,review,kwargs)
        with Registry(self.root) as registry:
            self.assertEqual(json.loads(registry.db.execute('SELECT evidence FROM ai_v2_capabilities').fetchone()[0]),probe)

    def test_video_probe_checks_all_binding_fields(self):
        q,probe,review,kwargs=self.video_fixture();q.record_video_probe(probe)
        for field in ('provider','provider_profile_id','account_fingerprint','client','client_version',
                      'requested_model','returned_model','control_video_hash'):
            self.assert_unverified(q,review,{**kwargs,field:'different'})

    def test_video_evidence_root_must_not_overlap_run_storage(self):
        for path in (self.root,self.root/'host',self.root.parent):
            with self.assertRaises(ValueError):RunCoordinator(self.root,video_evidence_root=path)
        # Failed configuration must not acquire/leak the queue ownership lock.
        self.coordinator()

    def test_video_probe_time_limits_and_expiry_with_injected_clock(self):
        q,probe,review,kwargs=self.video_fixture()
        q.video_evidence.validate({**probe,'checked_at':(self.now+timedelta(seconds=30)).isoformat()})
        cases=[{'checked_at':self.now.replace(tzinfo=None).isoformat()},
               {'expires_at':self.now.replace(tzinfo=None).isoformat()},
               {'checked_at':(self.now+timedelta(seconds=61)).isoformat()},
               {'expires_at':self.now.isoformat()},
               {'expires_at':(self.now+timedelta(hours=24,seconds=1)).isoformat()}]
        for changes in cases:
            with self.subTest(changes=changes),self.assertRaises(ValueError):q.record_video_probe({**probe,**changes})
        q.record_video_probe(probe)
        self.now+=timedelta(hours=1)
        self.assert_unverified(q,review,kwargs)
        with self.assertRaises(ValueError):q.record_video_probe(probe)

    def test_video_probe_missing_root_receipt_hash_and_challenge_fail_closed(self):
        q,probe,review,kwargs=self.video_fixture()
        q.video_evidence.root=None
        with self.assertRaises(ValueError):q.record_video_probe(probe)
        q.video_evidence.root=self.host
        with self.assertRaises(ValueError):q.record_video_probe({**probe,'result_hash':'2'*64})
        data=json.loads(self.receipt.read_text());data['observed_features']['text']='incorrect'
        self.receipt.write_text(json.dumps(data))
        changed={**probe,'result_hash':hashlib.sha256(self.receipt.read_bytes()).hexdigest()}
        with self.assertRaises(ValueError):q.record_video_probe(changed)
        data['expected_features']=data['observed_features']
        self.receipt.write_text(json.dumps(data))
        with self.assertRaises(ValueError):q.record_video_probe({**probe,'result_hash':hashlib.sha256(self.receipt.read_bytes()).hexdigest()})

    def test_video_probe_requires_bound_receipt_and_detects_later_tamper(self):
        q,probe,review,kwargs=self.video_fixture()
        data=json.loads(self.receipt.read_text());original=self.receipt.read_bytes()
        data['binding']['provider_profile_id']='wrong'
        self.receipt.write_text(json.dumps(data))
        with self.assertRaises(ValueError):q.record_video_probe({**probe,'result_hash':hashlib.sha256(self.receipt.read_bytes()).hexdigest()})
        self.receipt.write_bytes(original);q.record_video_probe(probe)
        self.receipt.write_bytes(original+b' ')
        self.assert_unverified(q,review,kwargs)

    def test_video_receipt_must_be_existing_regular_file_with_safe_reference(self):
        q,probe,review,kwargs=self.video_fixture()
        for refs,error in ((['../receipt.json'],ValueError),(['missing.json'],FileNotFoundError),
                           (['.'],ValueError),(['receipt.json','other.json'],ValueError)):
            with self.subTest(refs=refs),self.assertRaises(error):
                q.record_video_probe({**probe,'evidence_refs':refs})
        (self.host/'directory').mkdir()
        with self.assertRaises(ValueError):q.record_video_probe({**probe,'evidence_refs':['directory']})

    def test_video_probe_rejects_symlink_or_junction_escape(self):
        q,probe,review,kwargs=self.video_fixture()
        outside=Path(self.temp.name)/'outside';outside.mkdir()
        (outside/'receipt.json').write_bytes(self.receipt.read_bytes())
        link=self.host/'escape'
        try:link.symlink_to(outside,target_is_directory=True)
        except OSError:
            if os.name!='nt':raise
            import _winapi
            _winapi.CreateJunction(str(outside),str(link))
        try:
            with self.assertRaises(ValueError):q.record_video_probe({**probe,'evidence_refs':['escape/receipt.json']})
        finally:
            if link.is_symlink():link.unlink()
            else:link.rmdir()

    def test_video_review_requires_real_material_and_never_self_attests(self):
        q,probe,review,kwargs=self.video_fixture();q.record_video_probe(probe)
        self.assert_unverified(q,{**review,'subject_hash':'f'*64,'render_hash':'f'*64},kwargs)
        self.assert_unverified(q,review,{**kwargs,'video_artifact':None})
        simulated=kwargs['video_artifact'].model_copy(update={'simulated':True})
        self.assert_unverified(q,review,{**kwargs,'video_artifact':simulated})
        with self.assertRaises(ValidationError):q.accept_review({**review,'receipt':probe},**kwargs)
        q.store.verify(kwargs['video_artifact']).write_bytes(b'changed')
        self.assert_unverified(q,review,kwargs)

    def test_unverified_failed_expired_status_never_grants_capability(self):
        q,probe,review,kwargs=self.video_fixture()
        for status in ('UNVERIFIED','FAILED','EXPIRED'):
            evidence_id='evidence_'+status.lower()
            q.record_video_probe({**probe,'evidence_id':evidence_id,'status':status})
            self.assert_unverified(q,review,{**kwargs,'evidence_id':evidence_id})

    def test_dispatch_subprocess_all_status_exit_codes(self):
        from app.multi_ai_v2 import EXIT_CODES
        # Child-only trusted fixture replaces the facade; production has no test hooks.
        code='''import json, os, runpy
from unittest.mock import patch
status=os.environ['FIXTURE_STATUS']
state={'run_id':'fixture','status':status,'simulated':True,'ready_for_user':False,'blockers':[]}
with patch('app.multi_ai_v2.RunCoordinator') as factory:
    q=factory.return_value.__enter__.return_value
    q.create.return_value=state
    q.advance.return_value=state
    q.manifest.return_value={'sha256':'a'*64}
    if status=='ERROR': q.create.side_effect=ValueError('do not disclose input')
    runpy.run_path('scripts/dispatch_multi_ai_v2.py',run_name='__main__')
'''
        for status in ('SIMULATION_SUCCEEDED','IN_PROGRESS','WAITING_AUTH','WAITING_QUOTA',
                       'BLOCKED_UNSUPPORTED','FAILED_RETRYABLE','FAILED_FINAL','STALE','ERROR'):
            env={**os.environ,'LOCAL_RUN_ROOT':str(self.root),'GITHUB_RUN_ID':'42',
                 'VIDEO_REQUEST_JSON':'{"topic":"fixture"}','FIXTURE_STATUS':status}
            env.pop('GITHUB_STEP_SUMMARY',None)
            p=subprocess.run([sys.executable,'-X','utf8','-c',code],cwd=Path(__file__).resolve().parents[1],
                             env=env,capture_output=True,text=True,encoding='utf-8',timeout=30)
            self.assertEqual((p.returncode,json.loads(p.stdout)['status']),(EXIT_CODES[status],status),p.stderr)
            self.assertNotIn('do not disclose',p.stdout)


class UnifiedAcceptanceTests(unittest.TestCase):
    setUp = V2Tests.setUp
    coordinator = V2Tests.coordinator

    def live_fixture(self):
        from app.editorial import Coverage
        from app.v2_acceptance import fingerprint
        from app.v2_video import CONTROL_FEATURES
        self.now=datetime(2026,10,20,12,tzinfo=timezone.utc)
        self.host=Path(self.temp.name)/'host';self.host.mkdir(exist_ok=True)
        q=self.coordinator(video_evidence_root=self.host,clock=lambda:self.now)
        run=q.create({'topic':'offline delivery fixture'},'delivery')['run_id']
        q.advance(run,1)
        task_id=run+'_source_index'
        with Registry(self.root) as registry:
            row=registry.db.execute('SELECT * FROM ai_tasks WHERE id=?',(task_id,)).fetchone()
            attempt=dict(registry.db.execute('SELECT * FROM ai_attempts WHERE id=?',(row['current_attempt'],)).fetchone())
            task=json.loads(row['payload'])
            task['expected_output']='review';task['execution_mode']='live'
            contract=task['context']['v2_contract']
            contract.update(expected_output_schema='review',provider_profile_id=attempt['profile_id'],requested_model='requested')
            task['context'].update(edit_plan_version=1,subject_version=1)
            registry.db.execute('UPDATE ai_tasks SET payload=? WHERE id=?',(json.dumps(task),task_id))
            registry.db.execute("UPDATE ai_v2_runs SET mode='production' WHERE run_id=?",(run,))
        request_path=self.root/Path(attempt['result_path']).with_name('request.json')
        request=json.loads(request_path.read_text());request['context']=deepcopy(task['context'])
        request['expected_output']='review';request['execution_mode']='live'
        request['account_binding']={'identity_hash':'a'*64,'auth_generation':1}
        request_path.write_text(json.dumps(request),encoding='utf-8')
        path=self.root/f'runs/{run}/artifacts/preview.webm';path.parent.mkdir(parents=True)
        # Opaque bytes only: no MP4, media generation, decoding or real video review.
        raw=b'opaque delivery integrity fixture';path.write_bytes(raw)
        artifact=Artifact(artifact_id='render1',run_id=run,version=1,path=path.relative_to(self.root).as_posix(),
            sha256=hashlib.sha256(raw).hexdigest(),size=len(raw),simulated=False)
        coverage=Coverage(start_ms=0,end_ms=1000,tracks=['video','audio']).model_dump()
        review=render_review(artifact)
        review.update(verdict='ready_for_review',provided_coverage=[coverage],reviewed_coverage=[coverage])
        binding=dict(provider=attempt['provider'],provider_profile_id=attempt['profile_id'],account_fingerprint=fingerprint(request['account_binding']),
            client='fixture',client_version='1',requested_model='requested',returned_model='returned',control_video_hash='1'*64)
        cap={'evidence_id':'cap1','binding':binding,'expected_features':CONTROL_FEATURES,'observed_features':CONTROL_FEATURES}
        cap_path=self.host/'cap.json';cap_path.write_text(json.dumps(cap),encoding='utf-8')
        q.record_video_probe(dict(evidence_id='cap1',**binding,result_hash=hashlib.sha256(cap_path.read_bytes()).hexdigest(),
            checked_at=self.now.isoformat(),expires_at=(self.now+timedelta(hours=1)).isoformat(),status='VERIFIED',evidence_refs=['cap.json']))
        delivery=dict(delivery_id='delivery1',task_id=task_id,run_id=run,attempt_id=attempt['id'],evidence_id='cap1',**binding,
            video_artifact_id=artifact.artifact_id,video_artifact_path=artifact.path,video_artifact_sha256=artifact.sha256,
            video_artifact_size=artifact.size,input_version=contract['input_version'],input_hashes=contract['input_hashes'],
            edit_plan_version=1,delivered_coverage=[coverage],review_payload_sha256=fingerprint(Review.model_validate(review).model_dump()),
            requested_at=(self.now-timedelta(seconds=10)).isoformat(),completed_at=self.now.isoformat(),
            expires_at=(self.now+timedelta(minutes=30)).isoformat(),receipt_ref='delivery.json',receipt_sha256='0'*64)
        self.sign_delivery(delivery)
        envelope=Result(task_id=task_id,run_id=run,acceptance_key=attempt['id'],expected_output_schema='review',
            payload=review,input_version=contract['input_version'],input_hashes=contract['input_hashes'],
            artifacts=[artifact],simulated=False,host_review_delivery=delivery).model_dump()
        class LiveFixture:
            is_live=True
            is_simulated=False
            def start(self,*args):raise AssertionError('No live worker may start')
            def poll(self,handle):return [self.event]
            def cancel(self,handle):return None
        adapter=LiveFixture();adapter.event={'type':'completed','result':review,'host_metadata':envelope}
        q.queue.adapters[attempt['provider']]=adapter
        return q,envelope,adapter,attempt

    def sign_delivery(self,delivery):
        from app.v2_contracts import HostReviewDelivery
        delivery.update(HostReviewDelivery.model_validate(delivery).model_dump())
        body={k:v for k,v in delivery.items() if k not in {'receipt_ref','receipt_sha256'}}
        path=self.host/delivery['receipt_ref'];path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(body),encoding='utf-8')
        delivery['receipt_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()

    def assert_poll_rejected(self,q,attempt):
        q.queue._poll()
        with Registry(self.root) as registry:
            task=registry.db.execute('SELECT * FROM ai_tasks WHERE id=?',(attempt['task_id'],)).fetchone()
            self.assertEqual(task['status'],'failed',task['error'])
            self.assertIsNone(task['result'])
            self.assertEqual(registry.db.execute('SELECT status FROM ai_attempts WHERE id=?',(attempt['id'],)).fetchone()[0],'failed')
            self.assertEqual(registry.db.execute('SELECT COUNT(*) FROM ai_artifacts').fetchone()[0],0)
        self.assertFalse((self.root/attempt['result_path']).exists())

    def test_automatic_live_completion_without_host_envelope_fails_before_publication(self):
        q,value,adapter,attempt=self.live_fixture()
        adapter.event.pop('host_metadata')
        self.assert_poll_rejected(q,attempt)

    def test_model_envelope_and_evidence_fields_are_never_host_metadata(self):
        q,value,adapter,attempt=self.live_fixture()
        adapter.event['result']={**value['payload'],'VideoEvidence':{'status':'VERIFIED'},'host_review_delivery':value['host_review_delivery']}
        self.assert_poll_rejected(q,attempt)

    def test_valid_creative_review_without_trusted_delivery_is_rejected(self):
        q,value,adapter,attempt=self.live_fixture()
        value['host_review_delivery']=None
        self.assert_poll_rejected(q,attempt)

    def test_automatic_review_rejects_other_task_run_and_attempt(self):
        base=self.root
        for field in ('task_id','run_id','acceptance_key'):
            with self.subTest(field=field):
                self.root=base/field
                q,value,adapter,attempt=self.live_fixture()
                value[field]='other'
                self.assert_poll_rejected(q,attempt);q.close()

    def test_delivery_rejects_other_video_hash_even_with_new_receipt_hash(self):
        q,value,adapter,attempt=self.live_fixture()
        delivery=value['host_review_delivery'];delivery['video_artifact_sha256']='f'*64;self.sign_delivery(delivery)
        self.assert_poll_rejected(q,attempt)

    def test_valid_capability_cannot_rescue_expired_delivery(self):
        q,value,adapter,attempt=self.live_fixture()
        delivery=value['host_review_delivery']
        delivery.update(requested_at=(self.now-timedelta(minutes=3)).isoformat(),
            completed_at=(self.now-timedelta(minutes=2)).isoformat(),expires_at=(self.now-timedelta(seconds=1)).isoformat())
        self.sign_delivery(delivery)
        self.assert_poll_rejected(q,attempt)

    def test_valid_automatic_review_is_accepted_once_and_replays_after_restart(self):
        q,value,adapter,attempt=self.live_fixture()
        q.queue._poll()
        with Registry(self.root) as registry:
            row=registry.db.execute('SELECT * FROM ai_tasks WHERE id=?',(attempt['task_id'],)).fetchone()
            self.assertEqual(row['status'],'completed')
            accepted=json.loads(row['result'])
            self.assertEqual(accepted['v2_artifacts'],value['artifacts'])
            self.assertEqual(accepted['v2_host_delivery'],value['host_review_delivery'])
            self.assertEqual(registry.db.execute('SELECT COUNT(*) FROM ai_artifacts').fetchone()[0],1)
        q.queue._runs[attempt['id']]=(attempt['provider'],'duplicate_event');q.queue._poll()
        q.close();q=self.coordinator(video_evidence_root=self.host,clock=lambda:self.now)
        self.assertEqual(q.accept_result(value),accepted['v2_acceptance'])
        self.assertEqual(q.accepted_artifacts(value['task_id']),value['artifacts'])
        changed=deepcopy(value);changed['artifacts'].append(q.store.put_json(value['run_id'],{'fixture':1},simulated=False).model_dump())
        with self.assertRaises(StudioError) as caught:q.accept_result(changed)
        self.assertEqual(caught.exception.code,'IDEMPOTENCY_CONFLICT')
        changed=deepcopy(value);changed['payload']['review_id']='another_review'
        with self.assertRaises(StudioError) as caught:q.accept_result(changed)
        self.assertEqual(caught.exception.code,'IDEMPOTENCY_CONFLICT')
        with Registry(self.root) as registry:
            self.assertEqual(registry.db.execute('SELECT COUNT(*) FROM ai_artifacts').fetchone()[0],1)

    def test_manual_review_uses_identical_delivery_and_editorial_gate(self):
        q,value,adapter,attempt=self.live_fixture()
        with patch.object(q.result_gate,'_review_context',wraps=q.result_gate._review_context) as gate:
            q.accept_result(value)
            self.assertEqual(gate.call_count,2)  # Before write and again at queue acceptance.
        self.assertEqual(q.accepted_artifacts(value['task_id']),value['artifacts'])

    def test_manual_review_with_no_delivery_cannot_publish(self):
        q,value,adapter,attempt=self.live_fixture()
        value['host_review_delivery']=None
        with self.assertRaisesRegex(StudioError,'host delivery'):q.accept_result(value)
        with Registry(self.root) as registry:
            self.assertEqual(registry.db.execute('SELECT COUNT(*) FROM ai_artifacts').fetchone()[0],0)
        self.assertFalse((self.root/attempt['result_path']).exists())

    def test_readonly_accept_review_cannot_complete_task_or_register_artifact(self):
        q,value,adapter,attempt=self.live_fixture()
        delivery=value['host_review_delivery']
        from app.v2_video import BINDING_FIELDS
        artifact=Artifact.model_validate(value['artifacts'][0])
        result=q.accept_review(value['payload'],evidence_id='cap1',video_artifact=artifact,
            **{f:delivery[f] for f in BINDING_FIELDS})
        self.assertEqual(result.evidence_mode,'video_audio')
        with Registry(self.root) as registry:
            self.assertEqual(registry.db.execute('SELECT status FROM ai_tasks WHERE id=?',(value['task_id'],)).fetchone()[0],'running')
            self.assertEqual(registry.db.execute('SELECT COUNT(*) FROM ai_artifacts').fetchone()[0],0)
        self.assertFalse((self.root/attempt['result_path']).exists())

    def test_delivery_payload_coverage_and_current_plan_are_bound(self):
        from app.v2_acceptance import fingerprint
        base=self.root
        for change in ('payload','coverage','plan','revoked','receipt','client'):
            with self.subTest(change=change):
                self.root=base/change;q,value,adapter,attempt=self.live_fixture()
                delivery=value['host_review_delivery']
                if change=='payload':
                    value['payload']['verdict']='needs_review'
                    adapter.event['result']=value['payload']
                elif change=='coverage':
                    value['payload']['reviewed_coverage'][0]['end_ms']=2000
                    delivery['review_payload_sha256']=fingerprint(value['payload']);self.sign_delivery(delivery)
                    adapter.event['result']=value['payload']
                elif change=='plan':
                    with Registry(self.root) as registry:
                        row=registry.db.execute('SELECT payload FROM ai_tasks WHERE id=?',(value['task_id'],)).fetchone()
                        task=json.loads(row[0]);task['context']['edit_plan_version']=2
                        registry.db.execute('UPDATE ai_tasks SET payload=? WHERE id=?',(json.dumps(task),value['task_id']))
                elif change=='revoked':q.revoke_video_delivery(delivery['delivery_id'],'fixture revocation')
                elif change=='receipt':(self.host/delivery['receipt_ref']).write_text('{}')
                elif change=='client':delivery['client_version']='different';self.sign_delivery(delivery)
                self.assert_poll_rejected(q,attempt);q.close()

    def test_replay_rechecks_missing_and_changed_accepted_files(self):
        q,value,adapter,attempt=self.live_fixture();q.queue._poll();q.close()
        q=self.coordinator(video_evidence_root=self.host,clock=lambda:self.now)
        path=self.root/value['artifacts'][0]['path'];raw=path.read_bytes();path.write_bytes(b'tampered')
        with self.assertRaises(ValueError):q.accept_result(value)
        path.write_bytes(raw);path.unlink()  # Only this temporary fixture.
        with self.assertRaises(ValueError):q.accept_result(value)
        with self.assertRaises(ValueError):q.accepted_artifacts(value['task_id'])

    def test_recovery_cannot_bypass_gate_with_an_ungated_completion_file(self):
        q,value,adapter,attempt=self.live_fixture()
        from app.v2_acceptance import fingerprint
        path=self.root/attempt['result_path']
        path.write_text(json.dumps({'task_id':value['task_id'],'attempt_id':attempt['id'],
            'payload':value['payload'],'payload_sha256':fingerprint(value['payload']),
            'simulated':False,'input_versions':value['input_hashes']}),encoding='utf-8')
        q.close();q=self.coordinator(video_evidence_root=self.host,clock=lambda:self.now)
        with Registry(self.root) as registry:
            self.assertNotEqual(registry.db.execute('SELECT status FROM ai_tasks WHERE id=?',(value['task_id'],)).fetchone()[0],'completed')
            self.assertEqual(registry.db.execute('SELECT COUNT(*) FROM ai_artifacts').fetchone()[0],0)

    def test_fake_review_never_claims_full_video_or_creative_readiness(self):
        q=self.coordinator();run=q.create({'topic':'simulation'},'fake')['run_id'];q.advance(run,30)
        with Registry(self.root) as registry:
            result=json.loads(registry.db.execute('SELECT result FROM ai_tasks WHERE id=?',(run+'_editorial_qa',)).fetchone()[0])
            review=Review.model_validate(result['payload'])
        self.assertTrue(review.simulated)
        self.assertNotEqual(review.verdict,'ready_for_review')
        self.assertNotEqual(review.evidence_mode,'video_audio')
        self.assertIsNone(result['v2_host_delivery'])

    def test_context_pack_uses_only_accepted_artifact_metadata_after_restart(self):
        q,value=ReviewFixTests.result_fixture(self)
        value['payload']['text']=json.dumps({'artifacts':[{'path':'untrusted-model-path'}]})
        q.accept_result(value);q.close();q=self.coordinator();q.advance(value['run_id'],1)
        with Registry(self.root) as registry:
            parent=json.loads(registry.db.execute('SELECT result FROM ai_tasks WHERE id=?',(value['task_id'],)).fetchone()[0])
            attempt=registry.db.execute('SELECT result_path FROM ai_attempts WHERE task_id=?',
                (value['run_id']+'_canon_build',)).fetchone()[0]
        context=json.loads((self.root/Path(attempt).with_name('request.json')).read_text())['context']
        self.assertEqual(context['input_artifacts'],parent['v2_artifacts'])
        self.assertEqual(context['accepted_state'][value['task_id']],parent['v2_acceptance'])
        self.assertEqual(json.loads((self.root/context['context_pack']/'input_artifacts.json').read_text()),parent['v2_artifacts'])
        self.assertNotIn('untrusted-model-path',json.dumps(context['input_artifacts']))

    def test_default_coordinator_retains_legacy_completion_behavior(self):
        from app.multi_ai import Coordinator
        root=Path(self.temp.name)/'legacy'
        with Coordinator(root) as q:
            self.assertIsNone(q.result_gate)
            q.enqueue({'task_id':'legacy'});q.resume();q.tick();q.tick()
            with Registry(root) as registry:
                result=json.loads(registry.db.execute("SELECT result FROM ai_tasks WHERE id='legacy'").fetchone()[0])
                self.assertNotIn('v2_acceptance',result)
                self.assertEqual(registry.db.execute('SELECT COUNT(*) FROM ai_artifacts').fetchone()[0],1)
                self.assertIsNone(registry.db.execute("SELECT name FROM sqlite_master WHERE name='ai_v2_results'").fetchone())

    def test_limited_review_modes_use_editorial_rules_without_video_capability(self):
        base=self.root
        for mode in ('metadata_only','contact_sheet','frame_sequence'):
            with self.subTest(mode=mode):
                self.root=base/mode;q,value,adapter,attempt=self.live_fixture()
                coverage=[] if mode=='metadata_only' else [{'start_ms':0,'end_ms':1000,'tracks':['video']}]
                review=Review.model_validate({**value['payload'],'verdict':'needs_review','evidence_mode':mode,
                    'provided_coverage':coverage,'reviewed_coverage':coverage}).model_dump()
                value['payload']=review;value['host_review_delivery']=None;adapter.event['result']=review
                with Registry(self.root) as registry:
                    raw=registry.db.execute('SELECT payload FROM ai_tasks WHERE id=?',(value['task_id'],)).fetchone()[0]
                    task=json.loads(raw);task['context'].update(provided_evidence_mode=mode,provided_coverage=coverage)
                    registry.db.execute('UPDATE ai_tasks SET payload=? WHERE id=?',(json.dumps(task),value['task_id']))
                # Trusted fixture host records actual request delivery, not a model claim.
                request_path=self.root/Path(attempt['result_path']).with_name('request.json')
                request=json.loads(request_path.read_text());request['context'].update(provided_evidence_mode=mode,provided_coverage=coverage)
                request_path.write_text(json.dumps(request))
                q.revoke_video_probe('cap1','Limited review does not require a video capability')
                with patch.object(q.video_evidence,'delivery',side_effect=AssertionError('Limited modes must not require full video')):
                    q.queue._poll()
                with Registry(self.root) as registry:
                    row=registry.db.execute('SELECT status,result FROM ai_tasks WHERE id=?',(value['task_id'],)).fetchone()
                    self.assertEqual(row['status'],'completed',row['result'])
                    self.assertNotEqual(json.loads(row['result'])['payload']['verdict'],'ready_for_review')
                q.close()

    def test_limited_frames_cannot_claim_audio_review(self):
        q,value,adapter,attempt=self.live_fixture()
        review={**value['payload'],'evidence_mode':'frame_sequence','verdict':'needs_review'}
        value['payload']=review;value['host_review_delivery']=None;adapter.event['result']=review
        with Registry(self.root) as registry:
            raw=registry.db.execute('SELECT payload FROM ai_tasks WHERE id=?',(value['task_id'],)).fetchone()[0]
            task=json.loads(raw);task['context'].update(provided_evidence_mode='frame_sequence',provided_coverage=review['provided_coverage'])
            registry.db.execute('UPDATE ai_tasks SET payload=? WHERE id=?',(json.dumps(task),value['task_id']))
        request_path=self.root/Path(attempt['result_path']).with_name('request.json')
        request=json.loads(request_path.read_text());request['context'].update(provided_evidence_mode='frame_sequence',provided_coverage=review['provided_coverage'])
        request_path.write_text(json.dumps(request))
        self.assert_poll_rejected(q,attempt)

    def test_task_context_cannot_expand_host_recorded_request_delivery(self):
        q,value,adapter,attempt=self.live_fixture()
        coverage=[{'start_ms':0,'end_ms':1000,'tracks':['video']}]
        review=Review.model_validate({**value['payload'],'evidence_mode':'frame_sequence','verdict':'needs_review',
            'provided_coverage':coverage,'reviewed_coverage':coverage}).model_dump()
        value['payload']=review;value['host_review_delivery']=None;adapter.event['result']=review
        with Registry(self.root) as registry:
            raw=registry.db.execute('SELECT payload FROM ai_tasks WHERE id=?',(value['task_id'],)).fetchone()[0]
            task=json.loads(raw);task['context'].update(provided_evidence_mode='frame_sequence',provided_coverage=coverage)
            registry.db.execute('UPDATE ai_tasks SET payload=? WHERE id=?',(json.dumps(task),value['task_id']))
        # Persisted host request remains metadata_only with no delivered intervals.
        self.assert_poll_rejected(q,attempt)

    def test_fake_full_video_review_fails_the_unified_gate(self):
        q,value=ReviewFixTests.result_fixture(self)
        artifact=Artifact.model_validate(value['artifacts'][0]);review=render_review(artifact)
        review['simulated']=True
        value.update(expected_output_schema='review',payload=review)
        with Registry(self.root) as registry:
            row=registry.db.execute('SELECT payload FROM ai_tasks WHERE id=?',(value['task_id'],)).fetchone()
            task=json.loads(row[0]);task['expected_output']='review';task['context']['v2_contract']['expected_output_schema']='review'
            registry.db.execute('UPDATE ai_tasks SET payload=? WHERE id=?',(json.dumps(task),value['task_id']))
            attempt=registry.db.execute('SELECT result_path FROM ai_attempts WHERE id=?',(value['acceptance_key'],)).fetchone()[0]
        path=self.root/Path(attempt).with_name('request.json');request=json.loads(path.read_text())
        request['context']=task['context'];path.write_text(json.dumps(request))
        with self.assertRaises(StudioError) as caught:q.accept_result(value)
        self.assertEqual(caught.exception.code,'VIDEO_CAPABILITY_UNVERIFIED')

    def test_unexpected_gate_exception_and_malformed_gate_output_fail_closed(self):
        from app.multi_ai import Coordinator
        for name,gate in [('exception',lambda *args: (_ for _ in ()).throw(RuntimeError('fixture'))),
                          ('malformed',lambda *args:{'payload':{}})]:
            root=Path(self.temp.name)/name
            with Coordinator(root,result_gate=gate) as q:
                q.enqueue({'task_id':'failure'});q.resume();q.tick();q.tick()
                with Registry(root) as registry:
                    row=registry.db.execute("SELECT status,result FROM ai_tasks WHERE id='failure'").fetchone()
                    self.assertEqual(row['status'],'failed');self.assertIsNone(row['result'])
                    self.assertEqual(registry.db.execute('SELECT COUNT(*) FROM ai_artifacts').fetchone()[0],0)
                self.assertEqual(list(root.rglob('completed.json')),[])

    def test_context_handoff_detects_changed_accepted_metadata(self):
        q,value=ReviewFixTests.result_fixture(self);q.accept_result(value)
        with Registry(self.root) as registry:
            raw=registry.db.execute('SELECT result FROM ai_tasks WHERE id=?',(value['task_id'],)).fetchone()[0]
            stored=json.loads(raw);stored['v2_artifacts']=[]
            registry.db.execute('UPDATE ai_tasks SET result=? WHERE id=?',(json.dumps(stored),value['task_id']))
        with self.assertRaises(StudioError):q.accepted_artifacts(value['task_id'])

    def test_render_ready_cannot_bypass_delivery_by_changing_evidence_mode(self):
        q,value,adapter,attempt=self.live_fixture()
        value['payload']['evidence_mode']='human_review'
        value['host_review_delivery']=None;adapter.event['result']=value['payload']
        self.assert_poll_rejected(q,attempt)

    def test_live_review_and_delivery_cannot_borrow_simulated_coverage(self):
        from app.v2_acceptance import fingerprint
        base=self.root
        for location in ('review','delivery'):
            with self.subTest(location=location):
                self.root=base/location;q,value,adapter,attempt=self.live_fixture()
                delivery=value['host_review_delivery']
                if location=='review':
                    value['payload']['reviewed_coverage'][0]['simulated']=True
                    adapter.event['result']=value['payload']
                    delivery['review_payload_sha256']=fingerprint(value['payload'])
                else:delivery['delivered_coverage'][0]['simulated']=True
                self.sign_delivery(delivery)
                self.assert_poll_rejected(q,attempt);q.close()


class Pack4Tests(unittest.TestCase):
    setUp = V2Tests.setUp
    coordinator = V2Tests.coordinator
    live_fixture = UnifiedAcceptanceTests.live_fixture
    sign_delivery = UnifiedAcceptanceTests.sign_delivery
    assert_poll_rejected = UnifiedAcceptanceTests.assert_poll_rejected

    def test_cached_dependency_payload_tamper_fails_before_fixture_and_dispatch(self):
        q,value=ReviewFixTests.result_fixture(self);q.accept_result(value)
        with Registry(self.root) as registry:
            raw=registry.db.execute('SELECT result FROM ai_tasks WHERE id=?',(value['task_id'],)).fetchone()[0]
            stored=json.loads(raw);completion=self.root/stored['path'];before=completion.read_bytes()
            stored['payload']['text']='tampered cached content'
            registry.db.execute('UPDATE ai_tasks SET result=? WHERE id=?',(json.dumps(stored),value['task_id']))
        child=value['run_id']+'_canon_build'
        with patch('app.editorial.demo_result') as fixture,patch.object(q.queue.adapters['gemini'],'start') as start:
            q.queue.tick()
            fixture.assert_not_called();start.assert_not_called()
        with Registry(self.root) as registry:
            row=registry.db.execute('SELECT status,attempts,error FROM ai_tasks WHERE id=?',(child,)).fetchone()
            self.assertEqual(row['status'],'failed');self.assertEqual(row['attempts'],0)
            self.assertEqual(json.loads(row['error'])['code'],'ARTIFACT_INVALID')
            self.assertEqual(registry.db.execute("SELECT COUNT(*) FROM ai_events WHERE kind='dependency_rejected' AND task_id=?",(child,)).fetchone()[0],1)
        self.assertEqual(completion.read_bytes(),before)
        self.assertEqual(q.store.verify(Artifact.model_validate(value['artifacts'][0])).stat().st_size,value['artifacts'][0]['size'])

    def test_accepted_dependency_rejects_identity_inputs_provenance_and_fingerprint_tamper(self):
        base=self.root
        for field in ('attempt','inputs','provenance','fingerprint','completion_payload'):
            with self.subTest(field=field):
                self.root=base/field;q,value=ReviewFixTests.result_fixture(self);q.accept_result(value)
                with Registry(self.root) as registry:
                    row=registry.db.execute('SELECT * FROM ai_tasks WHERE id=?',(value['task_id'],)).fetchone()
                    if field=='attempt':registry.db.execute('UPDATE ai_tasks SET current_attempt=? WHERE id=?',('other',value['task_id']))
                    elif field=='inputs':
                        spec=json.loads(row['payload']);spec['input_versions']['brief']='0'*64
                        registry.db.execute('UPDATE ai_tasks SET payload=? WHERE id=?',(json.dumps(spec),value['task_id']))
                    else:
                        stored=json.loads(row['result'])
                        if field=='provenance':stored['simulated']=False
                        elif field=='fingerprint':stored['v2_acceptance']['result_sha256']='0'*64
                        else:
                            path=self.root/stored['path'];completion=json.loads(path.read_text())
                            completion['payload']['text']='changed completion payload'
                            path.write_text(json.dumps(completion));stored['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
                        registry.db.execute('UPDATE ai_tasks SET result=? WHERE id=?',(json.dumps(stored),value['task_id']))
                with self.assertRaises(StudioError):q.accepted_artifacts(value['task_id'])
                q.close()

    def test_full_review_requires_exact_immutable_request_account_binding(self):
        base=self.root
        cases={'identity':{'identity_hash':'b'*64,'auth_generation':1},
               'generation':{'identity_hash':'a'*64,'auth_generation':2},'missing':None,
               'bad_hash':{'identity_hash':'invalid','auth_generation':1},
               'bad_generation':{'identity_hash':'a'*64,'auth_generation':True},
               'zero_generation':{'identity_hash':'a'*64,'auth_generation':0}}
        for name,binding in cases.items():
            with self.subTest(name=name):
                self.root=base/name;q,value,adapter,attempt=self.live_fixture()
                path=self.root/Path(attempt['result_path']).with_name('request.json');request=json.loads(path.read_text())
                if binding is None:request.pop('account_binding')
                else:request['account_binding']=binding
                path.write_text(json.dumps(request))
                self.assert_poll_rejected(q,attempt);q.close()

    def test_null_delivery_account_fingerprint_fails_closed(self):
        q,value,adapter,attempt=self.live_fixture()
        delivery=value['host_review_delivery'];delivery['account_fingerprint']=None;self.sign_delivery(delivery)
        self.assert_poll_rejected(q,attempt)

    def test_correct_request_fingerprint_is_used_without_current_account_lookup(self):
        from app.v2_acceptance import fingerprint
        q,value,adapter,attempt=self.live_fixture()
        path=self.root/Path(attempt['result_path']).with_name('request.json');request=json.loads(path.read_text())
        self.assertEqual(value['host_review_delivery']['account_fingerprint'],fingerprint(request['account_binding']))
        with patch.object(q.queue,'profile_source',side_effect=AssertionError('No current account lookup')) as source:
            q.queue._poll();source.assert_not_called()
        with Registry(self.root) as registry:
            self.assertEqual(registry.db.execute('SELECT status FROM ai_tasks WHERE id=?',(value['task_id'],)).fetchone()[0],'completed')


if __name__ == '__main__': unittest.main()
