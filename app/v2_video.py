"""Host-only, offline capability boundary. No model calls or media QA here."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import stat

from .registry import StudioError, canonical_json
from .v2_contracts import TrustedVideoProbe, Artifact

BINDING_FIELDS = ('provider', 'provider_profile_id', 'account_fingerprint', 'client',
                  'client_version', 'requested_model', 'returned_model', 'control_video_hash')
# The host owns this challenge specification; receipts cannot redefine it.
CONTROL_FEATURES = {'colors': [['red', 0, 3], ['blue', 3, 6], ['green', 6, 9]],
                    'text': 'CONTROL-VIDEO-OK', 'audio_signal_seconds': 4.5}
MAX_FUTURE_SKEW = timedelta(seconds=60)


class VideoEvidence:
    def __init__(self, root, store, clock=None):
        self.root = Path(root).resolve() if root is not None else None
        self.store = store
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def validate(self, value):
        probe = TrustedVideoProbe.model_validate(value)
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError('Host clock must be timezone-aware')
        checked = datetime.fromisoformat(probe.checked_at.replace('Z', '+00:00'))
        expires = datetime.fromisoformat(probe.expires_at.replace('Z', '+00:00'))
        if checked > now + MAX_FUTURE_SKEW or expires <= now:
            raise ValueError('Video evidence is future-dated or expired')
        if probe.status == 'VERIFIED':
            self.receipt(probe)
        return probe

    def receipt(self, probe):
        if self.root is None or not self.root.is_dir():
            raise ValueError('No host-owned evidence root configured')
        # Require one exact receipt, separate from model-writable run artifacts.
        if len(probe.evidence_refs) != 1:
            raise ValueError('Exactly one host receipt is required')
        path = self.root / probe.evidence_refs[0]
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(self.root) or not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError('Receipt must be a regular file within the evidence root')
        for part in (path, *path.parents):
            if part == self.root:
                break
            if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
                raise ValueError('Receipt links are forbidden')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != probe.result_hash:
            raise ValueError('Host receipt hash changed')
        receipt = json.loads(raw)
        binding = {field: getattr(probe, field) for field in BINDING_FIELDS}
        if (receipt.get('evidence_id') != probe.evidence_id or receipt.get('binding') != binding
                or receipt.get('expected_features') != CONTROL_FEATURES
                or receipt.get('observed_features') != CONTROL_FEATURES):
            raise ValueError('Host receipt does not prove the bound control challenge')

    def record(self, registry, value):
        probe = TrustedVideoProbe.model_validate(value)
        encoded = canonical_json(probe.model_dump())
        prior = registry.db.execute('SELECT evidence FROM ai_v2_capabilities WHERE capability_key=?',
                                    (probe.evidence_id,)).fetchone()
        if prior and prior[0] != encoded:
            raise StudioError('EVIDENCE_CONFLICT', 'Evidence ID already has immutable content')
        self.validate(probe)
        if not prior:
            registry.db.execute('INSERT INTO ai_v2_capabilities VALUES(?,?)', (probe.evidence_id, encoded))
        return probe.evidence_id

    def accept(self, registry, evidence_id, binding, video_artifact, materials):
        try:
            row = registry.db.execute('SELECT evidence FROM ai_v2_capabilities WHERE capability_key=?',
                                      (evidence_id,)).fetchone()
            if not row or registry.db.execute('SELECT 1 FROM ai_v2_capability_revocations WHERE evidence_id=?',
                                              (evidence_id,)).fetchone():
                raise ValueError('Missing or revoked evidence')
            probe = self.validate(json.loads(row[0]))
            if probe.status != 'VERIFIED' or binding != {field: getattr(probe, field) for field in BINDING_FIELDS}:
                raise ValueError('Video capability binding mismatch')
            artifact = Artifact.model_validate(video_artifact)
            if artifact.simulated or Path(artifact.path).suffix.lower() not in {'.mp4', '.mov', '.mkv', '.webm'}:
                raise ValueError('A real video artifact is required')
            self.store.verify(artifact)
            if artifact.path not in materials:
                raise ValueError('Video artifact was not actually reviewed')
        except (ValueError, TypeError, KeyError, OSError) as exc:
            raise StudioError('VIDEO_CAPABILITY_UNVERIFIED', 'Full video requires current matching host evidence and video material') from exc
