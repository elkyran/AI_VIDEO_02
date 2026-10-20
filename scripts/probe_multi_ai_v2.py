"""Bounded official-client probes. No install, credential-file reads or rotation."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.ai_adapters import sanitize
from app.ai_accounts import isolated_environment
from app.ai_cli_runtime import build_command, decode_output, make_prompt, transport_schema, _safe_log, classify_cli_failure
from app.ai_codex_diagnostics import diagnose_codex


def write(folder, name, value):
    (folder / name).write_text(json.dumps(sanitize(value), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--codex-home', type=Path, required=True)
    parser.add_argument('--live-codex', action='store_true', help='One safe ChatGPT-authenticated schema task; never API auth')
    args = parser.parse_args(argv)
    folder = args.output.resolve(); folder.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    write(folder, 'environment.json', {'checked_at': now, 'python': sys.version,
          'platform': sys.platform, 'free_local_bytes': shutil.disk_usage(ROOT).free,
          'configured_paths': {k: bool(os.getenv(k)) for k in ('LOCAL_RUN_ROOT', 'LOCAL_CACHE_ROOT', 'GOOGLE_DRIVE_ROOT')},
          'production_unchanged': True})
    import importlib.metadata
    write(folder, 'dependency_versions.json', {p: importlib.metadata.version(p) for p in ('pydantic', 'numpy')})
    command = shutil.which('codex.exe') or shutil.which('codex')
    codex = {'status': 'UNAVAILABLE', 'checked_at': now, 'provider': 'codex', 'profile': 'chatgpt_primary',
             'auth_route': 'official_codex_chatgpt', 'requested_model': None, 'returned_model': None,
             'model_requests': 0, 'quota_source': None, 'identity_verified': False}
    if command and Path(command).suffix.lower() == '.exe':
        env = isolated_environment('codex', args.codex_home)
        with tempfile.TemporaryDirectory(prefix='v2_codex_probe_') as temp:
            work = Path(temp)
            for name, extra in [('version', ['--version']), ('help', ['exec', '--help'])]:
                p = subprocess.run([command, *extra], cwd=work, env=env, capture_output=True, text=True,
                                   encoding='utf-8', errors='replace', timeout=20)
                (folder / ('codex_' + name + '.log')).write_text(_safe_log(p.stdout + p.stderr), encoding='utf-8')
                codex[name + '_exit_code'] = p.returncode
                if name == 'version': codex['client_version'] = _safe_log(p.stdout.strip())
            diagnosis = diagnose_codex([command], cwd=work, env=env, timeout=20)
            codex['diagnosis'] = diagnosis
            codex['identity_verified'] = diagnosis['identity_verified']
            codex['account_fingerprint'] = diagnosis['identity_hash']
            codex['quota_source'] = diagnosis['identity_source'] if diagnosis.get('rate_limits_by_id') or diagnosis.get('rate_limits') else None
            codex['status'] = 'UNVERIFIED' if diagnosis['authenticated'] else 'BLOCKED_AUTH'
            # Only a currently authorized ordinary ChatGPT route may make the single request.
            if args.live_codex and diagnosis['authenticated'] and diagnosis.get('ordinary_usage_allowed') is True:
                task = {'task_id': 'v2_access_probe', 'kind': 'access_check', 'role': 'director',
                        'expected_output': 'note', 'input_versions': {}, 'assignment': {},
                        'context': {'brief': 'Return a short acknowledgement: MULTI-AI-V2-OK. No tools or media.'}}
                (work / 'output_schema.json').write_text(json.dumps(transport_schema()), encoding='utf-8')
                cmd = build_command('codex', [command], [], work, task)
                codex['command'] = cmd
                start = time.monotonic()
                try:
                    p = subprocess.run(cmd, cwd=work, env=env, input=make_prompt(task), text=True,
                                       encoding='utf-8', errors='replace', capture_output=True, timeout=50)
                    codex.update(model_requests=1, exit_code=p.returncode, duration_seconds=round(time.monotonic()-start, 3))
                    (folder / 'codex_smoke.log').write_text(_safe_log(p.stdout + '\n' + p.stderr), encoding='utf-8')
                    events = [json.loads(line) for line in p.stdout.splitlines() if line.strip().startswith('{')]
                    codex['usage'] = next((e.get('usage') for e in reversed(events) if e.get('usage')), None)
                    codex['returned_model'] = next((e.get('model') for e in events if e.get('model')), None)
                    codex['model_identity_source'] = 'cli_event' if codex['returned_model'] else None
                    if p.returncode == 0:
                        codex['result'] = decode_output('codex', p.stdout, task)
                        codex['status'] = 'VERIFIED'
                    else:
                        codex['failure'] = classify_cli_failure('codex', events, p.returncode, stderr=p.stderr, stdout=p.stdout)
                        codex['status'] = 'BLOCKED_QUOTA' if codex['failure']['kind'] == 'quota_exhausted' else 'UNVERIFIED'
                except subprocess.TimeoutExpired:
                    codex.update(status='UNVERIFIED', blocker='single smoke timed out; no automatic retry', model_requests=1)
                except (ValueError, json.JSONDecodeError) as exc:
                    codex.update(status='UNVERIFIED', blocker=_safe_log(str(exc)))
            elif args.live_codex and diagnosis['authenticated']:
                codex.update(status='BLOCKED_QUOTA' if diagnosis.get('ordinary_usage_allowed') is False else 'UNVERIFIED',
                             blocker='Ordinary usage not confirmed; no model request and no alternate account')
    write(folder, 'codex_probe.json', codex)
    story = {'status': 'UNAVAILABLE', 'provider': 'gemini', 'profile': 'google_primary',
             'route': 'antigravity', 'enabled': False, 'model': None, 'account_fingerprint': None,
             'identity_verified': False, 'quota_source': None, 'model_requests': 0,
             'agy_on_path': bool(shutil.which('agy')), 'checked_at': now,
             'blocker': 'Native agy CLI not verified; desktop app/internal language server is not an adapter'}
    if story['agy_on_path']: story.update(status='UNVERIFIED', blocker='agy discovered; installed flags, isolation, auth and billing require explicit verification')
    visual = {'status': 'BLOCKED_UNSUPPORTED', 'cursor_gemini': {'status': 'BLOCKED_UNSUPPORTED',
              'official_headless_documented': True, 'local_cli_found': bool(shutil.which('cursor-agent') or shutil.which('agent'))},
              'billing_route': 'UNVERIFIED', 'second_antigravity_profile': 'UNVERIFIED',
              'independence': 'INDEPENDENCE_UNVERIFIED', 'enabled': False, 'model': None,
              'account_fingerprint': None, 'quota_source': None, 'model_requests': 0, 'checked_at': now}
    write(folder, 'gemini_story_probe.json', story); write(folder, 'gemini_visual_probe.json', visual)
    write(folder, 'identity_isolation.json', {'status': 'UNVERIFIED', 'independence': 'INDEPENDENCE_UNVERIFIED',
          'reason': 'Antigravity docs describe shared local sign-in; no second boundary/identity/quota proof'})
    write(folder, 'capability_matrix.json', {'codex': codex['status'], 'gemini_story': story['status'],
          'gemini_visual': visual['status'], 'automatic_rotation': False})
    write(folder, 'video_review_probe.json', {'status': 'UNVERIFIED', 'FULL_VIDEO_REVIEW': 'UNVERIFIED',
          'FRAME_AND_TIMELINE_REVIEW': 'UNVERIFIED', 'blocker': 'No verified Gemini Visual transport; no model video request'})
    write(folder, 'drive_probe.json', {'status': 'BLOCKED_AUTH', 'root_configured': bool(os.getenv('GOOGLE_DRIVE_ROOT')),
          'upload_verified': False, 'capacity_bytes': None, 'reason': 'Drive account and configured destination not verified'})
    write(folder, 'pilot_manifest.json', {'status': 'BLOCKED', 'duration_seconds': None,
          'actual_video_created': False, 'model_requests': codex['model_requests'], 'accepted_seconds': None,
          'blockers': ['Gemini Story', 'Gemini Visual independence/media', 'runner', 'Drive', 'production import']})
    print(json.dumps({'evidence': str(folder), 'codex': codex['status'], 'gemini_story': story['status'],
                      'gemini_visual': visual['status'], 'model_requests': codex['model_requests']}))
    return 0


if __name__ == '__main__': raise SystemExit(main())
