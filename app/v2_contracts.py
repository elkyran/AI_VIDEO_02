"""Run-level contracts; creative Scene/EditPlan/Review remain in editorial.py."""
from __future__ import annotations

from typing import Literal
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .contracts import Identifier, Sha256, relative_path


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


class Result(Contract):
    task_id: Identifier
    run_id: Identifier
    input_version: int = Field(ge=1)
    input_hashes: dict[str, Sha256]
    artifacts: list[Artifact]
    simulated: bool

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


class Review(Contract):
    review_mode: Literal['full_video', 'frames_and_timeline', 'partial']
    verdict: Literal['pass', 'fix_required', 'blocked']
    materials_actually_reviewed: list[str]
    issues: list[Issue]

    @model_validator(mode='after')
    def evidence(self):
        if self.verdict == 'pass' and (self.issues or not self.materials_actually_reviewed):
            raise ValueError('Pass requires reviewed materials and no unresolved issues')
        return self


class TrustedVideoProbe(Contract):
    """Host-authored capability evidence, never accepted as part of a model Review."""
    provider: Identifier
    client_version: str = Field(min_length=1, max_length=160)
    model: str = Field(min_length=1, max_length=160)
    control_video_hash: Sha256
    result_hash: Sha256
    checked_at: str
    status: Literal['VERIFIED', 'UNVERIFIED', 'BLOCKED']

    @model_validator(mode='after')
    def timestamp(self):
        if datetime.fromisoformat(self.checked_at.replace('Z', '+00:00')).tzinfo is None:
            raise ValueError('Probe checked_at requires a timezone')
        return self
