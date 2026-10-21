from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from pydantic import TypeAdapter

from app.contracts import Identifier, ProjectBrief
from app.editorial import Scene, demo_result
from app.multi_ai_v2 import RunCoordinator
from app.registry import Registry, StudioError, content_hash
from app.v2_acceptance import fingerprint
from app.v2_production import OfflineProductionBridge
from app.v2_contracts import Artifact
from app.v2_ids import derived_id


class BoundedIdentifierTests(unittest.TestCase):
    def test_short_ids_and_exact_length_boundary_keep_legacy_values(self):
        for parts in (('scene_001','voice_001'),('edit','scene_001'),('audio','voice_001'),
                      ('run_one','revision','fix_one'),('s'*47,'v'*48)):
            with self.subTest(parts=parts):
                self.assertEqual(derived_id(*parts),'_'.join(parts))
                TypeAdapter(Identifier).validate_python(derived_id(*parts))

    def test_maximum_ids_keep_full_digest_and_distinguish_shared_prefixes_and_part_boundaries(self):
        import hashlib
        scene='s'+'a'*95
        one='v'+'b'*94+'x';two=one[:-1]+'y'
        cases=[(scene,one),(scene,two),('edit',scene),('audio',one),('audio',two),
               ('r'*96,'revision',one),('r'*96,'revision',two),
               ('a'*94+'_b','c'),('a'*94,'b_c')]
        results=[]
        for parts in cases:
            with self.subTest(parts=parts):
                identifier=derived_id(*parts)
                self.assertEqual(len(identifier),96)
                self.assertEqual(identifier,derived_id(*parts))
                self.assertEqual(identifier[-64:],hashlib.sha256(
                    json.dumps(parts,separators=(',',':')).encode('utf-8')).hexdigest())
                TypeAdapter(Identifier).validate_python(identifier)
                results.append(identifier)
        self.assertEqual(len(results),len(set(results)))

    def test_invalid_original_identifiers_are_not_hidden_by_hashing(self):
        for parts in ((),('s'*97,'voice'),('scene','invalid/key'),('scene',7)):
            with self.subTest(parts=parts),self.assertRaises(ValueError):derived_id(*parts)


class ProductionCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bulk_fixture_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.q = RunCoordinator(self.root/'queue')
        self.addCleanup(self.q.close)
        self.run_id = self.q.create({'topic': 'Original offline fixture'}, 'fixture')['run_id']
        self.task_id = self.run_id + '_scene_script'
        # Host-authored simulation fixture, still dispatched and accepted by the real gate.
        scene = demo_result('scene', {'project_id': self.run_id})
        scene['narration_segments'].append(dict(segment_id='voice_002', text='Вторая строка — точно.\nЮникод!', simulated=True))
        with Registry(self.q.root) as r:
            row = r.db.execute('SELECT payload FROM ai_tasks WHERE id=?', (self.task_id,)).fetchone()
            payload = json.loads(row[0]); payload['context']['fixture_result'] = scene
            r.db.execute('UPDATE ai_tasks SET payload=? WHERE id=?', (json.dumps(payload), self.task_id))
        self.q.advance(self.run_id, 7)
        self.engine = self.root/'engine'
        with Registry(self.engine) as r:
            r.initialize(ProjectBrief(project_id=self.run_id, title='Fixture', work_title='Original', character='Guide'))
            for i in (1, 2):
                path=self.engine/f'image{i}.png'; path.write_bytes(b'Unit-only image metadata '+bytes([i]))
                r.register_asset(self.run_id, f'image{i}', path.name, kind='image', rights='owned',
                                 base_revision=i, idempotency_key=f'image{i}')
        self.bridge = OfflineProductionBridge(self.q, self.engine)
        self.mapping = [dict(scene_id='scene_001', scene_version=1, segment_id=f'voice_00{i}', duration_ms=1500,
            shots=[dict(shot_id=f'shot{i}', asset_id=f'image{i}', source_out_ms=1500, emotional_task='Fixture intention')])
            for i in (1,2)]

    def candidate(self):
        return self.bridge.candidate(self.run_id,self.task_id,1,self.mapping)

    def test_exact_text_order_ids_replay_and_no_registration_or_jobs(self):
        before = content_hash(self.engine/'registry.sqlite')
        artifact = self.candidate()
        value = self.bridge.read_candidate(artifact)
        accepted, _ = self.bridge.accepted_scene(self.run_id,self.task_id,1)
        self.assertEqual([b['text'] for b in value['script']['segments']], [s.text for s in accepted.narration_segments])
        self.assertEqual([b['id'] for b in value['script']['segments']], ['scene_001_voice_001','scene_001_voice_002'])
        self.assertEqual(artifact,self.candidate())
        self.assertTrue(value['simulated'])
        self.assertEqual(value['state'],'validated_not_registered')
        self.assertEqual(before,content_hash(self.engine/'registry.sqlite'))
        with Registry(self.engine) as r:
            self.assertIsNone(r.db.execute("SELECT name FROM sqlite_master WHERE name='jobs'").fetchone())

    def test_foreign_run_stale_scene_missing_visual_and_duplicate_ids(self):
        with self.assertRaises(StudioError): self.bridge.candidate('foreign',self.task_id,1,self.mapping)
        with self.assertRaises(StudioError): self.bridge.candidate(self.run_id,self.task_id,2,self.mapping)
        with self.assertRaises(StudioError): self.bridge.candidate(self.run_id,self.task_id,1,[])
        with self.assertRaises(StudioError): self.bridge.candidate(self.run_id,self.task_id,1,[self.mapping[0]]*2)
        scene, _ = self.bridge.accepted_scene(self.run_id,self.task_id,1)
        raw=scene.model_dump();raw['narration_segments'].append(raw['narration_segments'][0])
        with self.assertRaises(ValueError):Scene.model_validate(raw)

    def test_rights_kind_foreign_project_and_changed_or_missing_asset(self):
        with Registry(self.engine) as r:
            r.db.execute("UPDATE assets SET rights='unknown' WHERE id='image1'")
        with self.assertRaises(StudioError):self.candidate()
        with Registry(self.engine) as r:
            r.db.execute("UPDATE assets SET rights='owned',kind='audio' WHERE id='image1'")
        with self.assertRaises(StudioError):self.candidate()
        with Registry(self.engine) as r:r.db.execute("UPDATE assets SET kind='image' WHERE id='image1'")
        self.mapping[0]['shots'][0]['asset_id']='foreign_asset'
        with self.assertRaises(StudioError):self.candidate()
        self.mapping[0]['shots'][0]['asset_id']='image1'
        (self.engine/'image1.png').write_bytes(b'changed')
        with self.assertRaises(StudioError):self.candidate()
        (self.engine/'image1.png').unlink()
        with self.assertRaises(StudioError):self.candidate()

    def test_cached_scene_or_dependency_tamper_is_rejected(self):
        with Registry(self.q.root) as r:
            row=r.db.execute('SELECT result FROM ai_tasks WHERE id=?',(self.task_id,)).fetchone()
            result=json.loads(row[0]);result['payload']['narration_segments'][0]['text']='Replacement'
            r.db.execute('UPDATE ai_tasks SET result=? WHERE id=?',(json.dumps(result),self.task_id))
        with self.assertRaises(StudioError):self.candidate()

    def test_source_dependency_tamper_is_rejected(self):
        with Registry(self.q.root) as r:
            identifier=self.run_id+'_source_index'
            row=r.db.execute('SELECT result FROM ai_tasks WHERE id=?',(identifier,)).fetchone()
            result=json.loads(row[0]);result['payload']['text']='Forged source result'
            r.db.execute('UPDATE ai_tasks SET result=? WHERE id=?',(json.dumps(result),identifier))
        with self.assertRaises(StudioError):self.candidate()

    def test_candidate_cannot_change_text_order_or_promote_provenance(self):
        original=self.candidate();value=self.bridge.read_candidate(original)
        for mutation in ('text','order','provenance'):
            raw=deepcopy(value)
            if mutation=='text':raw['script']['segments'][0]['text']='Different'
            if mutation=='order':raw['script']['segments'].reverse()
            if mutation=='provenance':raw['simulated']=False
            raw['content_sha256']=fingerprint({k:v for k,v in raw.items() if k!='content_sha256'})
            forged=self.q.store.put_json(self.run_id,raw,simulated=raw['simulated'])
            with self.subTest(mutation=mutation),self.assertRaises(StudioError):self.bridge.read_candidate(forged)

    def test_restart_and_manual_edit_preserved(self):
        original=self.candidate()
        path=self.q.store.verify(Artifact.model_validate(original));path.write_text('Manual edit',encoding='utf-8')
        with self.assertRaises(ValueError):self.bridge.read_candidate(original)
        with self.assertRaises(ValueError):self.candidate()
        self.assertEqual(path.read_text(),'Manual edit')
