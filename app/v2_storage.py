"""Immutable allowlisted artifacts; verified sync-folder copy is not cloud upload."""
from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import uuid
import subprocess
import sys
import errno

from .registry import canonical_json, content_hash, workspace_path
from .v2_contracts import Artifact


def source_binding(root):
    """Hash the complete requested core source scope, including untracked edits."""
    root = Path(root).resolve()
    paths = set()
    for folder in ('app', 'scripts', 'tests'):
        paths.update((root / folder).rglob('*.py'))
    paths.update((root / 'scripts').rglob('*.ps1'))
    schemas = set((root / 'config/multi_ai_v2_schemas').rglob('*.json'))
    paths.update(schemas)
    paths.update(root / p for p in ('pyproject.toml', '.env.example', 'config/production_profile.json'))
    hashes = {p.relative_to(root).as_posix(): content_hash(p) for p in sorted(paths)}
    schema_hashes = {p.relative_to(root).as_posix(): hashes[p.relative_to(root).as_posix()] for p in sorted(schemas)}
    source_sha = hashlib.sha256(canonical_json(hashes).encode()).hexdigest()
    schema_sha = hashlib.sha256(canonical_json(schema_hashes).encode()).hexdigest()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.DEVNULL).decode('utf-8').rstrip('\r\n')
    try:
        state = git('status', '--porcelain', '--untracked-files=all')
        metadata = {'git_commit_sha': git('rev-parse', 'HEAD'), 'git_tree_sha': git('rev-parse', 'HEAD^{tree}'),
                    'branch': git('branch', '--show-current'), 'dirty': bool(state), 'dirty_state': state.splitlines()}
    except subprocess.CalledProcessError:
        metadata = {'git_commit_sha': None, 'git_tree_sha': None, 'branch': None, 'dirty': None, 'dirty_state': None}
    return {**metadata, 'revision': 'source-sha256:' + source_sha,
            'source_manifest_sha256': source_sha, 'schema_manifest_sha256': schema_sha,
            'source_hashes': hashes, 'schema_hashes': schema_hashes}


class EvidenceRun:
    """Exclusive UTC+UUID directory, exclusive writes and a final hash manifest.

    manifest.json hashes every other evidence file; it cannot hash itself.
    Its own SHA-256 is returned to the caller and recorded outside this directory.
    """
    def __init__(self, parent, *, root):
        parent = Path(parent).resolve()
        if any((p / 'manifest.json').is_file() for p in (parent, *parent.parents)):
            raise ValueError('Output must be an evidence parent, not a sealed evidence directory')
        self.binding = source_binding(root)
        self.checked_at = datetime.now(timezone.utc).isoformat()
        self.folder = parent / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '_' + uuid.uuid4().hex)
        self.folder.mkdir(parents=True, exist_ok=False)
        self.commands = []
        self.sealed = False

    def write_json(self, name, value):
        if self.sealed: raise ValueError('Evidence already sealed')
        path = workspace_path(self.folder, name, for_write=True)
        with path.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')

    def record_command(self, command, exit_code, *, elapsed_seconds=None, test_count=None, model_requests=0, skipped_count=0):
        self.commands.append({'command': list(command), 'exit_code': exit_code,
                              'elapsed_seconds': elapsed_seconds, 'test_count': test_count,
                              'model_requests': model_requests, 'skipped_count': skipped_count})

    def finalize(self, *, status, model_requests=0, paid_api_requests=0, production_started=False):
        if self.sealed: raise ValueError('Evidence already sealed')
        files = {p.relative_to(self.folder).as_posix(): {'sha256': content_hash(p), 'bytes': p.stat().st_size}
                 for p in sorted(self.folder.rglob('*')) if p.is_file()}
        value = {**self.binding, 'checked_at': self.checked_at, 'status': status,
                 'invocation': [sys.executable, *sys.argv],
                 'commands': self.commands, 'test_count': sum(c['test_count'] or 0 for c in self.commands),
                 'skipped_count': sum(c['skipped_count'] for c in self.commands),
                 'model_requests': model_requests, 'paid_api_requests': paid_api_requests,
                 'production_started': production_started, 'files': files,
                 'manifest_self_hash': 'Excluded to avoid self-reference; SHA-256 reported by caller'}
        self.write_json('manifest.json', value)
        self.sealed = True
        return content_hash(self.folder / 'manifest.json')


class ArtifactStore:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def put_json(self, run_id, value, *, simulated):
        # Resolve before any mkdir or write; write_generated_json refuses overwrite.
        from .registry import write_generated_json
        import hashlib
        data = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()
        digest = hashlib.sha256(data).hexdigest()
        relative = f'runs/{run_id}/artifacts/{digest}.json'
        artifact = Artifact(artifact_id='artifact_' + digest[:24], run_id=run_id, version=1,
                            path=relative, sha256=digest, size=len(data), simulated=simulated)
        path = self._owned_path(self.root, artifact, for_write=True)
        if path.exists():
            if content_hash(path) != digest:
                raise ValueError('Existing artifact content differs from its address')
        else:
            write_generated_json(self.root, relative, value)
        return artifact

    def put_file(self, run_id, source, *, category, source_root=None, version=1, simulated):
        """Explicit offline file import; no database, credential or directory mirroring."""
        categories={'sources','canon','story','voice','visuals','edit','previews','reviews','final','manifests','backups'}
        if category not in categories:
            raise ValueError('Unknown release category')
        source=Path(source).resolve()
        allowed=Path(source_root or self.root).resolve()
        if not source.is_relative_to(allowed) or not source.is_file():
            raise ValueError('Release file must be under its explicit host source root')
        if source.suffix.lower() not in {'.json','.txt','.md','.srt','.mlt','.png','.jpg','.jpeg','.wav','.mp4'}:
            raise ValueError('This file type is not part of an offline media release')
        if source.stat().st_size>250_000_000:
            raise ValueError('Offline release file exceeds the bounded media limit')
        sha=content_hash(source)
        artifact=Artifact(artifact_id=f'file_{category}_v{version}_{sha[:32]}',run_id=run_id,version=version,
            path=f'runs/{run_id}/{category}/{sha}{source.suffix.lower()}',sha256=sha,
            size=source.stat().st_size,simulated=simulated)
        destination=self._owned_path(self.root,artifact,for_write=True)
        destination.parent.mkdir(parents=True,exist_ok=True)
        self._fallback_copy(source,destination,artifact)
        self.verify(artifact)
        return artifact

    @staticmethod
    def _owned_path(root, artifact, *, for_write=False):
        artifact = Artifact.model_validate(artifact.model_dump() if isinstance(artifact, Artifact) else artifact)
        base = root / 'runs' / artifact.run_id
        p = workspace_path(root, artifact.path, for_write=for_write)
        if base.resolve() != base or not p.is_relative_to(base):
            raise ValueError('Artifact symlink leaves its owning run')
        return p

    def verify(self, artifact):
        p = self._owned_path(self.root, artifact)
        if not p.is_file() or p.stat().st_size != artifact.size or content_hash(p) != artifact.sha256:
            raise ValueError('Artifact missing or hash/size mismatch')
        return p

    def copy_to_drive(self, artifact, drive_root):
        """Explicit configured mount only. Never calls Google API or claims remote sync."""
        source = self.verify(artifact)
        root = Path(drive_root).resolve()
        if not root.is_dir():
            raise ValueError('Configured Drive root is unavailable')
        if root == self.root or self.root.is_relative_to(root) or root.is_relative_to(self.root):
            raise ValueError('Drive and local registry roots must be separate')
        destination = self._owned_path(root, artifact, for_write=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            temporary = destination.with_name(destination.name + '.' + uuid.uuid4().hex + '.tmp')
            staged = False
            try:
                with source.open('rb') as src, temporary.open('xb') as dst:
                    staged = True
                    shutil.copyfileobj(src, dst)
                if temporary.stat().st_size != artifact.size or content_hash(temporary) != artifact.sha256:
                    raise ValueError('Drive staging copy failed integrity check')
                # link creates exclusively; never replace an existing cloud/user artifact.
                try:
                    os.link(temporary, destination)
                except FileExistsError:
                    pass
                except OSError:
                    self._fallback_copy(temporary, destination, artifact)
            finally:
                if staged: temporary.unlink(missing_ok=True)
        if destination.stat().st_size != artifact.size or content_hash(destination) != artifact.sha256:
            raise ValueError('Drive destination conflicts with the expected artifact')
        return {'status': 'COPIED_VERIFIED', 'remote_sync': 'UNVERIFIED', 'uploaded': False,
                'path': artifact.path, 'sha256': artifact.sha256, 'size': artifact.size}

    @staticmethod
    def _fallback_copy(source, destination, artifact):
        identity = None
        try:
            try:
                stream = destination.open('xb')
            except FileExistsError:
                return  # Existing destination is verified by caller, never replaced/deleted.
            with stream:
                stat = os.fstat(stream.fileno()); identity = (stat.st_dev, stat.st_ino)
                with source.open('rb') as src: shutil.copyfileobj(src, stream)
                stream.flush()
                try: os.fsync(stream.fileno())
                except OSError as exc:
                    if exc.errno not in {errno.EINVAL, errno.ENOSYS, errno.ENOTSUP}: raise
            if destination.stat().st_size != artifact.size or content_hash(destination) != artifact.sha256:
                raise ValueError('Drive fallback copy failed integrity check')
        except BaseException:
            if identity is not None:
                try:
                    stat = destination.lstat()
                    if (stat.st_dev, stat.st_ino) == identity: destination.unlink()
                except FileNotFoundError:
                    pass
            raise
