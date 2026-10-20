"""Bounded, non-authenticating checks of installed Windows CLI clients.

No installation, update, login, credential reads or model calls occur here.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile

from .ai_adapters import sanitize


def _run(command, *args, provider):
    try:
        from .ai_accounts import isolated_environment
        with tempfile.TemporaryDirectory(prefix='ai_cli_probe_') as folder:
            result = subprocess.run([*command, *args], capture_output=True, text=True,
                                    cwd=folder, env=isolated_environment(provider, Path(folder)),
                                    encoding='utf-8', errors='replace', timeout=25,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return result.returncode, sanitize(result.stdout + '\n' + result.stderr)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, sanitize(str(exc))


def discover_clients(*, include_historical=False):
    """Resolve native binaries, and Node + the actual Gemini package entrypoint.

    Script shims are never executed through a shell. Capabilities require the
    installed client's help; unknown or changed versions remain closed.
    """
    home = Path(os.environ.get('USERPROFILE', Path.home()))
    codex = shutil.which('codex.exe') or shutil.which('codex')
    claude = shutil.which('claude.exe') or shutil.which('claude')
    if not claude and (home / '.local/bin/claude.exe').is_file():
        claude = str(home / '.local/bin/claude.exe')
    if not claude:
        packages = Path(os.environ.get('LOCALAPPDATA', home)) / 'Microsoft/WinGet/Packages'
        found = sorted(packages.glob('Anthropic.ClaudeCode_*/claude.exe'))
        if len(found) == 1:
            claude = str(found[0])
    node = shutil.which('node.exe') or shutil.which('node')
    gemini = Path(os.environ.get('APPDATA', home)) / 'npm/node_modules/@google/gemini-cli/bundle/gemini.js'
    commands = {'codex': [codex] if codex else [], 'claude': [claude] if claude else [],
                'gemini': [node, str(gemini)] if node and gemini.is_file() else []}
    clients = {}
    required = {'codex': ('--ignore-user-config', '--ignore-rules', '--output-schema'),
                'claude': ('--output-format', '--json-schema', '--tools'),
                'gemini': ('--output-format', '--approval-mode', '--policy')}
    for provider, command in commands.items():
        if provider == 'claude' and not include_historical:
            continue
        record = {'command': command, 'version': None, 'supported': False,
                  'isolation_verified': False, 'installation': 'missing', 'reason': 'CLI не найден'}
        clients[provider] = record
        if not command or not all(Path(p).is_file() for p in command):
            continue
        if Path(command[0]).suffix.lower() in {'.cmd', '.ps1', '.bat'}:
            record['reason'] = 'Нужен фактический бинарник, а не shell shim'
            continue
        code, version = _run(command, '--version', provider=provider)
        version_pattern = {'codex': r'^codex-cli \S+$', 'claude': r'^\d+\.\d+\.\d+.*\(Claude Code\)$',
                           'gemini': r'^\d+\.\d+\.\d+[^\s]*$'}[provider]
        version_line = next((line.strip() for line in version.splitlines()
                             if re.fullmatch(version_pattern, line.strip())), None)
        record.update(version=version_line, version_exit_code=code, version_diagnostic=version)
        if code:
            record.update(installation='broken', reason=version[:1500])
            continue
        code, help_text = _run(command, *(['exec', '--help'] if provider == 'codex' else ['--help']), provider=provider)
        supported = code == 0 and version_line is not None and all(flag in help_text for flag in required[provider])
        record.update(installation='installed', supported=supported,
                      help_exit_code=code, reason=None if supported else 'Не подтверждены нужные флаги установленной версии')
        if provider == 'codex':
            record.update(login_args=['login'], status_args=['login', 'status'])
            # Home/auth restrictions are documented; actual identity isolation is
            # separately checked by the account manager after each login.
            record['isolation_verified'] = supported and version_line == 'codex-cli 0.163.0-alpha.10.2'
        elif provider == 'claude':
            ac, auth_help = _run(command, 'auth', '--help', provider=provider)
            lc, login_help = _run(command, 'auth', 'login', '--help', provider=provider)
            sc, status_help = _run(command, 'auth', 'status', '--help', provider=provider)
            supported = (supported and ac == lc == sc == 0 and 'status' in auth_help and 'login' in auth_help
                         and '--claudeai' in login_help and '--json' in status_help)
            record.update(supported=supported, login_args=['auth', 'login', '--claudeai'],
                          status_args=['auth', 'status', '--json'], isolation_verified=supported and version_line == '2.1.302 (Claude Code)')
        else:
            record.update(login_args=[], status_args=None,
                          isolation_verified=supported and version_line == '0.51.0',
                          access_route_supported=False,
                          access_route='gemini_cli_google_individual',
                          access_route_reason='Google прекратил поддержку входа Google individual в Gemini CLI с 18.06.2026. Нужен другой официально поддерживаемый способ подключения; API и Vertex не включаются.',
                          access_route_evidence='https://developers.google.com/gemini-code-assist/docs/deprecations/code-assist-individuals',
                          access_route_checked_at='2026-10-18')
        if not record['isolation_verified']:
            record['reason'] = 'Версия или механизм изоляции требует повторной проверки'
    return clients
