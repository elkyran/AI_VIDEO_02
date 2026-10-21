"""Fast checker orchestration tests; no workers, models or real timeout waits."""
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import check_multi_ai_v2 as checker


class CheckerTimeoutTests(unittest.TestCase):
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
                    'compileall', 'check_git_export', 'diff_check']
        self.assertEqual([c[0] for c in calls], expected)
        self.assertEqual(calls[0][1][-2:], ['tests.test_multi_ai_v2', 'tests.test_multi_ai_v2_checker'])
        self.assertEqual([c[2] for c in calls], [420] + [180] * 9)
        self.assertEqual([c['timeout_seconds'] for c in checks['checks']], [420] + [180] * 9)
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
