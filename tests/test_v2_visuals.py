from copy import deepcopy
import unittest

import tests.test_v2_production as fixtures
from app.registry import Registry, StudioError
from app.v2_visuals import verify_mapping, prepare_edit_plan


class VisualTests(unittest.TestCase):
    def setUp(self):
        fixtures.ProductionCandidateTests.setUp(self)
        self.scene, _ = self.bridge.accepted_scene(self.run_id,self.task_id,1)

    def test_multiple_shots_preserve_ranges_purpose_and_exact_coverage(self):
        self.mapping[0]['shots'][0]['source_out_ms']=500
        self.mapping[0]['shots'].append(dict(shot_id='extra',asset_id='image1',source_out_ms=1000,
            emotional_task='Explicit metadata candidate',reuse_reason='Intentional establishing reuse'))
        plan, assets=prepare_edit_plan(self.engine,self.run_id,self.scene,self.mapping)
        self.assertEqual([(p.timeline_start_ms,p.timeline_end_ms) for p in plan.shot_placements],[(0,500),(500,1500),(1500,3000)])
        self.assertEqual((plan.width,plan.height,plan.fps),(1920,1080,30))
        self.assertEqual(len(assets),2)
        self.assertEqual(len(plan.subtitle_events),2)
        self.assertEqual(plan.voice_placements,[])

    def test_duration_range_reuse_and_metadata_claim_fail_closed(self):
        for defect in ('duration','range','reuse','claim','absolute'):
            mapping=deepcopy(self.mapping)
            if defect=='duration':mapping[0]['duration_ms']=1000
            if defect=='range':mapping[0]['shots'][0]['source_in_ms']=1
            if defect=='reuse':mapping[1]['shots'][0]['asset_id']='image1'
            if defect=='claim':mapping[0]['shots'][0]['observed_content']='I watched the video'
            if defect=='absolute':mapping[0]['shots'][0]['asset_id']='C:/private/file.png'
            with self.subTest(defect=defect), self.assertRaises((StudioError,ValueError)):
                verify_mapping(self.engine,self.run_id,self.scene,mapping)

    def test_video_bounds_and_unknown_duration(self):
        with Registry(self.engine) as r:
            r.db.execute("UPDATE assets SET kind='video',duration_ms=1000 WHERE id='image1'")
        with self.assertRaises(StudioError):verify_mapping(self.engine,self.run_id,self.scene,self.mapping)
        with Registry(self.engine) as r:r.db.execute("UPDATE assets SET duration_ms=NULL WHERE id='image1'")
        with self.assertRaises(StudioError):verify_mapping(self.engine,self.run_id,self.scene,self.mapping)
