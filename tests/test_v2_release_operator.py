"""Release operator/host authorization tests: private fixtures, no remote calls."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.v2_delivery_host import (DriveHostAuthorization, DriveHostContext,
    DriveHostResolver, PrivateSessionStore, drive_host_readiness)
from app.v2_drive import DriveError
from app.registry import StudioError
from app.v2_run_config import HostRunConfig
from scripts.launch_multi_ai_v2 import main
from tests.test_v2_system import fixture


URI = 'https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&upload_id=synthetic_session'


class OperatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='release_operator_')
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name).resolve()
        self.config = fixture(self.folder, 1)
        self.file = self.folder/'explicit_config.json'
        self.file.write_text(self.config.model_dump_json(), encoding='utf-8')

    def launch(self, *arguments):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(['--config', str(self.file), *arguments])
        return code, json.loads(output.getvalue())

    def test_launcher_plan_doctor_and_default_are_readonly(self):
        with patch('app.jobs.JobManager.enqueue') as jobs, patch('urllib.request.OpenerDirector.open') as network:
            for arguments in [('plan',), ('doctor',), ()]:
                code, value = self.launch(*arguments)
                self.assertEqual(code, 0, value)
                self.assertEqual(value['status'], 'PLANNED')
                self.assertFalse(value.get('production_started', False))
                self.assertFalse(Path(self.config.root).exists())
            jobs.assert_not_called(); network.assert_not_called()

    def test_start_does_not_invent_execution_or_authorization(self):
        for arguments in [('start',), ('status',), ('stop',), ('resume',), ('deliver',)]:
            with self.subTest(arguments=arguments):
                code, value = self.launch(*arguments)
                self.assertEqual(code, 5)
                self.assertEqual(value['error_code'], 'SYSTEM_PERMISSION_REQUIRED')
                self.assertFalse(Path(self.config.root).exists())

    def test_start_resume_status_stop_use_existing_cli_and_queue(self):
        with patch('urllib.request.OpenerDirector.open') as network, patch('app.jobs.JobManager.enqueue') as production:
            code, first = self.launch('--execute-system', '--authorize', 'start')
            self.assertEqual((code, first['status']), (0, 'SIMULATION_SUCCEEDED'))
            code, repeated = self.launch('--execute-system', 'resume')
            self.assertEqual(code, 0, repeated)
            self.assertEqual(repeated['results'], first['results'])
            code, status = self.launch('--execute-system', 'status')
            self.assertEqual(code, 0, status)
            self.assertEqual(status['results'], first['results'])
            self.assertFalse(status['uploaded']); self.assertFalse(status['production_started'])
            network.assert_not_called(); production.assert_not_called()

    def test_missing_invalid_config_and_review_have_concrete_redacted_blockers(self):
        code, value = self.launch('review')
        self.assertEqual((code, value['error_code']), (5, 'REVIEW_REQUIRED'))
        self.file.write_text('{"secret_test_marker":"never print me"}', encoding='utf-8')
        code, value = self.launch('doctor')
        self.assertEqual(code, 5)
        self.assertNotIn('never print me', json.dumps(value))
        self.assertIn('next_action', value)

    def test_negative_ticks_never_start_work(self):
        code, value = self.launch('--execute-system', '--authorize', '--ticks', '0', 'start')
        self.assertEqual((code, value['error_code']), (5, 'INVALID_TICKS'))
        self.assertFalse(Path(self.config.root).exists())

    def test_observation_stop_and_prepare_do_not_require_an_executable_plan(self):
        # CLI owns state/permit checks. A failed doctor must not hide an existing
        # status/stop request, nor prevent prepare from persisting a blocked plan.
        def dispatch(arguments):
            self.assertNotIn(arguments[-1], ('plan', 'doctor'))
            print(json.dumps({'status': 'IN_PROGRESS', 'production_started': False, 'uploaded': False}))
            return 2
        with patch('app.v2_pipeline.system_main', side_effect=dispatch) as cli:
            for command in ('status', 'stop', 'prepare'):
                code, result = self.launch('--execute-system', command)
                self.assertEqual(code, 2)
            self.assertEqual(cli.call_count, 3)

    def test_synthetic_inputs_are_explicit_argv_and_never_enable_live_mode(self):
        supplied = self.folder/'synthetic fixture.json'; supplied.write_text('{}')
        def dispatch(arguments):
            self.assertEqual(arguments[arguments.index('--synthetic-inputs')+1], str(supplied))
            print(json.dumps({'status': 'PLANNED'})); return 0
        with patch('app.v2_pipeline.system_main', side_effect=dispatch):
            code, result = self.launch('--synthetic-inputs', str(supplied), 'plan')
            self.assertEqual(code, 0)
        live = self.config.model_dump(); live['mode'] = 'approved_input'; live['brief']['production_mode'] = 'production'
        live['inputs']['canon']['simulated'] = False
        for scene in live['inputs']['scenes']:
            scene['simulated'] = False
            for segment in scene['narration_segments']:
                segment['simulated'] = False
        self.file.write_text(HostRunConfig.model_validate(live).model_dump_json(), encoding='utf-8')
        code, result = self.launch('--synthetic-inputs', str(supplied), 'plan')
        self.assertEqual((code, result['error_code']), (5, 'SYNTHETIC_INPUTS_FORBIDDEN'))
        self.assertFalse(Path(self.config.root).exists())


class DriveHostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='release_drive_host_')
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name).resolve()
        value = fixture(self.folder, 1).model_dump()
        value['mode'] = 'approved_input'; value['brief']['production_mode'] = 'production'
        value['inputs']['canon']['simulated'] = False
        for scene in value['inputs']['scenes']:
            scene['simulated'] = False
            for segment in scene['narration_segments']:
                segment['simulated'] = False
        value['drive'] = {'credential_ref': 'existing_owner_binding', 'folder_id': 'approved_owner_folder',
            'network_write_authorized': True}
        value['operations'].append('deliver'); value['budget']['external_requests'] = 10
        self.config = HostRunConfig.model_validate(value)
        self.supplier = Mock(return_value='synthetic_opaque_token')
        self.binding = DriveHostAuthorization('existing_owner_binding', 'approved_owner_folder',
            self.config.run_id, self.config.version, self.supplier)
        self.resolver = DriveHostResolver([self.binding])
        self.context = DriveHostContext(self.resolver, self.folder/'private_sessions', authorized=True)

    def store(self, **changes):
        scope = {'folder': 'synthetic_folder', 'run': 'synthetic_run', 'version': 1, **changes}
        return PrivateSessionStore(self.folder/'private_sessions', scope=scope,
            excluded_roots=(self.config.root, self.config.input_root))

    def test_host_construction_and_readiness_never_resolve_token_or_access_network(self):
        with patch('urllib.request.OpenerDirector.open') as network:
            report = drive_host_readiness(self.context)
            self.assertTrue(report['host_binding_supplied'])
            self.assertFalse(report['uploaded']); self.assertEqual(report['remote_sync'], 'UNVERIFIED')
            transport = self.context.transport(self.config)
            self.assertTrue(callable(transport.token_supplier))
            self.assertEqual(transport.remaining, 10)
            self.assertFalse(self.context.session_root.exists())
            self.supplier.assert_not_called(); network.assert_not_called()
            self.assertNotIn('synthetic_opaque_token', repr(self.binding)+repr(self.context)+json.dumps(report))

    def test_missing_auth_ref_and_wrong_folder_run_version_are_rejected(self):
        for change in ({'credential_ref': 'unknown'}, {'folder_id': 'foreign_folder'},
                       {'run_id': 'foreign_run'}, {'version': 2}):
            arguments = dict(credential_ref=self.binding.credential_ref, folder_id=self.binding.folder_id,
                run_id=self.binding.run_id, version=self.binding.version)
            arguments.update(change)
            with self.subTest(change=change), self.assertRaises(DriveError) as result:
                self.resolver.resolve(**arguments)
            self.assertEqual(result.exception.kind, 'auth')
        self.supplier.assert_not_called()

    def test_no_authority_from_config_or_duplicate_ambiguous_binding(self):
        with self.assertRaises(DriveError):
            DriveHostContext(self.resolver, self.context.session_root).transport(self.config)
        with self.assertRaises(DriveError):
            DriveHostResolver([self.binding, self.binding])
        value = self.config.model_dump(); value['drive']['network_write_authorized'] = False
        with self.assertRaises(DriveError):
            self.context.transport(HostRunConfig.model_validate(value))
        self.supplier.assert_not_called()

    def test_private_store_is_dpapi_sealed_restartable_and_never_plaintext(self):
        store = self.store()
        self.assertFalse(store.root.exists())
        store['owned_file'] = URI
        self.assertNotIn(URI.encode(), store.path.read_bytes())
        self.assertNotIn(b'synthetic_session', store.path.read_bytes())
        self.assertEqual(self.store()['owned_file'], URI)
        self.store()['second_file'] = URI+'&second=1'
        self.assertEqual(store['second_file'], URI+'&second=1')

    def test_corrupt_and_copied_scope_sessions_fail_closed_without_uri_in_errors(self):
        first = self.store(); first['owned_file'] = URI
        for scope in ({'folder': 'foreign'}, {'run': 'foreign'}, {'version': 2}):
            other = self.store(**scope)
            other.path.write_bytes(first.path.read_bytes())
            with self.subTest(scope=scope), self.assertRaises(DriveError) as result:
                other.get('owned_file')
            self.assertEqual(result.exception.kind, 'private_store_invalid')
        first.path.write_bytes(b'corrupted synthetic private session')
        with self.assertRaises(DriveError) as result:
            first.get('owned_file')
        self.assertNotIn('synthetic', str(result.exception))

    def test_session_keys_and_https_upload_origin_are_allowlisted(self):
        store = self.store()
        for key in ('../outside', 'secret\nheader', '', 'a'*257):
            with self.subTest(key=key), self.assertRaises(DriveError):
                store[key] = URI
        for uri in ('http://www.googleapis.com/upload/drive/v3/files',
                    'https://foreign.invalid/upload/drive/v3/files',
                    'https://www.googleapis.com/drive/v3/files', URI+'\r\nAuthorization: forbidden', URI+'#fragment'):
            with self.subTest(uri=uri), self.assertRaises(DriveError):
                store['owned_file'] = uri
        self.assertFalse(store.root.exists())

    def test_store_rejects_repo_sync_inputs_and_active_run_locations(self):
        from app.registry import StudioError
        repo = Path(__file__).resolve().parents[1]
        for root in (repo/'private_sessions', self.folder/'OneDrive'/'sessions',
                     Path(self.config.root)/'sessions', Path(self.config.input_root)/'sessions'):
            with self.subTest(root=root), self.assertRaises((ValueError, StudioError, DriveError)):
                PrivateSessionStore(root, scope={'run': 'synthetic'},
                    excluded_roots=(self.config.root, self.config.input_root))

    def test_expired_supplier_does_not_log_auth_value_or_claim_uploaded(self):
        transport = self.context.transport(self.config)
        transport.token_supplier = Mock(return_value='')
        with patch.object(transport.opener, 'open') as network, self.assertRaises(DriveError) as result:
            transport.inspect('synthetic_file')
        self.assertEqual(result.exception.kind, 'auth')
        self.assertEqual(transport.remaining, 9)
        network.assert_not_called()

    def test_host_supplier_exception_is_redacted_before_transport_logging(self):
        self.supplier.side_effect = RuntimeError('synthetic_private_diagnostic_must_not_escape')
        transport = self.context.transport(self.config)
        with patch.object(transport.opener, 'open') as network, self.assertRaises(DriveError) as result:
            transport.inspect('synthetic_file')
        self.assertEqual(result.exception.kind, 'auth')
        self.assertNotIn('synthetic_private_diagnostic', str(result.exception))
        network.assert_not_called()

    def test_delivery_host_resolution_precedes_existing_queue_and_wrong_folder_cannot_enqueue(self):
        from app.v2_pipeline import SystemPipeline, delivery_node
        node = delivery_node(self.config)
        queue = Mock()
        queue.snapshot.return_value = {'tasks': [{'task_id': node['task_id'], 'status': 'waiting_access',
            'error': {'kind': 'authentication'}}]}
        pipeline = SimpleNamespace(config=self.config, drive_host=self.context,
            status=lambda: {'status': 'READY_FOR_OWNER_REVIEW'}, enqueue=Mock(),
            q=SimpleNamespace(queue=queue))
        with patch('urllib.request.OpenerDirector.open') as network:
            result = SystemPipeline.deliver(pipeline)
            self.assertEqual(result['status'], 'WAITING_AUTH')
            self.assertFalse(result['uploaded']); self.assertTrue(result['local_release_preserved'])
            pipeline.enqueue.assert_called_once_with(node)
            self.assertEqual(queue.tick.call_count, 3)
            self.assertEqual(pipeline.drive_transport.remaining, self.config.budget.external_requests)
            self.supplier.assert_not_called(); network.assert_not_called()
        pipeline.enqueue.reset_mock(); queue.reset_mock()
        wrong = self.config.model_dump(); wrong['drive']['folder_id'] = 'foreign_folder'
        pipeline.config = HostRunConfig.model_validate(wrong)
        with self.assertRaises(StudioError) as rejected:
            SystemPipeline.deliver(pipeline)
        self.assertEqual(rejected.exception.code, 'WAITING_AUTH')
        pipeline.enqueue.assert_not_called(); queue.tick.assert_not_called()


if __name__ == '__main__':
    unittest.main()
