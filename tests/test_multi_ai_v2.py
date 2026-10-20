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
        self.assertEqual(len(list(pack.iterdir())), 8)
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
        value = {'review_mode': 'full_video', 'verdict': 'pass', 'materials_actually_reviewed': ['control.mp4'], 'issues': []}
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
        path=self.root/'runs/video_run/artifacts/video.mp4';path.parent.mkdir(parents=True)
        raw=b'opaque video delivery fixture';path.write_bytes(raw)
        artifact=Artifact(artifact_id='video1',run_id='video_run',version=1,path='runs/video_run/artifacts/video.mp4',
                          sha256=hashlib.sha256(raw).hexdigest(),size=len(raw),simulated=False)
        review=dict(review_mode='full_video',verdict='pass',materials_actually_reviewed=[artifact.path],issues=[])
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
        self.assertEqual(q.accept_review(review,**kwargs).review_mode,'full_video')
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
        self.assert_unverified(q,{**review,'materials_actually_reviewed':['receipt.json']},kwargs)
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


if __name__ == '__main__': unittest.main()
