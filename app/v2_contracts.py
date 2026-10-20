"""Run-level contracts; creative Scene/EditPlan/Review remain in editorial.py."""
from __future__ import annotations

from typing import Literal
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .contracts import Identifier, Sha256, relative_path
from .editorial import Review, Coverage  # Exact CLI model/schema, no parallel Review.


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Brief(Contract):
    topic: str = Field(min_length=1, max_length=2000)
    language: str = Field(default='ru', min_length=2, max_length=32)
    target_minutes: int = Field(default=1, ge=1, le=30)
    style: str = Field(default='serious', max_length=500)
    source_profile: Identifier = 'owner_supplied'
    production_mode: Literal['fake', 'production'] = 'fake'
    auto_fix_cycles: int = Field(default=1, ge=0, le=1)
    output_profile: Identifier = 'ruslan-local-narration'


class Task(Contract):
    task_id: Identifier
    run_id: Identifier
    scene_id: Identifier | None = None
    role: Literal['gemini_story', 'gemini_visual', 'codex', 'python']
    capability: Identifier
    input_version: int = Field(ge=1)
    input_hashes: dict[str, Sha256]
    depends_on: list[Identifier]
    assigned_worker: Identifier
    provider_profile_id: Identifier
    requested_model: str | None = None
    expected_output_schema: str
    idempotency_key: Identifier


class Artifact(Contract):
    artifact_id: Identifier
    run_id: Identifier
    version: int = Field(ge=1)
    path: str
    sha256: Sha256
    size: int = Field(ge=0)
    simulated: bool

    @model_validator(mode='after')
    def safe_path(self):
        relative_path(self.path)
        if not self.path.startswith(f'runs/{self.run_id}/'):
            raise ValueError('Artifact path must belong to its run')
        return self


class HostReviewDelivery(Contract):
    """Trusted adapter metadata, never part of a model's creative payload."""
    delivery_id: Identifier
    task_id: Identifier
    run_id: Identifier
    attempt_id: Identifier
    evidence_id: Identifier
    provider: Identifier
    provider_profile_id: Identifier
    account_fingerprint: Sha256 | None = None
    client: Identifier
    client_version: str = Field(min_length=1)
    requested_model: str = Field(min_length=1)
    returned_model: str = Field(min_length=1)
    control_video_hash: Sha256
    video_artifact_id: Identifier
    video_artifact_path: str
    video_artifact_sha256: Sha256
    video_artifact_size: int = Field(ge=0)
    input_version: int = Field(ge=1)
    input_hashes: dict[str, Sha256]
    edit_plan_version: int = Field(ge=1)
    delivered_coverage: list[Coverage] = Field(min_length=1)
    review_payload_sha256: Sha256
    requested_at: str
    completed_at: str
    expires_at: str
    receipt_ref: str
    receipt_sha256: Sha256

    @model_validator(mode='after')
    def safe_receipt(self):
        relative_path(self.receipt_ref)
        relative_path(self.video_artifact_path)
        times = [datetime.fromisoformat(t.replace('Z', '+00:00')) for t in
                 (self.requested_at, self.completed_at, self.expires_at)]
        if any(t.tzinfo is None for t in times):
            raise ValueError('Delivery timestamps require timezones')
        requested, completed, expires = times
        if not (0 <= (completed-requested).total_seconds() <= 3600
                and 0 < (expires-completed).total_seconds() <= 3600):
            raise ValueError('Delivery request and post-completion lifetimes are each bounded to one hour')
        return self


class Result(Contract):
    task_id: Identifier
    run_id: Identifier
    acceptance_key: Identifier
    expected_output_schema: str
    payload: dict
    input_version: int = Field(ge=1)
    input_hashes: dict[str, Sha256]
    artifacts: list[Artifact]
    simulated: bool
    host_review_delivery: HostReviewDelivery | None = None

    @model_validator(mode='after')
    def same_run_and_provenance(self):
        if any(a.run_id != self.run_id or a.simulated != self.simulated for a in self.artifacts):
            raise ValueError('Result artifacts must share the same run and simulation provenance')
        return self


class Issue(Contract):
    issue_id: Identifier
    scene_id: Identifier
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    severity: Literal['critical', 'major', 'minor']
    category: Literal['canon', 'causality', 'text', 'visual', 'pacing', 'mlt', 'file', 'hash']
    problem: str = Field(min_length=1)
    recommended_fix: str = Field(min_length=1)
    evidence_refs: list[str]

    @model_validator(mode='after')
    def ordered(self):
        if self.end <= self.start:
            raise ValueError('Issue interval must be positive')
        return self


def issue_owner(category):
    return {'canon': 'gemini_story', 'causality': 'gemini_story', 'text': 'gemini_story',
            'visual': 'gemini_visual', 'pacing': 'gemini_visual', 'mlt': 'codex',
            'file': 'python', 'hash': 'python'}[category]


class TrustedVideoProbe(Contract):
    """Host-authored capability evidence, never accepted as part of a model Review."""
    evidence_id: Identifier
    provider: Identifier
    provider_profile_id: Identifier
    account_fingerprint: Sha256 | None = None
    client: Identifier
    client_version: str = Field(min_length=1, max_length=160)
    requested_model: str = Field(min_length=1, max_length=160)
    returned_model: str = Field(min_length=1, max_length=160)
    control_video_hash: Sha256
    result_hash: Sha256
    checked_at: str
    expires_at: str
    status: Literal['VERIFIED', 'UNVERIFIED', 'FAILED', 'EXPIRED']
    evidence_refs: list[str] = Field(min_length=1)

    @model_validator(mode='after')
    def timestamp(self):
        checked = datetime.fromisoformat(self.checked_at.replace('Z', '+00:00'))
        expires = datetime.fromisoformat(self.expires_at.replace('Z', '+00:00'))
        if checked.tzinfo is None or expires.tzinfo is None:
            raise ValueError('Probe timestamps require a timezone')
        if not 0 < (expires - checked).total_seconds() <= 86400:
            raise ValueError('Probe lifetime must be positive and at most 24 hours')
        for ref in self.evidence_refs:
            relative_path(ref)
        return self
