"""Bounded offline checks with full logs and version-bound JSON evidence.

Does not discover all tests, synthesize speech or touch production registry.
The optional native 3-second fixture has its own explicit command.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]
GROUPS = {
    'contracts': ['tests.test_editorial', 'tests.test_production_bridge'],
    'queue': ['tests.test_multi_ai', 'tests.test_multi_ai_configuration'],
    'adapters': ['tests.test_ai_adapters'],
    'panel': ['tests.test_multi_ai_ui', 'tests.test_multi_ai_ui_review', 'tests.test_multi_ai_preview'],
    'mai05': ['tests.test_ai_accounts', 'tests.test_ai_cli_runtime', 'tests.test_multi_ai_accounts_ui',
              'tests.test_ai_codex_diagnostics', 'tests.test_gemini_free_route'],
    'legacy': ['tests.test_core', 'tests.test_cli', 'tests.test_library', 'tests.test_exchange',
               'tests.test_production_profile', 'tests.test_quality', 'tests.test_asr',
               'tests.test_asr_runtime', 'tests.test_speech_review', 'tests.test_fit_narration',
               'tests.test_multishot.MultiShotContractTests', 'tests.test_multishot.ReviewRightsTests',
               'tests.test_tts.TTSInputTests', 'tests.test_tts.SileroContinuityTests',
               'tests.test_replace_narration.NarrationValidationTests',
               'tests.test_timeline.TimelineTests.test_traversal_and_preserving_existing_builds'],
}


def revision():
    files = sorted(p for folder in ('app','scripts','tests') for p in (ROOT/folder).glob('*.py'))
    files += [ROOT/'multi_ai.cmd', ROOT/'pyproject.toml']
    values = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    digest = hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()
    return 'source-sha256:'+digest, values


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--groups', nargs='+', choices=GROUPS, default=list(GROUPS))
    parser.add_argument('--output', type=Path, default=ROOT/'reports/development/2026-10-18_MULTI_AI')
    parser.add_argument('--task-id', default='MULTI-AI-OFFLINE')
    args = parser.parse_args(argv)
    args.output = args.output.resolve()
    args.output.mkdir(parents=True,exist_ok=True)
    run = args.output/('checks_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:6])
    run.mkdir()
    version, hashes = revision()
    results=[]
    for group in args.groups:
        command=[sys.executable,'-X','utf8','-m','unittest','-v',*GROUPS[group]]
        start=time.monotonic()
        log=run/(group+'.log')
        with log.open('w',encoding='utf-8') as stream:
            try:
                result=subprocess.run(command,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,timeout=180,
                                      creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                code=result.returncode
            except subprocess.TimeoutExpired:
                stream.write('\nCHECK TIMEOUT (180 seconds); not passed.\n')
                code=124
        value={'group':group,'status':'passed' if code==0 else 'failed','exit_code':code,
               'elapsed_seconds':round(time.monotonic()-start,3),'log_ref':log.relative_to(ROOT).as_posix(),
               'command':command,'truncated':False}
        results.append(value)
        print(json.dumps({k:v for k,v in value.items() if k!='command'},ensure_ascii=False),flush=True)
    current,_=revision()
    report={'schema_version':1,'task_id':args.task_id,'revision':version,
            'status':'passed' if current==version and all(r['status']=='passed' for r in results) else 'failed',
            'source_unchanged_during_checks':current==version,'source_hashes':hashes,'checks':results,
            'model_requests':0,'paid_api_requests':0,'creative_quality':None,'live_connections_verified':0,
            'truncated':False}
    path=run/'checks.json'
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':report['status'],'report':str(path),'revision':version}),flush=True)
    return 0 if report['status']=='passed' else 1


if __name__=='__main__':
    raise SystemExit(main())
