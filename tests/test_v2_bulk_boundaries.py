from copy import deepcopy
import json
from pathlib import Path
import unittest

import tests.test_v2_production as fixtures
from app.registry import Registry, StudioError
from app.multi_ai_v2 import RunCoordinator
from app.v2_production import OfflineProductionBridge
from app.v2_ids import derived_id
from app.v2_contracts import Artifact
from app.v2_sources import verify_sources


class BulkBoundaryTests(unittest.TestCase):
    def setUp(self):
        fixtures.ProductionCandidateTests.setUp(self)
        self.candidate=fixtures.ProductionCandidateTests.candidate(self)
        self.q.advance(self.run_id,30)  # Isolate revision dispatch from the remaining original stages.

    def test_revision_dependency_tamper_fails_before_dispatch(self):
        proposal=self.bridge.propose_revision(self.candidate,'voice_002','Updated text')
        task=self.bridge.enqueue_fixture_revision(proposal,'tamper_parent')
        with Registry(self.q.root) as r:
            value=json.loads(r.db.execute('SELECT result FROM ai_tasks WHERE id=?',(self.task_id,)).fetchone()[0])
            value['payload']['narration_segments'][0]['text']='Forged cache'
            r.db.execute('UPDATE ai_tasks SET result=? WHERE id=?',(json.dumps(value),self.task_id))
        self.q.advance(self.run_id,5)
        with Registry(self.q.root) as r:
            row=r.db.execute('SELECT * FROM ai_tasks WHERE id=?',(task['task_id'],)).fetchone()
            self.assertEqual(row['status'],'failed');self.assertEqual(row['attempts'],0)
            self.assertEqual(r.db.execute('SELECT count(*) FROM ai_artifacts WHERE task_id=?',(task['task_id'],)).fetchone()[0],0)

    def test_changed_prior_candidate_blocks_pending_revision(self):
        proposal=self.bridge.propose_revision(self.candidate,'voice_002','Updated text')
        task=self.bridge.enqueue_fixture_revision(proposal,'tamper_candidate')
        path=self.q.store.verify(Artifact.model_validate(self.candidate));path.write_text('Manual edit',encoding='utf-8')
        self.q.advance(self.run_id,5)
        with Registry(self.q.root) as r:
            row=r.db.execute('SELECT status,attempts FROM ai_tasks WHERE id=?',(task['task_id'],)).fetchone()
            self.assertEqual(tuple(row),('failed',0))
        self.assertEqual(path.read_text(),'Manual edit')

    def test_conflicting_scene_version_is_rejected_without_replacing_first_result(self):
        one=self.bridge.propose_revision(self.candidate,'voice_002','First version two')
        two=self.bridge.propose_revision(self.candidate,'voice_002','Conflicting version two')
        first=self.bridge.enqueue_fixture_revision(one,'first')
        second=self.bridge.enqueue_fixture_revision(two,'second')
        self.q.advance(self.run_id,5)
        with Registry(self.q.root) as r:
            row=r.db.execute('SELECT payload FROM ai_scenes WHERE project_id=? AND scene_id=? AND version=2',
                              (self.run_id,'scene_001')).fetchone()
            self.assertEqual(json.loads(row[0])['narration_segments'][1]['text'],'First version two')
            self.assertEqual(r.db.execute('SELECT status FROM ai_tasks WHERE id=?',(first['task_id'],)).fetchone()[0],'completed')
            self.assertEqual(r.db.execute('SELECT status FROM ai_tasks WHERE id=?',(second['task_id'],)).fetchone()[0],'failed')
            self.assertEqual(r.db.execute('SELECT count(*) FROM ai_artifacts WHERE task_id=?',(second['task_id'],)).fetchone()[0],0)

    def test_revision_provenance_cannot_be_promoted_even_on_replay(self):
        proposal=self.bridge.propose_revision(self.candidate,'voice_002','Updated text')
        data=json.loads(self.q.store.verify(Artifact.model_validate(proposal)).read_text(encoding='utf-8'))
        forged=self.q.store.put_json(self.run_id,data,simulated=False)
        with self.assertRaises(StudioError):self.bridge.enqueue_fixture_revision(forged.model_dump(),'live')

    def test_maximum_revision_key_survives_restart_pending_and_completed_replay(self):
        key='k'+'a'*94+'x'
        proposal=self.bridge.propose_revision(self.candidate,'voice_002','Bounded revision')
        conflict=self.bridge.propose_revision(self.candidate,'voice_002','Conflicting revision')
        task=self.bridge.enqueue_fixture_revision(proposal,key)
        self.assertEqual(task['task_id'],derived_id(self.run_id,'revision',key))
        self.assertEqual(len(task['task_id']),96)
        self.assertNotEqual(task['task_id'],derived_id(self.run_id,'revision',key[:-1]+'y'))
        self.q.close()
        self.q=RunCoordinator(self.root/'queue');self.addCleanup(self.q.close)
        self.bridge=OfflineProductionBridge(self.q,self.engine)
        self.assertEqual(task,self.bridge.enqueue_fixture_revision(proposal,key))
        with self.assertRaises(StudioError) as caught:self.bridge.enqueue_fixture_revision(conflict,key)
        self.assertEqual(caught.exception.code,'IDEMPOTENCY_CONFLICT')
        self.q.advance(self.run_id,5)
        replay=self.bridge.enqueue_fixture_revision(proposal,key)
        self.assertEqual(replay['task_id'],task['task_id']);self.assertEqual(replay['status'],'COMPLETED')
        with Registry(self.q.root) as r:
            payload=json.loads(r.db.execute('SELECT payload FROM ai_tasks WHERE id=?',(task['task_id'],)).fetchone()[0])
            contract=payload['context']['v2_contract']
            self.assertEqual((contract['run_id'],contract['idempotency_key'],contract['task_id']),
                             (self.run_id,key,task['task_id']))
            self.assertEqual(r.db.execute('SELECT count(*) FROM ai_attempts WHERE task_id=?',(task['task_id'],)).fetchone()[0],1)

    def test_reference_canon_fixture_is_hash_pinned(self):
        root=Path(__file__).resolve().parents[1]/'examples/bulk_01'
        bible=json.loads((root/'canon.json').read_text(encoding='utf-8'))
        manifest=json.loads((root/'sources.json').read_text(encoding='utf-8'))
        result=verify_sources(root,bible,manifest['sha256'])
        self.assertEqual(result['manifest'],manifest['manifest'])
        self.assertEqual(result['canon_truth'],'UNVERIFIED')
