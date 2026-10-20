"""Version-bound v2 and regression evidence; temporary fixtures, no model calls."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.check_multi_ai import revision, GROUPS


def main():
    root = Path(__file__).resolve().parents[1]
    folder = root / 'reports/multi_ai_v2/evidence/20261020T131633Z'
    folder.mkdir(parents=True, exist_ok=True)
    version, hashes = revision()
    groups = {'v2': ['tests.test_multi_ai_v2'], **{k: GROUPS[k] for k in ['contracts', 'queue', 'adapters', 'mai05', 'panel', 'legacy']}}
    checks = []
    for group, modules in groups.items():
        command = [sys.executable, '-X', 'utf8', '-m', 'unittest', '-v', *modules]
        log = folder / (group + '.log')
        with log.open('w', encoding='utf-8') as stream:
            try:
                code = subprocess.run(command, cwd=root, stdout=stream, stderr=subprocess.STDOUT, timeout=180).returncode
            except subprocess.TimeoutExpired:
                stream.write('\nTimeout 180s; not passed.\n'); code = 124
        text = log.read_text(encoding='utf-8')
        count = re.search(r'Ran (\d+) tests?', text)
        check = {'group': group, 'status': 'passed' if code == 0 else 'failed',
                 'exit_code': code, 'test_count': int(count.group(1)) if count else None,
                 'command': command, 'log_ref': log.relative_to(root).as_posix(), 'truncated': False}
        checks.append(check)
        print(json.dumps(check, ensure_ascii=False), flush=True)
    current, _ = revision()
    result = {'task_id': 'MULTI-AI-V2', 'revision': version, 'checked_at': datetime.now(timezone.utc).isoformat(),
              'status': 'passed' if current == version and all(c['status'] == 'passed' for c in checks) else 'failed',
              'source_unchanged_during_checks': current == version, 'source_hashes': hashes, 'checks': checks,
              'model_requests': 0, 'paid_api_requests': 0, 'truncated': False}
    (folder / 'checks.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    # Named failure/recovery evidence identifies actual tests, not undocumented live claims.
    for name, test in [('restart_resume_test', 'test_restart_reconciles_and_reuses_completed_results'),
                       ('quota_pause_test', 'test_quota_pause_no_rotation')]:
        value = {'status': checks[0]['status'], 'revision': version, 'test': 'tests.test_multi_ai_v2.V2Tests.' + test,
                 'evidence': 'checks.json', 'scope': 'temporary FakeAdapter process lifecycle; no Windows reboot/live provider'}
        (folder / (name + '.json')).write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__': raise SystemExit(main())
