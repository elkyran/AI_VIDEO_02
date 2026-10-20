"""Local health metadata only; no credential reads or model requests."""
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.multi_ai_v2 import local_root
from app.production_bridge import ProductionBridge


def main():
    root = local_root(os.environ['LOCAL_RUN_ROOT'])
    parent = root
    while not parent.exists(): parent = parent.parent
    with tempfile.TemporaryDirectory(prefix='v2_health_', dir=parent) as folder:
        p = Path(folder) / 'write-check'; p.write_text('ok'); write_ok = p.read_text() == 'ok'
    repo = Path(__file__).resolve().parents[1]
    profile = json.loads((repo / 'config/production_profile.json').read_text(encoding='utf-8'))
    model = repo / profile['voice']['model_ref']
    report = {'python': sys.version.split()[0], 'virtual_environment': sys.prefix != sys.base_prefix,
              'pydantic': importlib.metadata.version('pydantic'), 'sqlite': sqlite3.sqlite_version,
              'local_run_root_valid': True, 'write_permissions': write_ok,
              'free_bytes': shutil.disk_usage(parent).free,
              'codex_executable': bool(shutil.which('codex')), 'codex_auth': 'UNVERIFIED',
              'antigravity_cli': bool(shutil.which('agy')), 'gemini_story_auth': 'UNVERIFIED',
              'gemini_visual_route': 'BLOCKED_UNSUPPORTED',
              'drive_path_exists': bool(os.getenv('GOOGLE_DRIVE_ROOT') and Path(os.environ['GOOGLE_DRIVE_ROOT']).is_dir()),
              'ffmpeg_on_path': bool(shutil.which('ffmpeg')), 'runner': 'UNVERIFIED',
              'required_voice_model_exists': model.is_file(), 'profile_voice': profile['voice']['voice_id'],
              'profile_runtime_consistency': 'UNVERIFIED', 'ui_port_required': False,
              'production_bridge': ProductionBridge.capabilities(), 'production_ready': False,
              'model_requests': 0}
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == '__main__': raise SystemExit(main())
