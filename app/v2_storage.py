"""Immutable allowlisted artifacts; verified sync-folder copy is not cloud upload."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import uuid

from .registry import canonical_json, content_hash, workspace_path
from .v2_contracts import Artifact


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
        path = workspace_path(self.root, relative, for_write=True)
        if path.exists():
            if content_hash(path) != digest:
                raise ValueError('Existing artifact content differs from its address')
        else:
            write_generated_json(self.root, relative, value)
        return Artifact(artifact_id='artifact_' + digest[:24], run_id=run_id, version=1,
                        path=relative, sha256=digest, size=len(data), simulated=simulated)

    def verify(self, artifact):
        p = workspace_path(self.root, artifact.path)
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
        destination = workspace_path(root, artifact.path, for_write=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            temporary = destination.with_name(destination.name + '.' + uuid.uuid4().hex + '.tmp')
            try:
                with source.open('rb') as src, temporary.open('xb') as dst:
                    shutil.copyfileobj(src, dst)
                if temporary.stat().st_size != artifact.size or content_hash(temporary) != artifact.sha256:
                    raise ValueError('Drive staging copy failed integrity check')
                # link creates exclusively; never replace an existing cloud/user artifact.
                try:
                    os.link(temporary, destination)
                except FileExistsError:
                    pass
            finally:
                temporary.unlink(missing_ok=True)
        if destination.stat().st_size != artifact.size or content_hash(destination) != artifact.sha256:
            raise ValueError('Drive destination conflicts with the expected artifact')
        return {'status': 'COPIED_VERIFIED', 'remote_sync': 'UNVERIFIED', 'uploaded': False,
                'path': artifact.path, 'sha256': artifact.sha256, 'size': artifact.size}
