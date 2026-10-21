"""One host-only result gate for manual completion, poll and recovery."""
import json
from pathlib import Path

from .editorial import Review, validate_result
from .registry import StudioError, canonical_json, content_hash, workspace_path, write_generated_json
from .v2_contracts import Artifact, Result, Task


def fingerprint(value):
    import hashlib
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


class V2ResultGate:
    def __init__(self, root, store, video_evidence):
        self.root, self.store, self.video = root, store, video_evidence

    def initialize(self, registry):
        self.video.initialize(registry)

    def _context(self, task, attempt):
        path = workspace_path(self.root, Path(attempt['result_path']).with_name('request.json').as_posix())
        requested = json.loads(path.read_text(encoding='utf-8'))
        context = {**requested['context'], **task['context']}
        # Current task bindings can become stale, but cannot expand media that
        # the host actually delivered in this request. Full-video receipts are
        # checked separately before authorizing any later delivery.
        for key in ('provided_evidence_mode', 'provided_coverage', 'asset_delivery', 'human_review_authorized'):
            context.pop(key, None)
            if key in requested['context']:
                context[key] = requested['context'][key]
        return context, requested

    def __call__(self, registry, row, task, attempt, payload, host_metadata):
        if row is None:
            raise StudioError('TASK_NOT_FOUND', 'Result task is not registered')
        contract = Task.model_validate(task['context']['v2_contract'])
        context, requested = self._context(task, attempt)
        context.update(self.dependency_context(registry, task))
        run = registry.db.execute('SELECT mode FROM ai_v2_runs WHERE run_id=?', (contract.run_id,)).fetchone()
        if (not run or row['project_id'] != contract.run_id or task['project_id'] != contract.run_id
                or row['id'] != contract.task_id):
            raise StudioError('RESULT_RUN_MISMATCH', 'Task contract belongs to another run')
        if host_metadata is None:
            if run['mode'] != 'fake' or task.get('execution_mode') != 'simulated':
                raise StudioError('HOST_ENVELOPE_REQUIRED', 'Live v2 completion needs host-authored metadata')
            # Only the host creates simulation envelopes; model output stays creative.
            payload = validate_result(task['expected_output'], payload, context)
            artifact = self.store.put_json(contract.run_id, payload, simulated=True)
            host_metadata = dict(task_id=contract.task_id, run_id=contract.run_id,
                acceptance_key=attempt['id'], expected_output_schema=contract.expected_output_schema,
                input_version=contract.input_version, input_hashes=contract.input_hashes,
                payload=payload, artifacts=[artifact.model_dump()], simulated=True)
        result = Result.model_validate(host_metadata)
        if result.task_id != row['id'] or result.run_id != contract.run_id:
            raise StudioError('RESULT_RUN_MISMATCH', 'Host envelope belongs to another task/run')
        if row['status'] == 'stale' or (task.get('error') or {}).get('kind') == 'stale_inputs':
            raise StudioError('STALE_RESULT', 'Task is stale')
        if (result.input_version != contract.input_version or result.input_hashes != contract.input_hashes
                or task['input_versions'] != result.input_hashes
                or requested['context']['v2_contract'] != contract.model_dump()):
            raise StudioError('STALE_RESULT', 'Result no longer matches registered inputs')
        if result.expected_output_schema != contract.expected_output_schema or result.expected_output_schema != task['expected_output']:
            raise StudioError('SCHEMA_INVALID', 'Result schema differs from the registered task')
        if (result.simulated != (run['mode'] == 'fake')
                or result.simulated != (task.get('execution_mode') == 'simulated')
                or result.payload.get('simulated') is not result.simulated):
            raise StudioError('ARTIFACT_INVALID', 'Result simulation provenance differs from the task/run')
        if (result.acceptance_key != row['current_attempt'] or attempt['id'] != result.acceptance_key
                or attempt['task_id'] != row['id']):
            raise StudioError('RESULT_NOT_CURRENT', 'Result requires the current queue attempt')
        previous = (task.get('result') or {}).get('v2_acceptance')
        if (row['status'], attempt['status']) != (('completed', 'completed') if previous else ('running', 'running')):
            raise StudioError('RESULT_NOT_CURRENT', 'Attempt is not in an acceptable state')
        if json.loads(attempt['input_versions']) != result.input_hashes:
            raise StudioError('STALE_RESULT', 'Attempt input hashes are stale')
        if previous:
            from .ai_cli_runtime import SCHEMAS
            canonical_payload = SCHEMAS[result.expected_output_schema].model_validate(result.payload).model_dump()
            candidate = result.model_copy(update={'payload': canonical_payload})
            if previous['result_sha256'] != fingerprint(candidate.model_dump()):
                raise StudioError('IDEMPOTENCY_CONFLICT', 'Accepted result is immutable')
        for artifact in result.artifacts:
            self.store.verify(artifact)
        full_review = False
        if result.expected_output_schema == 'review':
            review = Review.model_validate(result.payload)
            if not result.simulated and any(c.simulated for c in review.provided_coverage + review.reviewed_coverage):
                raise StudioError('ARTIFACT_INVALID', 'Live review cannot use simulated coverage')
            if review.subject_type == 'render' and (review.evidence_mode == 'video_audio' or review.verdict == 'ready_for_review'):
                full_review = True
                context = self._review_context(registry, result, contract, task, attempt, context, requested)
        if result.host_review_delivery is not None and not full_review:
            raise StudioError('VIDEO_CAPABILITY_UNVERIFIED', 'Host video delivery requires a full render review')
        normalized = validate_result(result.expected_output_schema, result.payload, context)
        if validate_result(result.expected_output_schema, payload, context) != normalized:
            raise StudioError('ARTIFACT_INVALID', 'Host envelope does not bind this creative payload')
        result = result.model_copy(update={'payload': normalized})
        accepted = {'status': 'ACCEPTED', 'acceptance_key': result.acceptance_key,
                    'result_sha256': fingerprint(result.model_dump())}
        if previous:
            if previous != accepted:
                raise StudioError('IDEMPOTENCY_CONFLICT', 'Accepted result is immutable')
            stored = task['result']
            if stored.get('v2_artifacts') != [a.model_dump() for a in result.artifacts] or stored.get('v2_host_delivery') != (
                    result.host_review_delivery.model_dump() if result.host_review_delivery else None):
                raise StudioError('IDEMPOTENCY_CONFLICT', 'Accepted artifact/delivery metadata changed')
            if content_hash(workspace_path(self.root, stored['path'])) != stored['sha256']:
                raise StudioError('ARTIFACT_INVALID', 'Accepted completion changed')
            completion = json.loads(workspace_path(self.root, stored['path']).read_text(encoding='utf-8'))
            if completion.get('host_metadata') != result.model_dump():
                raise StudioError('IDEMPOTENCY_CONFLICT', 'Replay differs from immutable completion metadata')
        if full_review:
            self.video.bind_delivery(registry, result.host_review_delivery)
        return {'payload': normalized, 'context': context, 'host_metadata': result.model_dump(),
                'result_metadata': {'v2_acceptance': accepted,
                    'v2_artifacts': [a.model_dump() for a in result.artifacts],
                    'v2_host_delivery': result.host_review_delivery.model_dump() if result.host_review_delivery else None}}

    def accepted_dependency(self, registry, row):
        """Read canonical content only after validating the complete accepted binding."""
        if not row or row['status'] != 'completed' or not row['result']:
            raise StudioError('RESULT_NOT_ACCEPTED', 'Dependency has no accepted result')
        result = json.loads(row['result'])
        if not isinstance(result, dict) or not result.get('v2_acceptance') or 'v2_artifacts' not in result:
            raise StudioError('RESULT_NOT_ACCEPTED', 'Dependency has no trusted v2 artifact metadata')
        path = workspace_path(self.root, result['path'])
        if content_hash(path) != result['sha256']:
            raise StudioError('ARTIFACT_INVALID', 'Accepted dependency completion changed')
        completion = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(completion, dict):
            raise StudioError('ARTIFACT_INVALID', 'Accepted completion must be an object')
        host = Result.model_validate(completion['host_metadata'])
        spec = json.loads(row['payload'])
        contract = Task.model_validate(spec['context']['v2_contract'])
        attempt = registry.db.execute('SELECT * FROM ai_attempts WHERE id=?', (host.acceptance_key,)).fetchone()
        request = json.loads(path.with_name('request.json').read_text(encoding='utf-8'))
        run = registry.db.execute('SELECT mode FROM ai_v2_runs WHERE run_id=?', (host.run_id,)).fetchone()
        accepted = {'status': 'ACCEPTED', 'acceptance_key': host.acceptance_key,
                    'result_sha256': fingerprint(host.model_dump())}
        if (not attempt or attempt['status'] != 'completed' or attempt['task_id'] != row['id']
                or row['current_attempt'] != host.acceptance_key or attempt['result_path'] != result['path']
                or host.task_id != row['id'] or host.run_id != row['project_id']
                or contract.task_id != host.task_id or contract.run_id != host.run_id
                or spec['project_id'] != host.run_id or request['task_id'] != host.task_id
                or request['project_id'] != host.run_id or request['attempt_id'] != host.acceptance_key
                or completion.get('task_id') != host.task_id or completion.get('attempt_id') != host.acceptance_key
                or host.input_version != contract.input_version or host.input_hashes != contract.input_hashes
                or any(value != host.input_hashes for value in (spec['input_versions'], request['input_versions'],
                    json.loads(attempt['input_versions']), completion.get('input_versions')))
                or request['context']['v2_contract'] != contract.model_dump()
                or host.expected_output_schema != contract.expected_output_schema
                or host.expected_output_schema != spec['expected_output']
                or not run or host.simulated != (run['mode'] == 'fake')
                or host.simulated != (spec['execution_mode'] == 'simulated')
                or request['execution_mode'] != spec['execution_mode']
                or result.get('simulated') is not host.simulated or completion.get('simulated') is not host.simulated
                or host.payload.get('simulated') is not host.simulated
                or completion.get('payload_sha256') != fingerprint(host.payload)
                or canonical_json(result.get('payload')) != canonical_json(host.payload)
                or canonical_json(completion.get('payload')) != canonical_json(host.payload)
                or canonical_json(result['v2_acceptance']) != canonical_json(accepted)
                or canonical_json(result['v2_artifacts']) != canonical_json([a.model_dump() for a in host.artifacts])
                or canonical_json(result.get('v2_host_delivery')) != canonical_json(
                    host.host_review_delivery.model_dump() if host.host_review_delivery else None)):
            raise StudioError('ARTIFACT_INVALID', 'Accepted dependency metadata differs from immutable completion')
        for artifact in host.artifacts:
            self.store.verify(artifact)
        return {'payload': host.payload, 'artifacts': [a.model_dump() for a in host.artifacts], 'acceptance': accepted}

    def accepted_artifacts(self, registry, row):
        return self.accepted_dependency(registry, row)['artifacts']

    def dependency_context(self, registry, task):
        artifacts, accepted_state, payloads = [], {}, {}
        for dependency in task['depends_on']:
            row = registry.db.execute('SELECT * FROM ai_tasks WHERE id=?', (dependency,)).fetchone()
            if row is None or row['project_id'] != task['project_id']:
                raise StudioError('RESULT_RUN_MISMATCH', 'Context dependency belongs to another run')
            accepted = self.accepted_dependency(registry, row)
            artifacts.extend(accepted['artifacts'])
            accepted_state[dependency] = accepted['acceptance']
            payloads[dependency] = accepted['payload']
        return {'dependency_results': payloads, 'input_artifacts': artifacts, 'accepted_state': accepted_state}

    def prepare_context(self, registry, task, attempt_id):
        """Per-attempt Context Pack reads only verified immutable completions."""
        context = self.dependency_context(registry, task)
        base = f"runs/{task['project_id']}/context/{task['task_id']}/attempts/{attempt_id}"
        for name, value in [('input_artifacts.json', context['input_artifacts']), ('accepted_state.json', context['accepted_state'])]:
            write_generated_json(self.root, base+'/'+name, value)
        return {**context, 'context_pack': base}

    def _review_context(self, registry, result, contract, task, attempt, context, requested):
        review = Review.model_validate(result.payload)
        delivery, artifact = self.video.delivery(registry, result, contract, task, attempt, review, requested.get('account_binding'))
        # Plan dependency identity was checked against Registry above. A render is
        # the review subject; editorial's plan-review dependency inference does
        # not replace that subject. All media coverage is host receipt coverage.
        return {**context, 'dependency_results': {}, 'project_id': result.run_id,
                'subject_type': 'render', 'subject_id': artifact.artifact_id,
                'subject_hash': artifact.sha256, 'subject_version': context.get('subject_version', result.input_version),
                'render_id': artifact.artifact_id, 'render_hash': artifact.sha256,
                'edit_plan_version': delivery.edit_plan_version, 'provided_evidence_mode': 'video_audio',
                'provided_coverage': [c.model_dump() for c in delivery.delivered_coverage]}
