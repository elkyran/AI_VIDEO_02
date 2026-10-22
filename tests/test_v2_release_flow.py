"""Explicit Brief rehearsal through the existing pipeline; no TTS/render calls."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from app.registry import Registry, StudioError, content_hash
from app.v2_acceptance import fingerprint
from app.v2_pipeline import SystemPipeline, system_main
from app.v2_release_flow import synthetic_handoff
from app.v2_run_config import HostRunConfig, graph
from app.v2_operator_status import observe, request_control
from tests.test_v2_system import fixture
from tests.test_v2_release_routes import routes


class ReleaseFlowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='release01_flow_'); self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name).resolve()

    def handoff(self, count=1, name='brief_run'):
        folder = self.folder/name; folder.mkdir()
        approved = fixture(folder, count, run_id=name)
        data = approved.model_dump(); data['worker_routes'] = routes().model_dump()
        data['inputs']['canon']['work_id'] = name
        brief_data = deepcopy(data)
        brief_data['brief']['topic'] = 'Как общее дело помогает соседям — '+str(count)
        brief_data['inputs'] = {'version': 1, 'sources': data['inputs']['canon']['sources'],
            'media': data['inputs']['media'], 'requested_scene_count': count}
        original = HostRunConfig.model_validate(brief_data)
        full = HostRunConfig.model_validate(data)
        supplied = {}
        for node in graph(full):
            if node.get('provider_role'):
                value = supplied.setdefault(node['scene_id'], {})
                value['story' if node['provider_role'] == 'gemini_story' else 'visual'] = node['value']
        bundle = folder/'synthetic.json'; bundle.write_text(json.dumps({'inputs': data['inputs'],
            'worker_results': supplied}, ensure_ascii=False), encoding='utf-8')
        return original, bundle

    def test_one_two_five_scene_briefs_run_real_handlers_and_separate_worker_routes(self):
        for count in (1, 2, 5):
            with self.subTest(count=count):
                original, bundle = self.handoff(count, 'brief_'+str(count))
                config, workers = synthetic_handoff(original, bundle, persist=True)
                self.assertEqual(config.brief, original.brief)
                self.assertEqual(config.operations, original.operations)
                self.assertEqual(config.runtime, original.runtime)
                self.assertTrue((Path(original.root)/'execution_config.json').is_file())
                pipeline = SystemPipeline(config, opt_in=True, authorize=True, worker_fixtures=workers)
                try:
                    result = pipeline.run()
                    self.assertEqual(result['status'], 'SIMULATION_SUCCEEDED', result)
                    self.assertFalse(result['production_started']); self.assertFalse(result['uploaded'])
                    self.assertEqual(result['total'], len(graph(config)))
                    with Registry(pipeline.q.root) as db:
                        bindings = [json.loads(r[0])['worker_route'] for r in db.db.execute(
                            "SELECT payload FROM ai_tasks WHERE role IN ('writer','critic')")]
                        self.assertEqual(len(bindings), count*2)
                        self.assertEqual({r['profile_id'] for r in bindings}, {'gemini-1', 'gemini-2'})
                        self.assertEqual(db.db.execute("SELECT count(*) FROM ai_events WHERE kind='result_accepted'").fetchone()[0], len(graph(config)))
                    self.assertFalse((Path(config.root)/'engine').exists())
                finally: pipeline.close()

    def test_explicit_bundle_does_not_enable_live_or_expand_host_permissions(self):
        original, bundle = self.handoff()
        live = original.model_dump(); live['mode'] = 'autonomous'; live['brief']['production_mode'] = 'production'
        for route in live['worker_routes']['routes']: route['synthetic'] = False
        with self.assertRaises(StudioError): synthetic_handoff(HostRunConfig.model_validate(live), bundle)
        value = json.loads(bundle.read_text()); value['runtime'] = {'command': 'ignored'}
        bundle.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaises(StudioError): synthetic_handoff(original, bundle)
        self.assertFalse(Path(original.root).exists())

    def test_exact_handoff_replay_and_materialized_config_cli_resume(self):
        original, bundle = self.handoff()
        config, workers = synthetic_handoff(original, bundle, persist=True)
        self.assertEqual(synthetic_handoff(original, bundle, persist=True)[0], config)
        self.assertEqual(synthetic_handoff(config, bundle)[0], config)
        file = Path(original.root)/'execution_config.json'
        for command, authority in [('run', ['--authorize']), ('resume', [])]:
            with redirect_stdout(io.StringIO()) as out:
                code = system_main(['--config', str(file), '--synthetic-inputs', str(bundle),
                    '--execute-system', *authority, command])
            result = json.loads(out.getvalue()); self.assertEqual(code, 0, result)
            self.assertEqual(result['status'], 'SIMULATION_SUCCEEDED')
        receipt = next((Path(original.root)/'reconciliation').glob('*.json'))
        receipt.write_text('{}', encoding='utf-8')
        with self.assertRaises(StudioError): synthetic_handoff(original, bundle, persist=True)

    def test_pipeline_receipt_recovery_is_exact_and_idempotent(self):
        original, bundle = self.handoff()
        config, workers = synthetic_handoff(original, bundle, persist=True)
        pipeline = SystemPipeline(config, opt_in=True, authorize=True, worker_fixtures=workers)
        try:
            pipeline.q.queue.resume()
            adapter = pipeline.q.queue.adapters['gemini']
            for _ in range(12):
                pipeline.q.queue.tick()
                if adapter.runs: break
            self.assertTrue(adapter.runs)
            state = next(iter(adapter.runs.values()))
            event = adapter.poll(next(iter(adapter.runs)))[0]
            self.assertEqual(event['type'], 'completed')
            directory = state['attempt_dir']
            for _ in range(2):
                recovered = adapter.recover(directory)
                self.assertEqual(recovered['status'], 'completed')
                self.assertEqual(recovered['host_metadata'], event['host_metadata'])
            pipeline.close()
            pipeline = SystemPipeline(config, opt_in=True, worker_fixtures=workers)
            self.assertEqual(pipeline.run()['status'], 'SIMULATION_SUCCEEDED')
        finally: pipeline.close()

    def test_missing_worker_outputs_never_fall_back_to_fake_adapter(self):
        original, bundle = self.handoff()
        config, workers = synthetic_handoff(original, bundle)
        with self.assertRaises(StudioError): SystemPipeline(config, opt_in=True, authorize=True)
        self.assertFalse(Path(config.root).exists())

    def test_cli_plan_doctor_are_readonly_and_prepare_preserves_blocked_brief_plan(self):
        original, bundle = self.handoff()
        file = self.folder/'brief_config.json'; file.write_text(original.model_dump_json(), encoding='utf-8')
        for command in ('plan', 'doctor'):
            with redirect_stdout(io.StringIO()) as out:
                code = system_main(['--config', str(file), command])
            self.assertEqual(code, 5); self.assertFalse(Path(original.root).exists())
        with redirect_stdout(io.StringIO()) as out:
            code = system_main(['--config', str(file), '--execute-system', '--authorize', 'prepare'])
        result = json.loads(out.getvalue()); self.assertEqual(code, 5)
        self.assertTrue(Path(result['plan_path']).is_file())
        self.assertEqual(content_hash(Path(result['plan_path'])), result['plan_sha256'])
        self.assertFalse((Path(original.root)/'coordinator').exists())

    def test_status_and_stop_do_not_require_original_source_to_exist(self):
        config = fixture(self.folder)
        pipeline = SystemPipeline(config, opt_in=True, authorize=True)
        try: result = pipeline.run(ticks=1)
        finally: pipeline.close()
        source = Path(config.input_root)/config.inputs.canon.sources[0].path
        source.rename(source.with_suffix('.held'))
        state = observe(config)
        self.assertEqual(state['status'], 'RECORDED_PROGRESS')
        self.assertEqual(state['results_verification'], 'NOT_REVALIDATED_READ_ONLY_OBSERVATION')
        stopped = request_control(config, 'stop', opt_in=True)
        self.assertEqual(stopped['control'], 'stopped')
        self.assertFalse(stopped['process_termination_verified'])


if __name__ == '__main__': unittest.main()
