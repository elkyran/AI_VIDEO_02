"""No ApprovedInputs bundle: real extraction/queue/gate/expansion, synthetic transport."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.registry import Registry, StudioError
from app.v2_acceptance import fingerprint
from app.v2_host_services import HostServices
from app.v2_pipeline import SystemPipeline
from app.v2_provider_bridge import SyntheticTransport
from app.v2_run_config import BriefInputs, plan
from scripts.launch_multi_ai_v2 import main as launch
from tests.test_v2_acquisition import source_config, synthetic_reply


class AcquisitionPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='closure_e2e_');self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)

    def services(self, responder=synthetic_reply):
        return HostServices(worker_transport=SyntheticTransport(responder=responder))

    def pipeline(self, config, services=None):
        p=SystemPipeline(config,opt_in=True,authorize=True,host_services=services or self.services())
        self.addCleanup(p.close)
        return p

    def test_ordinary_launcher_source_only_acquires_expands_and_replays_without_bundle(self):
        config=source_config(self.folder)
        self.assertIsInstance(config.inputs,BriefInputs)
        self.assertFalse({'canon','story','scenes','visuals'} & set(config.inputs.model_dump()))
        path=self.folder/'source_only.json';path.write_text(config.model_dump_json(),encoding='utf-8')
        services=self.services()
        def call(*args):
            with redirect_stdout(io.StringIO()) as output:
                code=launch(['--config',str(path),*args],host_services=services)
            value=json.loads(output.getvalue());self.assertEqual(code,0,value)
            return value
        self.assertEqual(call('plan')['status'],'PLANNED')
        first=call('--execute-system','--authorize','start')
        self.assertEqual(first['status'],'SIMULATION_SUCCEEDED')
        self.assertFalse(first['production_started']);self.assertFalse(first['uploaded'])
        calls=len(services.worker_transport.calls)
        second=call('--execute-system','resume')
        self.assertEqual(first['results'],second['results'])
        self.assertEqual(calls,len(services.worker_transport.calls))
        with Registry(Path(config.root)/'coordinator') as db:
            rows=db.db.execute('SELECT * FROM ai_tasks WHERE project_id=?',(config.run_id,)).fetchall()
            self.assertTrue(all(row['status']=='completed' for row in rows))
            self.assertTrue(all(json.loads(row['result'])['v2_acceptance']['status']=='ACCEPTED' for row in rows))
            self.assertEqual(db.db.execute("SELECT count(*) FROM ai_events WHERE kind='system_expansion'").fetchone()[0],1)
        seen=[call['binding']['task']['expected_output_schema'] for call in services.worker_transport.calls]
        self.assertEqual(seen,['canon_bible','story_outline','scene','segment_visuals'])
        self.assertIn('passages',json.dumps(services.worker_transport.calls[0]['context']))
        self.assertFalse((Path(config.root)/'engine/registry.sqlite').exists())

    def test_new_briefs_one_two_five_scenes_unicode_long_ids_are_data_dependent(self):
        for count in [1,2,5]:
            with self.subTest(count=count):
                config=source_config(self.folder/str(count),count,run='r'+'x'*94+str(count))
                config.brief.topic='Другая тема №'+str(count)
                p=self.pipeline(config)
                result=p.run();self.assertEqual(result['status'],'SIMULATION_SUCCEEDED',result)
                self.assertTrue(p.expanded)
                self.assertEqual(len(p.config.inputs.scenes),count)
                self.assertTrue(all(s.goal==config.brief.topic for s in p.config.inputs.scenes))
                self.assertTrue(all(len(n['task_id'])<=96 for n in p.nodes))
                self.assertEqual(p.permit.sha256,fingerprint(config.pinned()))
                self.assertEqual(p.config.operations,config.operations)
                p.close()

    def test_crash_mid_batch_rolls_back_and_restart_reuses_accepted_acquisition(self):
        config=source_config(self.folder)
        services=self.services();p=self.pipeline(config,services)
        enqueue=p.q.queue.enqueue
        def crash(spec,**kwargs):
            if kwargs.get('_registry') is not None and spec['context']['v2_contract']['capability']=='system_story':
                raise RuntimeError('Injected crash inside the one expansion transaction')
            return enqueue(spec,**kwargs)
        with patch.object(p.q.queue,'enqueue',side_effect=crash):
            with self.assertRaisesRegex(RuntimeError,'Injected crash'):p.run()
        count=len(services.worker_transport.calls)
        with Registry(p.q.root) as db:
            self.assertEqual(db.db.execute("SELECT count(*) FROM ai_events WHERE kind='system_expansion'").fetchone()[0],0)
            self.assertEqual(db.db.execute('SELECT count(*) FROM ai_tasks').fetchone()[0],len(p.nodes))
        p.close()
        p=self.pipeline(config,services)
        self.assertEqual(p.run()['status'],'SIMULATION_SUCCEEDED')
        self.assertEqual(count,len(services.worker_transport.calls))

    def test_changed_source_live_mode_and_malicious_creative_authority_fail_closed(self):
        config=source_config(self.folder)
        def attack(binding,context):
            reply=synthetic_reply(binding,context)
            if binding.task.expected_output_schema=='canon_bible':reply['payload']['sources'][0]['sha256']='f'*64
            return reply
        p=self.pipeline(config,self.services(attack))
        result=p.run();self.assertEqual(result['status'],'FAILED_FINAL')
        self.assertFalse(p.expanded);p.close()
        live=config.model_copy(deep=True);live.mode='autonomous';live.brief.production_mode='production'
        live.worker_routes=None
        self.assertTrue(any('BLOCKED_ELIGIBILITY' in b for b in plan(live)['blockers']))
        (Path(config.input_root)/config.inputs.sources[0].path).write_text('tampered',encoding='utf-8')
        with self.assertRaises(StudioError):self.pipeline(config)
