"""Verified, resumable reuse of speech QA; this module never accepts a video."""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import time
import uuid

from .registry import StudioError, canonical_json, content_hash, workspace_path


def _json(path):
    from .operations import read_json
    return read_json(path)


def _save(path,value):
    from .operations import save_json
    save_json(path,value)


def prepare_speech_manifest(ctx,build_result: dict) -> dict:
    """Use immutable build timing and original measured WAVs, before or after export."""
    from .operations import verify_build
    verify_build(ctx,build_result)
    timeline=_json(workspace_path(ctx.root,build_result['timeline_json']))
    audio_result=ctx.parent(build_result['audio_job_id'],'synthesize')
    manifest_path=workspace_path(ctx.root,audio_result['manifest'])
    if content_hash(manifest_path)!=audio_result['manifest_sha256']:
        raise StudioError('ASSET_CHANGED','Audio manifest changed before speech QA')
    if audio_result.get('script_sha256')!=build_result.get('script_sha256'):
        raise StudioError('STALE_AUDIO','Speech QA input belongs to another script revision')
    manifest=_json(manifest_path)
    entries=manifest.get('segments')
    if not isinstance(entries,list) or len(entries)!=len(timeline['segments']):
        raise StudioError('SPEECH_INPUT_INVALID','Narration blocks do not match the timeline')
    cursor=0.0
    for entry,segment in zip(entries,timeline['segments']):
        if entry.get('block_id')!=segment['id'] or entry.get('text')!=segment['text']:
            raise StudioError('STALE_AUDIO','Narration order or text differs from the timeline')
        path=workspace_path(ctx.root,entry['path'])
        if path.suffix.lower()!='.wav' or not path.is_file() or content_hash(path)!=entry.get('wav_hash'):
            raise StudioError('ASSET_CHANGED','Narration WAV changed before speech QA')
        duration=segment['duration']
        if isinstance(duration,bool) or not isinstance(duration,(float,int)) or not math.isfinite(duration) or duration<=0:
            raise StudioError('SPEECH_INPUT_INVALID','Invalid timeline duration')
        entry['path']=str(path);entry['start_seconds']=cursor
        cursor+=duration
    terms=ctx.config.get('pronunciation_terms',[])
    if not isinstance(terms,list) or any(not isinstance(term,str) for term in terms):
        raise StudioError('SPEECH_INPUT_INVALID','pronunciation_terms must be a list of strings')
    manifest['workspace_root']=str(Path(ctx.root).resolve())
    manifest['critical_terms']=terms
    # Last-block padding also belongs to the timing identity, even though the ASR
    # worker only consumes WAV durations and block starts.
    manifest['timeline_duration_seconds']=cursor
    manifest['timeline_segments']=[{'id':s['id'],'duration':s['duration']} for s in timeline['segments']]
    return manifest


def _model_identity(ctx) -> dict:
    runner=Path(__file__).resolve().parents[1]/'scripts/speech_model_identity.py'
    log=ctx.run([ctx.tool('asr_python'),str(runner),ctx.config.get('asr_model','medium')],
                log_name='speech_model_identity.log',timeout=300)
    try:identity=json.loads(log.read_text(encoding='utf-8').strip().splitlines()[-1])
    except (ValueError,IndexError) as exc:raise StudioError('SPEECH_MODEL_INVALID','Cannot fingerprint the installed ASR model') from exc
    if not isinstance(identity,dict) or not identity.get('files',{}).get('model.bin'):
        raise StudioError('SPEECH_MODEL_INVALID','The ASR model fingerprint is incomplete')
    return identity


def _workspace_absolute(root: Path,value: str) -> Path:
    path=Path(value)
    if not path.is_absolute():raise StudioError('PATH_UNSAFE','Expected a workspace-bounded absolute path')
    try:relative=path.resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError as exc:raise StudioError('PATH_UNSAFE','Speech path leaves the workspace') from exc
    return workspace_path(root,relative)


def _verify_wavs(root: Path,manifest: dict) -> None:
    for entry in manifest['segments']:
        path=_workspace_absolute(root,entry['path'])
        if path.suffix.lower()!='.wav' or not path.is_file() or content_hash(path)!=entry['wav_hash']:
            raise StudioError('ASSET_CHANGED','Narration WAV changed during speech QA')


@contextmanager
def _cache_lock(ctx,path: Path,timeout=3600):
    """OS lock is released on process exit; preflight and QA can safely overlap."""
    path.parent.mkdir(parents=True,exist_ok=True)
    started=time.monotonic()
    with path.open('a+b') as handle:
        if handle.seek(0,os.SEEK_END)==0:handle.write(b'\0');handle.flush()
        acquired=False;waited=False
        try:
            while not acquired:
                if hasattr(ctx,'check'):ctx.check()
                handle.seek(0)
                try:
                    if os.name=='nt':
                        import msvcrt
                        msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                    acquired=True
                except OSError:
                    waited=True
                    if time.monotonic()-started>timeout:
                        raise StudioError('SPEECH_CACHE_BUSY','Another speech review still owns this cache key')
                    time.sleep(.25)
            yield waited
        finally:
            if acquired:
                handle.seek(0)
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(),fcntl.LOCK_UN)


def _validate_report(root: Path,paths: dict,manifest: dict,model_name: str,execution: dict) -> None:
    report=_json(paths['report'])
    if report.get('model')!=model_name or report.get('status') not in ('automated_pass','automated_warning'):
        raise StudioError('SPEECH_RESULT_INVALID','Unexpected speech model or result status')
    if any(report.get(key)!=execution[key] for key in ('device','compute_type','dll_directories')):
        raise StudioError('SPEECH_RESULT_INVALID','Speech execution settings differ from the cache key')
    rows=report.get('segments')
    if not isinstance(rows,list) or len(rows)!=len(manifest['segments']):
        raise StudioError('SPEECH_RESULT_INVALID','Speech result omits narration blocks')
    for row,entry in zip(rows,manifest['segments']):
        if (row.get('block_id')!=entry['block_id'] or row.get('text')!=entry['text']
                or row.get('start_seconds')!=entry['start_seconds']
                or _workspace_absolute(root,row['path'])!=Path(entry['path'])):
            raise StudioError('SPEECH_RESULT_INVALID','Speech result does not match its input')
    for key in ('subtitles','raw_subtitles'):
        recorded=Path(report[key]['path'])
        if not recorded.is_absolute():recorded=paths['report'].parent/recorded
        if _workspace_absolute(root,str(recorded))!=paths[key]:
            raise StudioError('SPEECH_RESULT_INVALID','Speech result points to unrelated subtitles')


def _cached_result(ctx,cache_dir: Path,key: str,manifest: dict,model_name: str,execution: dict):
    receipt_path=cache_dir/'receipt.json'
    if not receipt_path.is_file():return None
    try:
        receipt=_json(receipt_path)
        if receipt.get('cache_key')!=key:return None
        paths={}
        if set(receipt['artifacts'])!={'report','subtitles','raw_subtitles'}:return None
        for kind,record in receipt['artifacts'].items():
            path=workspace_path(ctx.root,record['path'])
            if not path.is_relative_to(cache_dir/'runs') or not path.is_file() or content_hash(path)!=record['sha256']:
                return None
            paths[kind]=path
        _validate_report(ctx.root,paths,manifest,model_name,execution)
        return {**paths,'cache_key':key,'cache_hit':True,
                'artifact_hashes':{kind:record['sha256'] for kind,record in receipt['artifacts'].items()}}
    except (StudioError,OSError,ValueError,KeyError,TypeError):
        # An incomplete or altered receipt is never evidence of completed work.
        return None


def review_build_speech(ctx,build_result: dict) -> dict:
    """Return verified Path artifacts, reusing only exactly matching evidence.

    Required context: root, directory, config, parent(), tool(), run(). check()
    is optional for a headless caller. No queue state or progress is mutated.
    Cached warnings are retained; final QA still applies every existing check.
    """
    if not ctx.config.get('asr_python'):
        raise StudioError('CAPABILITY_MISSING','Configure asr_python before speech preflight')
    manifest=prepare_speech_manifest(ctx,build_result)
    from scripts.transcribe_audio import validate_runtime_options
    try:
        execution=validate_runtime_options(ctx.config.get('asr_device','cpu'),ctx.config.get('asr_compute_type','int8'),
                                           ctx.config.get('asr_dll_directories',[]))
    except ValueError as exc:raise StudioError('SPEECH_RUNTIME_INVALID',str(exc)) from exc
    model_name=ctx.config.get('asr_model','medium')
    identity=_model_identity(ctx)
    runner=Path(__file__).resolve().parents[1]/'scripts/transcribe_audio.py'
    identity_runner=runner.with_name('speech_model_identity.py')
    runtime=Path(ctx.tool('asr_python'))
    payload={'cache_schema':'1.0','manifest':manifest,'model':identity,
             'runner_sha256':content_hash(runner),'identity_runner_sha256':content_hash(identity_runner),
             'python_path':str(runtime.resolve()),'python_sha256':content_hash(runtime),'cpu_threads':4,
             'execution':execution}
    key=hashlib.sha256(canonical_json(payload).encode('utf-8')).hexdigest()
    cache_dir=workspace_path(ctx.root,f'cache/speech_review/{key}',for_write=True)
    with _cache_lock(ctx,cache_dir/'review.lock') as waited:
        if waited and _model_identity(ctx)!=identity:
            raise StudioError('ASSET_CHANGED','Speech model changed while waiting for the cache')
        if prepare_speech_manifest(ctx,build_result)!=manifest:
            raise StudioError('ASSET_CHANGED','Speech inputs changed while waiting for the cache')
        _verify_wavs(ctx.root,manifest)
        cached=_cached_result(ctx,cache_dir,key,manifest,model_name,execution)
        if cached:return cached
        attempt=workspace_path(ctx.root,f'cache/speech_review/{key}/runs/{uuid.uuid4().hex}',for_write=True)
        attempt.mkdir(parents=True)
        input_path=attempt/'asr_input.json';_save(input_path,manifest)
        paths={'report':attempt/'asr_report.json','subtitles':attempt/'subtitles.srt','raw_subtitles':attempt/'subtitles_asr.srt'}
        arguments=[str(runtime),str(runner),'--manifest',str(input_path),'--output',str(paths['report']),
                   '--subtitles',str(paths['subtitles']),'--model',model_name,'--cpu-threads','4',
                   '--device',execution['device'],'--compute-type',execution['compute_type']]
        for directory in execution['dll_directories']:arguments+=['--dll-directory',directory]
        ctx.run(arguments,log_name='asr.log')
        _verify_wavs(ctx.root,manifest)
        if content_hash(runner)!=payload['runner_sha256'] or _model_identity(ctx)!=identity:
            raise StudioError('ASSET_CHANGED','Speech model or worker changed during review')
        if prepare_speech_manifest(ctx,build_result)!=manifest:
            raise StudioError('ASSET_CHANGED','Speech inputs changed during review')
        _validate_report(ctx.root,paths,manifest,model_name,execution)
        artifacts={kind:{'path':path.relative_to(Path(ctx.root).resolve()).as_posix(),'sha256':content_hash(path)} for kind,path in paths.items()}
        _save(cache_dir/'receipt.json',{'cache_key':key,'input_identity':payload,'artifacts':artifacts})
        return {**paths,'cache_key':key,'cache_hit':False,
                'artifact_hashes':{kind:record['sha256'] for kind,record in artifacts.items()}}
