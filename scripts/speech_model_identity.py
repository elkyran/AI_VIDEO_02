"""Fingerprint the installed offline Whisper model without running inference."""
from __future__ import annotations
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import sys


def model_identity(name: str) -> dict:
    os.environ['HF_HUB_OFFLINE']='1'
    from faster_whisper.utils import download_model
    configured=Path(name)
    directory=(configured if configured.is_dir() else Path(download_model(name,local_files_only=True))).resolve()
    if not (directory/'model.bin').is_file():raise ValueError('The offline model has no model.bin')
    files={}
    for path in sorted(directory.rglob('*')):
        if not path.is_file():continue
        digest=hashlib.sha256()
        with path.open('rb') as source:
            for chunk in iter(lambda:source.read(1024*1024),b''):digest.update(chunk)
        files[path.relative_to(directory).as_posix()]=digest.hexdigest()
    versions={}
    for package in ('faster-whisper','ctranslate2','tokenizers','onnxruntime','numpy'):
        try:versions[package]=version(package)
        except PackageNotFoundError:versions[package]=None
    return {'name':name,'resolved_directory':str(directory),'files':files,'runtime_versions':versions}


if __name__=='__main__':
    if len(sys.argv)!=2:raise SystemExit('Usage: speech_model_identity.py MODEL_NAME_OR_PATH')
    print(json.dumps(model_identity(sys.argv[1]),ensure_ascii=True,sort_keys=True))
