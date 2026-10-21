from copy import deepcopy
import json
from pathlib import Path
import unittest
import wave
from unittest.mock import patch

import tests.test_v2_production as fixtures
from app.editorial import Review
from app.registry import Registry, StudioError, content_hash
from app.v2_acceptance import fingerprint
from app.v2_contracts import Artifact, Task
from app.v2_offline_qa import technical_qa, route_review, scene_graph, invalidation_plan
from app.v2_narration import narration_descriptors


class OfflineQATests(unittest.TestCase):
    def setUp(self):fixtures.ProductionCandidateTests.setUp(self)

    def test_structural_qa_keeps_unmeasured_and_creative_results_unknown(self):
        candidate=fixtures.ProductionCandidateTests.candidate(self)
        prepared=self.bridge.prepare_mlt(candidate)
        qa=technical_qa(self.bridge,prepared)
        self.assertEqual(qa['status'],'VERIFIED')
        self.assertIn('declared',qa['scope'])
        for name in ('audio_silence','av_sync','measured_resolution','measured_fps','decoded_video_duration'):
            self.assertIsNone(qa[name])
        self.assertEqual(qa['full_video_creative_qa'],'NOT_RUN')

    def test_canonical_review_routing_binds_subject_and_cannot_claim_video_review(self):
        review=Review(project_id=self.run_id,review_id='qa',subject_type='scene',subject_id='scene_001',
            subject_version=1,subject_hash='a'*64,simulated=True).model_dump()
        routed=route_review(review,project_id=self.run_id,subject_id='scene_001',subject_version=1,subject_hash='a'*64)
        self.assertEqual(routed['creative_qa'],'NOT_RUN')
        with self.assertRaises(StudioError):route_review(review,project_id=self.run_id,subject_id='other',subject_version=1,subject_hash='a'*64)

    def add_scene(self, identifier):
        scene,_=self.bridge.accepted_scene(self.run_id,self.task_id,1)
        raw=scene.model_dump();raw['scene_id']=identifier
        task_id=self.run_id+'_'+identifier
        inputs={'fixture':fingerprint(raw)}
        contract=Task(task_id=task_id,run_id=self.run_id,scene_id=identifier,role='python',capability='scene_fixture',
            input_version=1,input_hashes=inputs,depends_on=[],assigned_worker='fake_python',provider_profile_id='simulation',
            expected_output_schema='scene',idempotency_key=identifier)
        self.q.queue.enqueue({'task_id':task_id,'project_id':self.run_id,'role':'director','expected_output':'scene',
            'depends_on':[],'input_versions':inputs,'context':{'project_id':self.run_id,'scene_id':identifier,'scene_version':1,
            'v2_contract':contract.model_dump(),'fixture_result':raw}})
        self.q.advance(self.run_id,5)
        mapping=deepcopy(self.mapping)
        for m in mapping:m['scene_id']=identifier
        return self.bridge.candidate(self.run_id,task_id,1,mapping),mapping

    def test_revision_invalidates_only_scene_two_and_preserves_other_wave_files(self):
        self.q.advance(self.run_id,30)
        one=fixtures.ProductionCandidateTests.candidate(self)
        two,mapping=self.add_scene('scene_002')
        three,_=self.add_scene('scene_003')
        def voices(candidate, label):
            value=self.bridge.read_candidate(candidate)
            recipe=narration_descriptors(value,self.engine)
            bindings={}
            for index,segment in enumerate(recipe['segments']):
                asset_id='audio_'+label+'_'+str(index)
                path=self.engine/(asset_id+'.wav')
                # Tiny original PCM fixture, explicitly synthetic, never Ruslan synthesis.
                with wave.open(str(path),'wb') as wav:
                    wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(8000)
                    wav.writeframes(bytes([len(label)+index,0])*12000)
                with Registry(self.engine) as r:
                    revision=r._project(self.run_id)['revision']
                    registered=r.register_asset(self.run_id,asset_id,path.name,kind='audio',rights='owned',
                        duration_ms=1500,base_revision=revision,idempotency_key=asset_id)
                segment_id=value['scene_block_mapping'][index]['segment_id']
                bindings[segment_id]={'asset_id':registered['asset_id'],'cache_key':segment['cache_key'],
                                      'simulated':True,'origin':'synthetic_fixture'}
            return bindings
        bindings=[voices(one,'one'),voices(two,'two_two'),voices(three,'three_three')]
        prepared=[self.bridge.prepare_mlt(a,voice_assets=b) for a,b in zip((one,two,three),bindings)]
        before=scene_graph(self.bridge,prepared)
        wav1=self.engine/'audio_one_0.wav'
        wav3=self.engine/'audio_three_three_0.wav'
        hashes=(content_hash(wav1),content_hash(wav3))
        proposal=self.bridge.propose_revision(two,'voice_002','Only Scene Two changes.')
        task=self.bridge.enqueue_fixture_revision(proposal,'fix_two')
        self.q.advance(self.run_id,5)
        for row in mapping:row['scene_version']=2
        updated=self.bridge.candidate(self.run_id,task['task_id'],2,mapping)
        with self.assertRaises(StudioError):self.bridge.prepare_mlt(updated,voice_assets=bindings[1])
        updated_bindings=deepcopy(bindings[1])
        fresh=voices(updated,'two_updated')
        updated_bindings['voice_002']=fresh['voice_002']
        with patch('app.operations.synthesize',side_effect=AssertionError('No synthesis during invalidation')):
            new_prepared=[prepared[0],self.bridge.prepare_mlt(updated,voice_assets=updated_bindings),prepared[2]]
        after=scene_graph(self.bridge,new_prepared)
        change=invalidation_plan(before,after)
        self.assertTrue(any('scene_002:mlt' in k for k in change['invalidate']))
        self.assertIn('final_assembly',change['invalidate'])
        self.assertTrue(all('scene_001' not in k and 'scene_003' not in k for k in change['invalidate']))
        self.assertTrue(any('scene_002:voice:scene_002_voice_001' in k for k in change['retain']))
        self.assertEqual(hashes,(content_hash(wav1),content_hash(wav3)))
        self.assertEqual(self.bridge.enqueue_fixture_revision(proposal,'fix_two')['task_id'],task['task_id'])
        with self.assertRaises(StudioError):self.bridge.read_candidate(two)

    def test_pinned_manual_scene_cannot_be_replaced(self):
        candidate=fixtures.ProductionCandidateTests.candidate(self)
        proposal=self.bridge.propose_revision(candidate,'voice_001','Proposal only')
        self.q.queue.update_scene(self.run_id,'scene_001',
            '\n'.join(s.text for s in self.bridge.accepted_scene(self.run_id,self.task_id,1)[0].narration_segments),1,pinned=True)
        with self.assertRaises(StudioError):self.bridge.enqueue_fixture_revision(proposal,'pinned')
        self.q.queue.update_scene(self.run_id,'scene_001','Manual replacement',1,pinned=False)
        with self.assertRaises(StudioError):self.bridge.read_candidate(candidate)

    def test_forged_mlt_receipt_without_checkpoint_is_rejected(self):
        candidate=fixtures.ProductionCandidateTests.candidate(self)
        receipt=self.bridge.prepare_mlt(candidate);raw=self.bridge.read_mlt(receipt)
        raw['compiled']['duration_seconds']=99
        forged=self.q.store.put_json(self.run_id,raw,simulated=True)
        with self.assertRaises(StudioError):self.bridge.read_mlt(forged.model_dump())
