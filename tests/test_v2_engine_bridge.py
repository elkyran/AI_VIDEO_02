from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import tests.test_v2_production as fixtures
from app import operations
from app.jobs import JobManager
from app.production_bridge import ProductionBridge
from app.registry import Registry, StudioError
from app.v2_contracts import Artifact
from app.v2_narration import narration_descriptors, profile_spec
from app.v2_visuals import prepare_edit_plan


class EngineBridgeTests(unittest.TestCase):
    def setUp(self):
        fixtures.ProductionCandidateTests.setUp(self)
        self.artifact=fixtures.ProductionCandidateTests.candidate(self)

    def receipt(self, artifact):
        return json.loads(self.q.store.verify(Artifact.model_validate(artifact)).read_text(encoding='utf-8'))

    def register_script(self):
        script=self.bridge.read_candidate(self.artifact)['script']
        (self.engine/'script.json').write_text(json.dumps(script),encoding='utf-8')
        with Registry(self.engine) as r:
            r.register_asset(self.run_id,'script','script.json',kind='text',rights='owned',base_revision=3,idempotency_key='script')

    def test_cache_prediction_matches_existing_synthesize_requests_without_running_tts(self):
        value=self.bridge.read_candidate(self.artifact)
        recipe=narration_descriptors(value,self.engine)
        self.register_script()
        profile=profile_spec(); voice=profile['voice']
        config={'tts_engine':'piper','pronunciation_lexicon':profile['pronunciation_lexicon'],
                'tts_normalization_scope':voice['normalization_scope']}
        config.update({'tts_'+k:v for k,v in voice['settings'].items()})
        directory=self.engine/'dry_test';directory.mkdir()
        captured=[]
        def run(command,**kwargs):
            request=json.loads(Path(command[-1]).read_text(encoding='utf-8'));captured.append(request)
            log=directory/kwargs['log_name']
            log.write_text(json.dumps({'duration_seconds':0.0,'cache_hit':False}),encoding='utf-8')
            return log
        model=recipe['segments'][0]['settings']['model_path']
        ctx=SimpleNamespace(root=self.engine,job={'project_id':self.run_id},directory=directory,config=config,
            tool=lambda name:model if name=='tts_model' else 'not_executed',progress=lambda *args:None,
            relative=lambda p:Path(p).relative_to(self.engine).as_posix(),run=run)
        with patch.object(operations,'content_hash',return_value=voice['model_sha256']):
            operations.synthesize(ctx,{'script_asset_id':'script'})
        for segment,request in zip(recipe['segments'],captured):
            self.assertEqual(request['config'],segment['settings'])
            self.assertEqual(Path(request['output']).stem,segment['cache_key'])
            self.assertFalse(Path(request['output']).exists())
        self.assertEqual(recipe['runtime_status'],'BLOCKED_LOCAL_RUNTIME')
        changed=deepcopy(value);changed['script']['segments'][1]['text']='Only second changed'
        new=narration_descriptors(changed,self.engine)
        self.assertEqual(recipe['segments'][0]['cache_key'],new['segments'][0]['cache_key'])
        self.assertNotEqual(recipe['segments'][1]['cache_key'],new['segments'][1]['cache_key'])

    def test_existing_mlt_compiler_preserves_frames_subtitles_and_replay(self):
        with patch.object(JobManager,'enqueue') as enqueue:
            receipt=self.bridge.prepare_mlt(self.artifact)
            self.assertEqual(receipt,self.bridge.prepare_mlt(self.artifact))
            enqueue.assert_not_called()
        data=self.receipt(receipt);build=data['compiled']
        self.assertEqual(build['frames'],90)
        xml=ET.parse(build['project_mlt'])
        self.assertEqual(xml.find('profile').attrib['width'],'1920')
        self.assertEqual(xml.find('profile').attrib['frame_rate_num'],'30')
        self.assertTrue(Path(build['project_mlt']).with_name('subtitles.srt').is_file())
        self.assertFalse(data['production_started'])
        self.assertEqual(data['render_status'],'NOT_RUN')
        self.assertEqual(data['edit_plan']['voice_placements'],[])
        Path(build['project_mlt']).write_text('Manual MLT edit',encoding='utf-8')
        with self.assertRaises(StudioError):self.bridge.prepare_mlt(self.artifact)
        self.assertEqual(Path(build['project_mlt']).read_text(),'Manual MLT edit')

    def test_registered_descriptor_reuses_prepare_request_and_never_enqueues(self):
        self.register_script()
        with patch('app.production_profile.load_profile',return_value=profile_spec()), \
                patch('app.production_profile.check_runtime'),patch.object(JobManager,'enqueue') as enqueue:
            request=self.bridge.prepare_engine_request(self.artifact,'script',4,'voice_fixture')
        self.assertEqual(request['operation'],'synthesize')
        self.assertEqual(request['status'],'validated_not_enqueued')
        self.assertFalse(request['worker_started']);enqueue.assert_not_called()

    def test_capability_gap_and_partial_build_retry_preserve_evidence(self):
        scene,_=self.bridge.accepted_scene(self.run_id,self.task_id,1)
        plan,assets=prepare_edit_plan(self.engine,self.run_id,scene,self.mapping)
        raw=plan.model_dump();raw['transitions']=[{'kind':'unsupported'}]
        with self.assertRaises(StudioError) as caught:ProductionBridge(self.engine).compile(raw,assets,'unsupported')
        self.assertEqual(caught.exception.code,'capability_gap')
        self.assertFalse((self.engine/'unsupported').exists())
        def partial(_bridge,_plan,_assets,destination,**kwargs):
            folder=self.engine/destination;folder.mkdir(parents=True);(folder/'partial.txt').write_text('preserved')
            raise OSError('Injected interruption')
        with patch.object(ProductionBridge,'compile',new=partial):
            with self.assertRaises(OSError):self.bridge.prepare_mlt(self.artifact)
        receipt=self.bridge.prepare_mlt(self.artifact)
        self.assertTrue(self.bridge.verify_compiled(self.receipt(receipt)['compiled']))
        self.assertEqual(len(list((self.engine/'builds').glob('*/partial.txt'))),1)
