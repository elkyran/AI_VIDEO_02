"""Host-only, offline capability boundary. No model calls or media QA here."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import stat

from .registry import StudioError, canonical_json
from .v2_contracts import TrustedVideoProbe, Artifact, HostReviewDelivery
from .contracts import relative_path

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

    def read_receipt(self, ref, sha256):
        if self.root is None or not self.root.is_dir():
            raise ValueError('No host-owned evidence root configured')
        relative_path(ref)
        path = self.root / ref
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(self.root) or not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError('Receipt must be a regular file within the evidence root')
        for part in (path, *path.parents):
            if part == self.root:
                break
            if part.is_symlink() or getattr(part.lstat(), 'st_file_attributes', 0) & 0x400:
                raise ValueError('Receipt links are forbidden')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != sha256:
            raise ValueError('Host receipt hash changed')
        return json.loads(raw)

    def receipt(self, probe):
        if len(probe.evidence_refs) != 1:
            raise ValueError('Exactly one host receipt is required')
        receipt = self.read_receipt(probe.evidence_refs[0], probe.result_hash)
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
        except (ValueError, TypeError, KeyError, OSError, StudioError) as exc:
            raise StudioError('VIDEO_CAPABILITY_UNVERIFIED', 'Full video requires current matching host evidence and video material') from exc

    def delivery(self, registry, result, contract, task, attempt, review, account_binding):
        """Bind this exact response/file/request; capability alone is insufficient."""
        try:
            if result.simulated or result.host_review_delivery is None:
                raise ValueError('A real review needs a separate host delivery envelope')
            delivery = HostReviewDelivery.model_validate(result.host_review_delivery)
            # Only the immutable launch request identifies the login generation.
            # AccountManager may already describe a different login at completion.
            if not isinstance(account_binding, dict) or set(account_binding) != {'identity_hash', 'auth_generation'}:
                raise ValueError('Immutable request account binding is missing or malformed')
            identity, generation = account_binding['identity_hash'], account_binding['auth_generation']
            if (not isinstance(identity, str) or len(identity) != 64 or any(c not in '0123456789abcdef' for c in identity)
                    or type(generation) is not int or generation < 1):
                raise ValueError('Immutable request account binding is invalid')
            account_hash = hashlib.sha256(canonical_json(account_binding).encode('utf-8')).hexdigest()
            if delivery.account_fingerprint != account_hash:
                raise ValueError('Delivery does not bind the request login generation')
            if any(c.simulated for c in delivery.delivered_coverage):
                raise ValueError('Live delivery cannot attest simulated coverage')
            if registry.db.execute('SELECT 1 FROM ai_v2_delivery_revocations WHERE delivery_id=?',
                                   (delivery.delivery_id,)).fetchone():
                raise ValueError('Delivery was revoked')
            now = self.clock()
            requested, completed, expires = [datetime.fromisoformat(t.replace('Z', '+00:00')) for t in
                (delivery.requested_at, delivery.completed_at, delivery.expires_at)]
            if now.tzinfo is None or requested > now + MAX_FUTURE_SKEW or completed > now + MAX_FUTURE_SKEW or expires <= now:
                raise ValueError('Delivery is expired or future-dated')
            if (delivery.task_id != result.task_id or delivery.run_id != result.run_id
                    or delivery.attempt_id != result.acceptance_key
                    or delivery.input_version != result.input_version or delivery.input_hashes != result.input_hashes
                    or delivery.provider != attempt['provider'] or delivery.provider_profile_id != attempt['profile_id']
                    or delivery.provider_profile_id != contract.provider_profile_id
                    or contract.requested_model != delivery.requested_model):
                raise ValueError('Delivery identity does not match this task/attempt')
            body = delivery.model_dump(exclude={'receipt_ref', 'receipt_sha256'})
            if self.read_receipt(delivery.receipt_ref, delivery.receipt_sha256) != body:
                raise ValueError('Delivery envelope differs from the host receipt')
            if hashlib.sha256(canonical_json(review.model_dump()).encode('utf-8')).hexdigest() != delivery.review_payload_sha256:
                raise ValueError('Delivery does not bind this review payload')
            artifacts = [a for a in result.artifacts if a.artifact_id == delivery.video_artifact_id]
            if len(artifacts) != 1:
                raise ValueError('Delivery must name exactly one result video artifact')
            artifact = artifacts[0]
            if (artifact.path != delivery.video_artifact_path or artifact.sha256 != delivery.video_artifact_sha256
                    or artifact.size != delivery.video_artifact_size or artifact.run_id != result.run_id):
                raise ValueError('Delivered video differs from result artifact')
            self.accept(registry, delivery.evidence_id, {f: getattr(delivery, f) for f in BINDING_FIELDS},
                        artifact, [artifact.path])
            if (review.subject_type != 'render' or review.subject_id != review.render_id
                    or review.render_id != artifact.artifact_id or review.subject_hash != review.render_hash
                    or review.render_hash != artifact.sha256 or review.edit_plan_version != delivery.edit_plan_version):
                raise ValueError('Review does not describe the delivered render')
            if task['context'].get('edit_plan_version') != delivery.edit_plan_version:
                raise ValueError('Review no longer matches the registered edit plan version')
            for dependency in task['depends_on']:
                dep = registry.db.execute('SELECT status,result FROM ai_tasks WHERE id=?', (dependency,)).fetchone()
                if not dep or dep['status'] != 'completed':
                    raise ValueError('Review dependency is no longer current')
                payload = json.loads(dep['result'])['payload']
                if 'plan_id' in payload and payload['version'] != delivery.edit_plan_version:
                    raise ValueError('Current accepted edit plan differs from the review')
            return delivery, artifact
        except (ValueError, TypeError, KeyError, OSError, StudioError) as exc:
            raise StudioError('VIDEO_CAPABILITY_UNVERIFIED', 'Full render review requires current host delivery, identity and integrity evidence') from exc
