"""Offline readiness checks. Fake can pass; unverified production fails closed."""
import argparse
from contextlib import closing
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def check(mode, root, *, require_runner=False, require_drive=False, write_probe=False):
    report = {'mode': mode, 'status': 'ERROR', 'errors': [], 'model_requests': 0,
              'paid_api_requests': 0, 'production_started': False}
    if mode not in {'fake', 'production'}:
        report['errors'].append('Unknown healthcheck mode'); return report
    report['python'] = sys.version.split()[0]
    if sys.version_info < (3,11): report['errors'].append('Python >=3.11 is required')
    dependencies = {}
    for name, expected in [('pydantic','2.12.5'),('numpy','2.4.3')]:
        try: dependencies[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError: dependencies[name] = None
        if dependencies[name] != expected: report['errors'].append(f'{name}=={expected} is required')
    report['dependencies'] = dependencies
    try:
        from app.multi_ai_v2 import local_root, legacy_queue_conflict
        from app.v2_contracts import Brief, Task, Result, Artifact, Review
        from app.editorial import Scene, EditPlan
        from app.v2_run_config import HostRunConfig
        from app.production_bridge import ProductionBridge
        from app.registry import StudioError
        models = {'brief': Brief,'task': Task,'result': Result,'artifact': Artifact,
                  'scene': Scene,'edit_plan': EditPlan,'review': Review,'host_run_config': HostRunConfig}
        for name, model in models.items():
            path = ROOT/'config/multi_ai_v2_schemas'/(name+'.schema.json')
            if json.loads(path.read_text(encoding='utf-8')) != model.model_json_schema():
                report['errors'].append('Schema drift: '+name)
        report['schemas'] = 'VERIFIED' if not any('Schema' in e for e in report['errors']) else 'INVALID'
        if not root: raise ValueError('Set a separate LOCAL_RUN_ROOT')
        root = local_root(root)
        if legacy_queue_conflict(root): raise ValueError('Legacy queue conflict')
        from app.v2_run_config import isolated_root
        root = isolated_root(root)
        if write_probe:
            root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='v2_health_', dir=root) as folder:
                path = Path(folder)/'write-check'; path.write_text('ok')
                if path.read_text()!='ok': raise ValueError('Write/read integrity failed')
        existing = next(p for p in (root, *root.parents) if p.exists())
        report.update(write_permissions=True if write_probe else None, write_probe=write_probe,
                      local_run_root_valid=True, legacy_queue_conflict=False,
                      free_bytes=shutil.disk_usage(existing).free)
        with closing(sqlite3.connect(':memory:')) as conn:
            report['sqlite'] = conn.execute('SELECT sqlite_version()').fetchone()[0]
        report['production_bridge'] = ProductionBridge.capabilities()
        if mode=='production':
            from app.production_profile import load_profile
            profile = load_profile(ROOT)
            report['voice_model_exists'] = (ROOT/profile['voice']['model_ref']).is_file()
            if not report['voice_model_exists']: report['errors'].append('Voice model missing')
            ffmpeg = shutil.which('ffmpeg') or str(ROOT/'tools/Shotcut/ffmpeg.exe')
            report['ffmpeg'] = Path(ffmpeg).is_file()
            if not report['ffmpeg']: report['errors'].append('FFmpeg unavailable')
            report['production_profile_consistency'] = 'UNVERIFIED'
            report['errors'].append('Use --config doctor for the explicitly selected isolated runtime; studio is not inspected')
            report['provider_routes'] = 'UNVERIFIED'
            report['errors'].append('Gemini Story/Visual and live production routes are not verified')
            if require_runner:
                report['runner_readiness']='UNVERIFIED'; report['errors'].append('Runner readiness not verified')
            if require_drive:
                report['drive_readiness']='UNVERIFIED'; report['errors'].append('Unattended Drive readiness not verified')
    except (ValueError,OSError,sqlite3.Error,ImportError) as exc:
        report['errors'].append(str(exc))
    report['status'] = 'SUCCEEDED' if not report['errors'] else 'BLOCKED_UNSUPPORTED' if mode=='production' else 'ERROR'
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['fake','production'], default='fake')
    parser.add_argument('--root', default=os.getenv('LOCAL_RUN_ROOT'))
    parser.add_argument('--require-runner', action='store_true')
    parser.add_argument('--require-drive', action='store_true')
    parser.add_argument('--write-probe', action='store_true', help='Explicit small temporary write/read probe in the chosen root')
    args = parser.parse_args(argv)
    report = check(args.mode, args.root, require_runner=args.require_runner, require_drive=args.require_drive, write_probe=args.write_probe)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['status']=='SUCCEEDED' else 5 if report['status']=='BLOCKED_UNSUPPORTED' else 9


if __name__=='__main__': raise SystemExit(main())
