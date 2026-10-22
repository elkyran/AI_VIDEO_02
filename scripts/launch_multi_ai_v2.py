"""Small operator entrypoint over the existing v2 CLI; never grants permission."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ALIASES = {'start': 'run', 'review': 'approve'}
COMMANDS = ('plan', 'doctor', 'start', 'status', 'stop', 'resume', 'review', 'deliver',
            'prepare', 'inspect-artifacts', 'report')


def _blocked(code, action):
    print(json.dumps({'status': 'BLOCKED_UNSUPPORTED', 'error_code': code,
        'next_action': action, 'production_started': False, 'uploaded': False,
        'remote_sync': 'UNVERIFIED', 'truncated': False}, ensure_ascii=False))
    return 5


def main(argv=None, *, host_services=None):
    inventory=ROOT/'SOURCE_INVENTORY.json'
    if inventory.exists():
        from app.v2_source_package import verify
        verify(ROOT)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, help='Explicit owner-approved host JSON; no automatic studio scan')
    parser.add_argument('--execute-system', action='store_true')
    parser.add_argument('--authorize', action='store_true')
    parser.add_argument('--ticks', type=int)
    parser.add_argument('--review')
    parser.add_argument('--synthetic-inputs', help='Explicit fixture JSON for simulation only; never used as a live fallback')
    parser.add_argument('command', nargs='?', choices=COMMANDS, default='doctor')
    args = parser.parse_args(argv)
    if sys.version_info < (3, 11) or not Path(sys.executable).is_file():
        return _blocked('PYTHON_REQUIRED', 'Use the installed project Python 3.11 or later.')
    if not (ROOT/'app/multi_ai_v2.py').is_file() or importlib.util.find_spec('pydantic') is None:
        return _blocked('RUNTIME_REQUIRED', 'Use .venv-core/Scripts/python.exe; restore the documented dependencies separately.')
    try:
        from app.registry import StudioError
        from app.v2_run_config import load_config, no_links
        from app.v2_pipeline import system_main
        config = no_links(args.config)
        if not config.is_file():
            return _blocked('CONFIG_REQUIRED', 'Select an existing owner-approved host config JSON.')
        host_config = load_config(config)  # Bound schema and paths before a stateful CLI command.
        if args.ticks is not None and args.ticks < 1:
            return _blocked('INVALID_TICKS', 'Choose a positive bounded tick count.')
        if args.command == 'review' and not args.review:
            return _blocked('REVIEW_REQUIRED', 'Supply --review with the exact canonical owner-review JSON.')
        base = ['--config', str(config)]
        if args.synthetic_inputs:
            if host_config.mode != 'simulation':
                return _blocked('SYNTHETIC_INPUTS_FORBIDDEN', 'Synthetic fixtures require mode=simulation; live workers remain disabled.')
            file = no_links(args.synthetic_inputs)
            if not file.is_file() or file.stat().st_size > 2*1024**2:
                return _blocked('SYNTHETIC_INPUTS_REQUIRED', 'Select the exact small simulation fixture JSON.')
            base.extend(['--synthetic-inputs', str(file)])
        if args.command not in {'plan', 'doctor'}:
            # These are the same read-only commands exposed by the existing CLI.
            # Capture only their public JSON; no runtime/auth discovery is added.
            checks = {}
            preflight_commands = ('plan', 'doctor') if args.command in {'start', 'resume', 'review', 'deliver'} else ()
            for command in preflight_commands:
                output = io.StringIO()
                with redirect_stdout(output):
                    code = system_main([*base, command],host_services=host_services)
                report = json.loads(output.getvalue())
                checks[command] = report
                if code or report.get('blockers'):
                    print(json.dumps({'status': report.get('status', 'BLOCKED_UNSUPPORTED'),
                        'preflight': checks, 'next_action': 'Resolve the reported plan/doctor blocker before execution.',
                        'production_started': False, 'uploaded': False}, ensure_ascii=False))
                    return code or 5
            if not args.execute_system:
                return _blocked('SYSTEM_PERMISSION_REQUIRED',
                    'Review plan/doctor; add --execute-system only for a separately authorized operation and --authorize for new inputs.')
            base.append('--execute-system')
            if args.authorize:
                base.append('--authorize')
        command = ALIASES.get(args.command, args.command)
        if args.review:
            base.extend(['--review', str(no_links(args.review))])
        if args.ticks is not None:
            base.extend(['--ticks', str(args.ticks)])
        return system_main([*base, command],host_services=host_services)
    except (ValueError, OSError, ImportError, KeyError, TypeError):
        # Do not echo arbitrary config content, private exception diagnostics,
        # environment values, or credentials into the operator output.
        return _blocked('INPUT_OR_RUNTIME_INVALID',
            'Check the explicit config, installed core interpreter and documented run/input paths; no operation was authorized automatically.')
    except StudioError:
        return _blocked('INPUT_OR_RUNTIME_INVALID',
            'Check the explicit config and documented run/input paths; no operation was authorized automatically.')


if __name__ == '__main__':
    raise SystemExit(main())
