"""Fast checker orchestration tests; no workers, models or real timeout waits."""
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import check_multi_ai_v2 as checker


class CheckerTimeoutTests(unittest.TestCase):
    def timing_child(self, source, deadline=5, timeout=10):
        folder = Path(tempfile.mkdtemp(prefix='closure_timing_'))
        self.addCleanup(__import__('shutil').rmtree, folder)
        (folder/'fixture_timing.py').write_text(source, encoding='utf-8')
        timing, stacks = folder/'timing.jsonl', folder/'stacks.log'
        command = [sys.executable, '-B', '-X', 'utf8', '-m', 'scripts.timed_unittest',
            '--telemetry', str(timing), '--stacks', str(stacks), '--deadline', str(deadline),
            '--', '-m', 'unittest', 'discover', '-s', str(folder), '-p', 'fixture_timing.py']
        try:
            result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1],
                capture_output=True, timeout=timeout)
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = 124
        return code, [json.loads(line) for line in timing.read_text(encoding='utf-8').splitlines()], stacks.read_text(encoding='utf-8')

    def test_timing_child_records_real_phases_failure_and_skip(self):
        code, events, stacks = self.timing_child('import unittest\nclass Fixture(unittest.TestCase):\n'
            ' def test_ok(self): self.assertTrue(True)\n'
            ' def test_fail(self): self.fail("expected fixture failure")\n'
            ' @unittest.skip("fixture tests skip accounting")\n def test_skip(self): pass\n')
        self.assertEqual(code, 1)
        self.assertEqual(len([e for e in events if e['event']=='start']), 3)
        self.assertEqual({e['status'] for e in events if e['event']=='finish'}, {'passed','failed','skipped'})
        self.assertTrue({'_callSetUp','_callTestMethod','_callTearDown'} <=
                        {e['phase'] for e in events if e['event']=='phase'})
        summary = next(e for e in events if e['event']=='summary')
        self.assertEqual((summary['tests'],summary['failures'],summary['skipped']), (3,1,1))
        self.assertEqual(stacks, '')

    def test_timeout_snapshot_captures_unfinished_actual_child_without_locals(self):
        code, events, stacks = self.timing_child('import unittest,threading\nclass Fixture(unittest.TestCase):\n'
            ' def test_wait(self):\n  private_text="AUDIT_PRIVATE_LOCAL_SENTINEL"\n  threading.Event().wait(60)\n',
            deadline=.4, timeout=2)
        self.assertEqual(code, 124)
        snapshot = next(e for e in events if e['event']=='deadline_snapshot')
        self.assertTrue(any('test_wait' in x for x in snapshot['unfinished']))
        self.assertIn('test_wait', stacks)
        self.assertNotIn('AUDIT_PRIVATE_LOCAL_SENTINEL', stacks)

    def run_checker(self, groups=None, *, timeout_group=None, failed_group=None):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binding = {'git_commit_sha': 'a' * 40, 'git_tree_sha': 'b' * 40,
                       'dirty': False, 'source_manifest_sha256': 'c' * 64}
            calls = []

            def fake_run(command, **kwargs):
                group = Path(kwargs['stdout'].name).stem
                calls.append((group, command, kwargs['timeout']))
                if group == timeout_group:
                    # Even a misleading partial success footer cannot override exit 124.
                    kwargs['stdout'].write('Ran 1 test in 0.001s\n\nOK\n')
                    raise subprocess.TimeoutExpired(command, kwargs['timeout'])
                if group == failed_group:
                    kwargs['stdout'].write('Ran 1 test in 0.001s\n\nFAILED (errors=1)\n')
                    return subprocess.CompletedProcess(command, 1)
                if '-m' in command and 'unittest' in command:
                    kwargs['stdout'].write('Ran 1 test in 0.001s\n\nOK\n')
                return subprocess.CompletedProcess(command, 0)

            argv = ['--output', str(root / 'evidence')]
            if groups is not None:
                argv += ['--groups', *groups]
            with patch.object(checker, 'ROOT', root), \
                    patch.object(checker, 'source_binding', return_value=binding), \
                    patch('app.v2_storage.source_binding', return_value=binding), \
                    patch.object(checker.subprocess, 'run', side_effect=fake_run), \
                    redirect_stdout(io.StringIO()) as output:
                code = checker.main(argv)
            summary = json.loads(output.getvalue().splitlines()[-1])
            folder = Path(summary['evidence_directory'])
            checks = json.loads((folder / 'checks.json').read_text(encoding='utf-8'))
            manifest_data = (folder / 'manifest.json').read_bytes()
            manifest = json.loads(manifest_data)
            self.assertEqual(hashlib.sha256(manifest_data).hexdigest(), summary['manifest_sha256'])
            for name, record in manifest['files'].items():
                data = (folder / name).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), record['sha256'])
                self.assertEqual(len(data), record['bytes'])
            logs = {p.stem: p.read_text(encoding='utf-8') for p in folder.glob('*.log')}
            return code, calls, checks, manifest, logs

    def test_full_run_selects_bounded_timeouts_and_keeps_all_groups(self):
        code, calls, checks, manifest, _ = self.run_checker()
        expected = ['v2', 'contracts', 'queue', 'adapters', 'mai05', 'panel', 'legacy',
                    'acquisition', 'closure', 'compileall', 'check_git_export', 'diff_check']
        self.assertEqual([c[0] for c in calls], expected)
        self.assertEqual(calls[0][1][-2:], ['tests.test_multi_ai_v2', 'tests.test_multi_ai_v2_checker'])
        self.assertEqual([c[2] for c in calls], [420, 420] + [180] * 10)
        self.assertEqual([c['timeout_seconds'] for c in checks['checks']], [420, 420] + [180] * 10)
        self.assertEqual(code, 0)
        self.assertEqual(checks['status'], 'passed')
        self.assertEqual(manifest['status'], 'passed')
        self.assertTrue(checks['source_unchanged_during_checks'])
        for value in (checks, manifest):
            self.assertEqual(value['git_commit_sha'], 'a' * 40)
            self.assertEqual(value['git_tree_sha'], 'b' * 40)
            self.assertEqual(value['source_manifest_sha256'], 'c' * 64)
            self.assertEqual(value['model_requests'], 0)
            self.assertEqual(value['paid_api_requests'], 0)
            self.assertIs(value['production_started'], False)
        for value in checks['checks']:
            self.assertGreaterEqual(value['elapsed_seconds'], 0)

    def test_v2_timeout_remains_failed_with_exit_124_and_honest_evidence(self):
        code, calls, checks, manifest, logs = self.run_checker(['v2'], timeout_group='v2')
        self.assertEqual(calls[0][2], 420)
        self.assertEqual(code, 1)
        self.assertEqual(checks['status'], 'failed')
        self.assertEqual(manifest['status'], 'failed')
        self.assertEqual(checks['checks'][0]['status'], 'failed')
        self.assertEqual(checks['checks'][0]['exit_code'], 124)
        self.assertEqual(manifest['commands'][0]['exit_code'], 124)
        self.assertEqual(checks['skipped_count'], 0)
        self.assertIn('Timeout 420s; not passed.', logs['v2'])

    def test_other_group_timeout_keeps_180_and_failed_status(self):
        code, calls, checks, manifest, logs = self.run_checker(['adapters'], timeout_group='adapters')
        self.assertEqual(calls[0][2], 180)
        self.assertEqual(code, 1)
        self.assertEqual(checks['checks'][0]['exit_code'], 124)
        self.assertEqual(manifest['status'], 'failed')
        self.assertIn('Timeout 180s; not passed.', logs['adapters'])

    def test_contracts_timeout_remains_failed_with_exit_124(self):
        code, calls, checks, manifest, logs = self.run_checker(['contracts'], timeout_group='contracts')
        self.assertEqual(calls[0][2], 420)
        self.assertEqual(code, 1)
        self.assertEqual(checks['status'], 'failed')
        self.assertEqual(checks['checks'][0]['exit_code'], 124)
        self.assertEqual(manifest['commands'][0]['exit_code'], 124)
        self.assertEqual(manifest['status'], 'failed')
        self.assertIn('Timeout 420s; not passed.', logs['contracts'])

    def test_test_failure_is_never_reported_as_passed(self):
        code, _, checks, manifest, logs = self.run_checker(['v2'], failed_group='v2')
        self.assertEqual(code, 1)
        self.assertEqual(checks['status'], 'failed')
        self.assertEqual(manifest['status'], 'failed')
        self.assertEqual(checks['checks'][0]['exit_code'], 1)
        self.assertEqual(manifest['commands'][0]['exit_code'], 1)
        self.assertNotIn('Timeout', logs['v2'])


if __name__ == '__main__':
    unittest.main()
