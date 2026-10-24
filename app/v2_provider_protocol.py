"""Pure, bounded parsing of a conservative headless JSON/NDJSON subset.

No process, network, credentials or provider activation. Host bindings are NOT
fields a model can supply. All tools/media remain unverified in this package.
"""
from __future__ import annotations

import json
import math
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Identifier, Sha256
from .editorial import Scene, Review, validate_result
from .registry import StudioError, canonical_json
from .v2_acceptance import fingerprint
from .v2_contracts import Contract, Task


class WorkerSlot(Contract):
    worker_id: Literal['antigravity_story', 'antigravity_visual']
    role: Literal['gemini_story', 'gemini_visual']
    provider: Literal['antigravity'] = 'antigravity'
    profile_id: Identifier
    identity_binding_ref: Identifier
    identity_hash: Sha256
    auth_generation: int = Field(ge=1)
    auth_namespace: Identifier
    session_namespace: Identifier
    synthetic: Literal[True] = True
    quota_remaining: None = None

    @model_validator(mode='after')
    def fixed_role(self):
        if self.worker_id != {'gemini_story': 'antigravity_story',
                              'gemini_visual': 'antigravity_visual'}[self.role]:
            raise ValueError('Worker ID has one fixed role')
        return self


class DualWorkerConfig(Contract):
    story: WorkerSlot
    visual: WorkerSlot

    @model_validator(mode='after')
    def isolated(self):
        if (self.story.role, self.visual.role) != ('gemini_story', 'gemini_visual'):
            raise ValueError('Both fixed roles are required')
        for field in ('identity_hash', 'identity_binding_ref', 'auth_namespace',
                      'session_namespace', 'profile_id'):
            if getattr(self.story, field) == getattr(self.visual, field):
                raise ValueError('Synthetic workers must have distinct '+field)
        return self

    def slot(self, role):
        if role not in ('gemini_story', 'gemini_visual'):
            raise StudioError('ROLE_MISMATCH', 'This bridge only handles Story and Visual')
        return self.story if role == 'gemini_story' else self.visual


class ProviderLimits(Contract):
    deadline_seconds: int = Field(default=30, ge=1, le=120)
    output_bytes: int = Field(default=65536, ge=256, le=1048576)
    context_bytes: int = Field(default=65536, ge=256, le=1048576)
    attempts: int = Field(default=2, ge=1, le=2)
    model_requests: Literal[0] = 0
    paid_api_requests: Literal[0] = 0


class SessionBinding(Contract):
    slot: WorkerSlot
    task: Task
    attempt_id: Identifier
    context_sha256: Sha256

    @model_validator(mode='after')
    def assigned(self):
        if (self.task.role, self.task.assigned_worker, self.task.provider_profile_id) != (
                self.slot.role, self.slot.worker_id, self.slot.profile_id):
            raise ValueError('Host task must pin the exact worker and synthetic profile')
        allowed = {'gemini_story': {'canon_bible','story_outline','scene'},
                   'gemini_visual': {'segment_visuals','review'}}
        if self.task.expected_output_schema not in allowed[self.slot.role]:
            raise ValueError('Creative schema belongs to the other role')
        return self

    @property
    def session_key(self):
        return fingerprint(self.model_dump())

    @property
    def conversation_id(self):
        return 'fixture_'+self.session_key


class Usage(Contract):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    thinking_tokens: int = Field(ge=0)
    cache_read_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class Terminal(Contract):
    conversation_id: str = Field(min_length=1, max_length=128)
    status: str
    response: str
    duration_seconds: float = Field(ge=0, allow_inf_nan=False)
    num_turns: int = Field(ge=1, le=1)
    structured_output: dict
    json_schema: dict
    usage: Usage
    error: str | None = None


def creative_schema(kind):
    from .v2_acquisition_contracts import ACQUISITION_OUTPUTS
    return {'scene': Scene, 'review': Review, **ACQUISITION_OUTPUTS}[kind].model_json_schema()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON field')
        result[key] = value
    return result


def load_json(text):
    return json.loads(text, object_pairs_hook=_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))


def parse_output(stdout, *, binding, output_format, limits, elapsed, context):
    """Reject unknown protocol fields/tools. Never infer delivery from response text.

    One host attempt, one conversation, one terminal. Continuation across turns
    and tool execution intentionally require a separately reviewed integration.
    """
    if (type(stdout) is not bytes or len(stdout) > limits.output_bytes
            or not math.isfinite(elapsed) or elapsed < 0 or elapsed >= limits.deadline_seconds):
        raise StudioError('PROVIDER_BUDGET', 'Output size or host deadline exceeded')
    if fingerprint({k: v for k, v in context.items() if k != 'provided_evidence_mode'}) != binding.context_sha256:
        raise StudioError('PROVIDER_SESSION_MISMATCH', 'Context differs from its host binding')
    try:
        text = stdout.decode('utf-8')
        if output_format == 'json':
            raw = load_json(text)
        elif output_format == 'stream-json':
            rows = [load_json(line) for line in text.splitlines() if line.strip()]
            if len(rows) < 2 or any(type(row) is not dict for row in rows):
                raise ValueError('Missing terminal stream result')
            init = rows[0]
            if (set(init) != {'event', 'conversation_id', 'init'} or init['event'] != 'init'
                    or init['conversation_id'] != binding.conversation_id):
                raise ValueError('Wrong stream conversation')
            config = init['init']
            if (type(config) is not dict or set(config)-{'cwd', 'tools', 'permission_mode', 'model', 'agent', 'json_schema'}
                    or config.get('tools') != [] or config.get('permission_mode') != 'request-review'):
                raise ValueError('Tools/permission mode are not verified')
            for row in rows[1:-1]:
                if set(row) != {'event', 'step_update'} or row['event'] != 'step_update':
                    raise ValueError('Unexpected or multiple terminal events')
                step = row['step_update']
                if (type(step) is not dict or set(step)-{'conversation_id', 'step_index', 'state',
                        'step_type', 'text_delta', 'duration_seconds', 'usage'}
                        or step.get('conversation_id') != binding.conversation_id
                        or step.get('step_type') not in {'user_input', 'agent_response', 'checkpoint'}
                        or step.get('state') not in {'ACTIVE', 'DONE'}
                        or type(step.get('step_index')) is not int or step['step_index'] < 0):
                    raise ValueError('Mixed session or unverified tool/step')
            last = rows[-1]
            if set(last) != {'event', 'result'} or last['event'] != 'result':
                raise ValueError('Missing terminal stream result')
            raw = last['result']
        else:
            raise ValueError('Unsupported output format')
        terminal = Terminal.model_validate(raw)
        if (terminal.status != 'SUCCESS' or terminal.error is not None
                or terminal.conversation_id != binding.conversation_id
                or terminal.duration_seconds >= limits.deadline_seconds):
            raise ValueError('Unsuccessful, stale or expired result')
        expected = creative_schema(binding.task.expected_output_schema)
        if terminal.json_schema != expected or load_json(terminal.response) != terminal.structured_output:
            raise ValueError('Structured output/schema differs from response')
        payload = validate_result(binding.task.expected_output_schema, terminal.structured_output, context)
        if payload.get('simulated') is not True:
            raise ValueError('Synthetic transport cannot become live')
        return payload
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
        # Never include model text, validation input or stderr in exception logs.
        raise StudioError('PROVIDER_PROTOCOL_INVALID', 'Untrusted provider output rejected') from None


def synthetic_stdout(binding, payload, output_format='json'):
    """Test data encoder, not a provider client or an authentication mock."""
    raw = dict(conversation_id=binding.conversation_id, status='SUCCESS',
        response=canonical_json(payload), structured_output=payload,
        json_schema=creative_schema(binding.task.expected_output_schema), duration_seconds=0.0,
        num_turns=1, usage=dict.fromkeys(Usage.model_fields, 0))
    if output_format == 'json':
        return canonical_json(raw).encode('utf-8')
    return ('\n'.join(canonical_json(row) for row in [
        {'event': 'init', 'conversation_id': binding.conversation_id,
         'init': {'cwd': 'synthetic', 'tools': [], 'permission_mode': 'request-review'}},
        {'event': 'result', 'result': raw}])+'\n').encode('utf-8')
