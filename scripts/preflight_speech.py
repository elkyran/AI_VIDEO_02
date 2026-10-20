"""Run/cache speech QA for a completed build while its video is rendering."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.operations import Context
from app.registry import StudioError, workspace_path
from app.speech_review import review_build_speech


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--build-job-id',required=True)
    args=parser.parse_args()
    ctx=Context(args.workspace,args.build_job_id)
    if ctx.job['operation']!='build' or ctx.job['state']!='done':
        raise StudioError('DEPENDENCY_PENDING','Speech preflight requires a completed build')
    # Context.run supplies the existing cancellation/timeout/process handling;
    # this helper never calls Context.progress or updates the completed job.
    ctx.directory=workspace_path(ctx.root,f'preflight/speech/{args.build_job_id}',for_write=True)
    ctx.directory.mkdir(parents=True,exist_ok=True)
    result=review_build_speech(ctx,ctx.job['result'])
    print(json.dumps({key:str(value) if isinstance(value,Path) else value for key,value in result.items()},ensure_ascii=True))


if __name__=='__main__':
    try:main()
    except StudioError as error:
        print(json.dumps(error.as_dict(),ensure_ascii=True));raise SystemExit(2)
