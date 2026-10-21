"""Capability gate around the existing adapter interface, with injected test transport.

No launcher, account manager, authentication flow or undocumented provider protocol.
Antigravity cannot be enabled by a capability JSON supplied by a model.
"""
import time

from .registry import StudioError


class ProviderBridge:
    is_local = False
    def __init__(self, adapter, *, provider, capability_check, opt_in=False, timeout=60, clock=time.monotonic):
        self.adapter, self.provider, self.capability_check = adapter, provider, capability_check
        self.opt_in, self.timeout, self.clock = opt_in is True, timeout, clock
        self.is_simulated = getattr(adapter, 'is_simulated', False) is True
        self.is_live = not self.is_simulated
        self.paused = False
        self.runs = {}

    def start(self, task, profile, attempt_dir=None):
        if self.paused:
            raise StudioError('PROVIDER_PAUSED', 'Resume explicitly; no account rotation is attempted')
        if (self.provider in {'antigravity', 'gemini'} or
                task.get('context', {}).get('v2_contract', {}).get('role') in {'gemini_story', 'gemini_visual'}):
            raise StudioError('BLOCKED_ELIGIBILITY', 'Story/Visual live access remains AGE-GATED / DISABLED; use only the exact offline bridge')
        if not self.is_simulated and (not self.opt_in or self.provider != 'codex'):
            raise StudioError('BLOCKED_UNSUPPORTED', 'Antigravity is AGE-GATED / DISABLED; no replacement route')
        checked = self.capability_check(task, profile)
        if not checked.get('structured_output') or not checked.get('cancellation'):
            raise StudioError('BLOCKED_UNSUPPORTED', 'Required structured/cancellation capabilities are not verified')
        for kind in task.get('context', {}).get('required_media', []):
            if checked.get(kind) is not True:
                raise StudioError('UNSUPPORTED_MEDIA', 'The selected provider has no verified '+kind+' capability')
        if not self.is_simulated and (checked.get('live_verified') is not True or not profile.get('identity_verified')
                or not profile.get('account_binding')):
            raise StudioError('WAITING_AUTH', 'Existing host account/capability binding is unverified')
        handle = self.adapter.start(task, profile, attempt_dir)
        self.runs[handle] = self.clock()+self.timeout
        return handle

    def poll(self, handle):
        if handle not in self.runs:
            return []
        if self.clock() >= self.runs[handle]:
            self.adapter.cancel(handle); self.runs.pop(handle)
            return [{'type': 'failed', 'failure': {'kind': 'local_turn_limit', 'code': 'PROVIDER_TIMEOUT',
                'message': 'Bounded provider deadline expired; accepted results are not repeated', 'retryable': False}}]
        if self.paused:
            return []
        events = self.adapter.poll(handle)
        if any(e.get('type') in {'completed','failed','interrupted'} for e in events):
            self.runs.pop(handle, None)
        return events

    def cancel(self, handle):
        self.runs.pop(handle, None)
        return self.adapter.cancel(handle)

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False


def provider_readiness():
    """Static offline implementation status. No probing of user accounts/files."""
    return {'target': 'two Antigravity workers with separate account bindings',
        'offline_wiring': 'IMPLEMENTED', 'offline_validation': 'OFFLINE_TESTED',
        'validation_scope': 'synthetic regression suite; no live verification',
        'workers': {worker: {'role': role, 'live_enabled': False,
            'eligibility': 'BLOCKED_ELIGIBILITY', 'access': 'AGE-GATED / DISABLED',
            'account_identity': 'UNVERIFIED', 'authorization_isolation': 'UNVERIFIED',
            'session_isolation': 'OFFLINE_TESTED', 'quota': None, 'quota_evidence': 'UNVERIFIED',
            'requested_model': None, 'returned_model': None, 'live_requests': 'NOT_RUN',
            'media': dict.fromkeys(('text', 'image_frame', 'frame_sequence', 'full_video'), 'UNVERIFIED')}
            for worker, role in [('antigravity_story', 'gemini_story'), ('antigravity_visual', 'gemini_visual')]},
        'accounts_connected': 0, 'model_requests': 0, 'paid_api_requests': 0,
        'production_started': False, 'system_ready_for_autonomous_episode': False}


class SyntheticTransport:
    """Data-only injected fixtures. Cannot wrap a real client behind a fake flag."""
    def __init__(self, fixtures):
        import json
        from .registry import canonical_json
        self._fixtures = json.loads(canonical_json(fixtures))
        self.calls = []

    def exchange(self, binding, context):
        from .v2_provider_protocol import synthetic_stdout
        from .registry import canonical_json
        import json
        fixture = self._fixtures[binding.task.task_id]
        if set(fixture)-{'payload', 'format', 'failure', 'pending'}:
            raise ValueError('Unknown synthetic fixture field')
        self.calls.append({'binding': binding.model_dump(), 'context': json.loads(canonical_json(context))})
        if fixture.get('pending') is True:
            return {'pending': True}
        failure = fixture.get('failure')
        if failure is not None:
            if failure not in {'quota_exhausted', 'authentication', 'eligibility'}:
                raise ValueError('Unknown synthetic failure')
            return {'failure': failure}
        output_format = fixture.get('format', 'json')
        return {'binding': binding.model_dump(), 'format': output_format,
                'stdout': synthetic_stdout(binding, fixture['payload'], output_format), 'stderr': ''}


class DualWorkerBridge:
    """Offline adapter for the EXISTING Coordinator and V2ResultGate.

    The legacy gemini queue profile is a synthetic transport reservation, never
    proof of either worker account. This object accepts ONLY data-only fixtures.
    No live enable flag, launcher, scheduler, Registry or acceptance table exists.
    """
    is_simulated = True
    is_live = False
    is_local = True  # Enables existing host-receipt recovery, not production.

    def __init__(self, config, transport, store, *, limits=None, clock=time.monotonic):
        from .v2_provider_protocol import DualWorkerConfig, ProviderLimits
        if type(transport) is not SyntheticTransport:
            raise StudioError('BLOCKED_ELIGIBILITY', 'Only exact synthetic transport is allowed; live roles remain disabled')
        self.config = DualWorkerConfig.model_validate_json(config.model_dump_json())
        self.limits = ProviderLimits.model_validate_json((limits or ProviderLimits()).model_dump_json())
        self.transport, self.store, self.clock = transport, store, clock
        self.runs = {}

    def _request(self, task):
        from .v2_provider_protocol import SessionBinding
        from .v2_contracts import Task
        from .v2_acceptance import fingerprint
        from .registry import canonical_json
        import json
        contract = Task.model_validate(task['context']['v2_contract'])
        if (task['project_id'] != contract.run_id or task['task_id'] != contract.task_id
                or task['expected_output'] != contract.expected_output_schema
                or task['input_versions'] != contract.input_hashes or task['execution_mode'] != 'simulated'
                or task['role'] != {'gemini_story': 'writer', 'gemini_visual': 'critic'}.get(contract.role)
                or task['assignment']['provider'] != 'gemini'):
            raise StudioError('ROLE_MISMATCH', 'Queue request differs from fixed host role binding')
        if (task.get('max_attempts', 0) > self.limits.attempts
                or task.get('attempt', 0) >= self.limits.attempts):
            raise StudioError('PROVIDER_BUDGET', 'Attempt budget exceeded')
        context = task['context']
        if context.get('required_media') or context.get('provided_evidence_mode', 'metadata_only') != 'metadata_only':
            raise StudioError('UNSUPPORTED_MEDIA', 'No media delivery/capability is verified in this package')
        # Send creative inputs and accepted dependencies only, never host policy,
        # identity, fixture_result, routing rules, file paths or another chat.
        allowed = ('source_text', 'dependency_results', 'revision_dependencies', 'accepted_state',
                   'revision_request', 'project_id', 'scene_id', 'scene_version',
                   'subject_type', 'subject_id', 'subject_version', 'subject_hash')
        creative = json.loads(canonical_json({key: context[key] for key in allowed if key in context}))
        if len(canonical_json(creative).encode('utf-8')) > self.limits.context_bytes:
            raise StudioError('PROVIDER_BUDGET', 'Input context exceeds the host budget')
        binding = SessionBinding(slot=self.config.slot(contract.role), task=contract,
            attempt_id=task['attempt_id'], context_sha256=fingerprint(creative))
        if contract.role == 'gemini_story':
            revision = creative.get('revision_request')
            if revision is None:
                if contract.input_version != 1:
                    raise StudioError('REVISION_LIMIT', 'A later Scene needs one bounded host revision request')
            else:
                from .editorial import Scene, Review
                if (type(revision) is not dict or set(revision) != {'issue_id', 'segment_id', 'instruction',
                        'prior_scene', 'max_revision_cycles'} or type(revision['max_revision_cycles']) is not int
                        or revision['max_revision_cycles'] != 1 or contract.input_version != 2
                        or type(revision['instruction']) is not str or not 1 <= len(revision['instruction']) <= 2000):
                    raise StudioError('REVISION_LIMIT', 'Only one addressed text revision is permitted')
                prior = Scene.model_validate(revision['prior_scene'])
                reviews = [Review.model_validate(value) for value in creative.get('dependency_results', {}).values()
                           if 'review_id' in value]
                if (not prior.simulated or prior.version != 1 or prior.project_id != contract.run_id
                        or prior.scene_id != contract.scene_id or not creative.get('accepted_state')
                        or not any(r.subject_type == 'scene' and r.subject_hash == fingerprint(prior.model_dump())
                            and r.subject_version == prior.version and r.subject_id == prior.scene_id
                            and any(i.issue_id == revision['issue_id'] and i.location == revision['segment_id']
                                    and i.status == 'open' for i in r.issues) for r in reviews)):
                    raise StudioError('REVISION_BINDING', 'Revision requires an accepted, matching Visual issue')
        return binding, creative

    def start(self, task, profile, attempt_dir=None):
        from copy import deepcopy
        binding, creative = self._request(task)
        handle = binding.session_key
        if handle in self.runs:
            raise StudioError('PROVIDER_SESSION_CONFLICT', 'One session per host attempt')
        self.runs[handle] = {'task': deepcopy(task), 'binding': binding, 'creative': creative,
            'started': self.clock(), 'attempt_dir': attempt_dir, 'sent': False, 'cancelled': False}
        return handle

    def poll(self, handle):
        from .v2_provider_protocol import parse_output, SessionBinding
        from .v2_contracts import Result
        from .v2_acceptance import fingerprint
        from .registry import write_generated_json
        state = self.runs.get(handle)
        if state is None:
            return []
        binding, task = state['binding'], state['task']
        keep_pending = False
        try:
            elapsed = self.clock()-state['started']
            if state['cancelled']:
                return [{'type': 'interrupted', 'failure': {'kind': 'interrupted',
                    'code': 'PROVIDER_CANCELLED', 'message': 'Synthetic attempt cancelled', 'retryable': False}}]
            if elapsed >= self.limits.deadline_seconds:
                raise StudioError('PROVIDER_TIMEOUT', 'Host deadline expired; accepted tasks are preserved')
            if binding.slot != self.config.slot(binding.task.role):
                raise StudioError('PROVIDER_SESSION_MISMATCH', 'Host account binding changed during this attempt')
            if state['sent']:
                keep_pending = True
                return []  # Pending fixture: bounded by host clock, never busy-waits.
            state['sent'] = True
            exchange = self.transport.exchange(binding, state['creative'])
            if exchange.get('pending') is True:
                keep_pending = True
                return []
            if exchange.get('failure'):
                cause = exchange['failure']
                # Generic task-scoped access pause deliberately avoids core's
                # provider-wide quota mutation/rotation. Preserve exact cause.
                return [{'type': 'failed', 'failure': {'kind': 'permissions', 'provider_failure_kind': cause,
                    'code': {'quota_exhausted': 'WAITING_QUOTA', 'authentication': 'WAITING_AUTH',
                             'eligibility': 'BLOCKED_ELIGIBILITY'}[cause],
                    'message': 'Synthetic worker task paused; no fallback or rotation', 'retryable': False}}]
            if (set(exchange) != {'binding', 'format', 'stdout', 'stderr'}
                    or SessionBinding.model_validate(exchange['binding']) != binding
                    or binding.session_key != handle or fingerprint(state['creative']) != binding.context_sha256):
                raise StudioError('PROVIDER_SESSION_MISMATCH', 'Reply differs from the current host attempt')
            if exchange['stderr']:
                # Any permission/soft-denial diagnostics fail closed; do not mix
                # into creative text, store credentials or claim a needed tool ran.
                from .ai_adapters import sanitize
                diagnostics = exchange['stderr']
                if type(diagnostics) is not str:
                    raise StudioError('PROVIDER_DIAGNOSTICS', 'Malformed provider diagnostics')
                if state['attempt_dir'] is not None:
                    relative = state['attempt_dir'].relative_to(self.store.root).as_posix()+'/provider_diagnostics.json'
                    write_generated_json(self.store.root, relative, {'stderr': sanitize(diagnostics[:self.limits.output_bytes]),
                        'truncated': len(diagnostics) > self.limits.output_bytes, 'stdout_mixed': False})
                raise StudioError('PROVIDER_DIAGNOSTICS', 'Provider diagnostics rejected; sanitized attempt log only')
            context = {**state['creative'], 'provided_evidence_mode': 'metadata_only'}
            payload = parse_output(exchange['stdout'], binding=binding, output_format=exchange['format'],
                limits=self.limits, elapsed=self.clock()-state['started'], context=context)
            self._revision_output(payload, context)
            artifact = self.store.put_json(binding.task.run_id, payload, simulated=True)
            result = Result(task_id=binding.task.task_id, run_id=binding.task.run_id,
                acceptance_key=binding.attempt_id, expected_output_schema=binding.task.expected_output_schema,
                input_version=binding.task.input_version, input_hashes=binding.task.input_hashes,
                payload=payload, artifacts=[artifact], simulated=True)
            receipt = {'binding': binding.model_dump(), 'result': result.model_dump(),
                       'requested_model': binding.task.requested_model, 'returned_model': None}
            if state['attempt_dir'] is not None:
                relative = state['attempt_dir'].relative_to(self.store.root).as_posix()+'/provider_receipt.json'
                write_generated_json(self.store.root, relative, receipt)
            return [{'type': 'completed', 'result': payload, 'host_metadata': result.model_dump(),
                     'model': None, 'simulated': True}]
        except Exception as exc:
            # Avoid Pydantic errors echoing hostile inputs/credential-like text.
            return [{'type': 'failed', 'failure': {'kind': 'invalid_input',
                'code': getattr(exc, 'code', 'PROVIDER_PROTOCOL_INVALID'),
                'message': 'Offline attempt rejected; no live request performed', 'retryable': False}}]
        finally:
            if not keep_pending:
                self.runs.pop(handle, None)

    def cancel(self, handle):
        if handle in self.runs:
            self.runs[handle]['cancelled'] = True

    def recover(self, attempt_dir):
        try:
            return self._recover_receipt(attempt_dir)
        except (ValueError, KeyError, TypeError, OSError, StudioError):
            # The exact synthetic transport has no surviving child process.
            # A rejected receipt is terminal; core must not reserve it forever.
            return {'status': 'failed', 'failure': {'kind': 'invalid_input',
                'code': 'PROVIDER_RECOVERY_REJECTED', 'message': 'Synthetic receipt binding rejected', 'retryable': False}}

    @staticmethod
    def _revision_output(payload, context):
        from copy import deepcopy
        revision = context.get('revision_request')
        if revision:
            expected = deepcopy(revision['prior_scene'])
            expected['version'] = 2
            target = [part for part in payload['narration_segments'] if part['segment_id'] == revision['segment_id']]
            prior = [part for part in expected['narration_segments'] if part['segment_id'] == revision['segment_id']]
            if len(target) != 1 or len(prior) != 1:
                raise StudioError('REVISION_SCOPE', 'One existing narration segment must be addressed')
            prior[0]['text'] = target[0]['text']
            if payload != expected:
                raise StudioError('REVISION_SCOPE', 'Only the requested text and Scene version may change')

    def _recover_receipt(self, attempt_dir):
        import json
        from .v2_contracts import Result
        from .v2_provider_protocol import SessionBinding
        from .editorial import validate_result
        request = json.loads((attempt_dir/'request.json').read_text(encoding='utf-8'))
        binding, context = self._request(request)
        file = attempt_dir/'provider_receipt.json'
        if not file.exists():
            return {'status': 'interrupted'}  # Never silently re-send a request.
        receipt = json.loads(file.read_text(encoding='utf-8'))
        result = Result.model_validate(receipt['result'])
        if (set(receipt) != {'binding', 'result', 'requested_model', 'returned_model'}
                or SessionBinding.model_validate(receipt['binding']) != binding
                or receipt['requested_model'] != binding.task.requested_model or receipt['returned_model'] is not None
                or result.acceptance_key != binding.attempt_id or result.task_id != binding.task.task_id
                or result.run_id != binding.task.run_id or result.input_hashes != binding.task.input_hashes
                or result.input_version != binding.task.input_version or not result.simulated
                or result.expected_output_schema != binding.task.expected_output_schema):
            raise StudioError('PROVIDER_SESSION_MISMATCH', 'Persisted receipt differs from the host attempt')
        for artifact in result.artifacts:
            self.store.verify(artifact)
        validate_result(result.expected_output_schema, result.payload, {**context, 'provided_evidence_mode': 'metadata_only'})
        self._revision_output(result.payload, context)
        return {'status': 'completed', 'result': result.payload, 'host_metadata': result.model_dump()}


if __name__ == '__main__':
    import json
    print(json.dumps(provider_readiness(), ensure_ascii=False))
