"""Immutable, fully revision-bound offline core and regression evidence."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.check_multi_ai import GROUPS
from app.v2_storage import EvidenceRun, source_binding


DEFAULT_GROUP_TIMEOUT_SECONDS = 180
GROUP_TIMEOUT_SECONDS = {'v2': 420, 'contracts': 420}


def audit_export():
    """Use the existing scanner read-only; do not overwrite its historical report."""
    from scripts.check_git_export import scan
    def git(*args): return subprocess.check_output(['git', '-C', str(ROOT), *args])
    names = set(n for n in git('ls-files', '--cached', '--others', '--exclude-standard', '-z').decode().split('\0') if n)
    findings = []
    for name in sorted(names):
        p = ROOT / name
        if not p.is_file(): continue
        data = p.read_bytes()
        findings.extend(scan(data, name))
        if len(data) > 25*1024*1024: findings.append({'path': name, 'rule': 'large_file'})
    labels = {row.partition(' ')[0]: row.partition(' ')[2] for row in git('rev-list', '--all', '--objects').decode().splitlines()}
    p = subprocess.Popen(['git', '-C', str(ROOT), 'cat-file', '--batch'], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    history_blobs = 0
    try:
        for oid, name in labels.items():
            p.stdin.write((oid+'\n').encode()); p.stdin.flush()
            header = p.stdout.readline().split()
            data = p.stdout.read(int(header[2])); p.stdout.read(1)
            if header[1] == b'blob':
                history_blobs += 1; findings.extend(scan(data, 'git:'+oid+':'+name))
    finally:
        p.stdin.close(); p.stdout.close(); p.wait()
    result = {'status': 'passed' if not findings else 'failed', 'file_count': len(names),
              'history_blobs': history_blobs, 'findings': findings, 'historical_reports_modified': False}
    print(json.dumps(result)); return 0 if not findings else 1


def main(argv=None):
    groups = {'v2': ['tests.test_multi_ai_v2', 'tests.test_multi_ai_v2_checker'],
              **{k: GROUPS[k] for k in ['contracts','queue','adapters','mai05','panel','legacy']}}
    groups['contracts'] = [*groups['contracts'], 'tests.test_v2_sources', 'tests.test_v2_production',
        'tests.test_v2_visuals', 'tests.test_v2_engine_bridge', 'tests.test_v2_offline_qa',
        'tests.test_v2_release', 'tests.test_v2_offline', 'tests.test_v2_bulk_boundaries']
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'reports/multi_ai_v2/evidence', help='Parent directory only; UTC+UUID child is created exclusively')
    parser.add_argument('--groups', nargs='+', choices=list(groups), default=list(groups))
    args = parser.parse_args(argv)
    run = EvidenceRun(args.output, root=ROOT)
    result = {'status': 'failed'}
    try:
        checks = []
        commands = [(name, [sys.executable, '-X', 'utf8', '-m', 'unittest', '-v', *groups[name]]) for name in args.groups]
        if args.groups == list(groups):
            commands.extend([('compileall', [sys.executable, '-m', 'compileall', '-q', 'app', 'scripts', 'tests']),
                ('check_git_export', [sys.executable, '-c', 'from scripts.check_multi_ai_v2 import audit_export; raise SystemExit(audit_export())']),
                ('diff_check', ['git', 'diff', '--check'])])
        for group, command in commands:
            timeout_seconds = GROUP_TIMEOUT_SECONDS.get(group, DEFAULT_GROUP_TIMEOUT_SECONDS)
            log = run.folder/(group+'.log'); start = time.monotonic()
            with log.open('x', encoding='utf-8') as stream:
                try:
                    code = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout_seconds).returncode
                except subprocess.TimeoutExpired:
                    stream.write(f'\nTimeout {timeout_seconds}s; not passed.\n'); code = 124
            elapsed = round(time.monotonic()-start, 6)
            count = re.search(r'Ran (\d+) tests?', log.read_text(encoding='utf-8'))
            count = int(count.group(1)) if count else None
            skipped = re.search(r'OK \(skipped=(\d+)\)', log.read_text(encoding='utf-8'))
            skipped = int(skipped.group(1)) if skipped else 0
            check = {'group': group, 'status': 'passed' if code == 0 else 'failed', 'exit_code': code,
                     'test_count': count, 'skipped_count': skipped, 'elapsed_seconds': elapsed,
                     'timeout_seconds': timeout_seconds, 'command': command, 'log_ref': log.name, 'truncated': False}
            checks.append(check); run.record_command(command, code, elapsed_seconds=elapsed, test_count=count, skipped_count=skipped)
            print(json.dumps(check, ensure_ascii=False), flush=True)
        unchanged = source_binding(ROOT)['source_manifest_sha256'] == run.binding['source_manifest_sha256']
        result = {**run.binding, 'task_id': 'MULTI-AI-V2-CORE-FIX', 'checked_at': run.checked_at,
                  'status': 'passed' if unchanged and all(c['status']=='passed' for c in checks) else 'failed',
                  'source_unchanged_during_checks': unchanged, 'checks': checks,
                  'test_count': sum(c['test_count'] or 0 for c in checks),
                  'skipped_count': sum(c['skipped_count'] for c in checks),
                  'model_requests': 0, 'paid_api_requests': 0, 'production_started': False, 'truncated': False}
        run.write_json('checks.json', result)
    finally:
        manifest_sha = run.finalize(status=result['status'])
        print(json.dumps({'status': result['status'], 'evidence_directory': str(run.folder), 'manifest_sha256': manifest_sha}), flush=True)
    return 0 if result['status']=='passed' else 1


if __name__ == '__main__': raise SystemExit(main())
