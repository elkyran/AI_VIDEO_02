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

    def _context(self, task, attempt):
        path = workspace_path(self.root, Path(attempt['result_path']).with_name('request.json').as_posix())
        requested = json.loads(path.read_text(encoding='utf-8'))
        return {**requested['context'], **task['context']}, requested

    def __call__(self, registry, row, task, attempt, payload, host_metadata):
        if row is None:
            raise StudioError('TASK_NOT_FOUND', 'Result task is not registered')
        contract = Task.model_validate(task['context']['v2_contract'])
        context, requested = self._context(task, attempt)
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
        for artifact in result.artifacts:
            self.store.verify(artifact)
        if result.expected_output_schema == 'review':
            review = Review.model_validate(result.payload)
            if review.subject_type == 'render' and (review.evidence_mode == 'video_audio' or review.verdict == 'ready_for_review'):
                context = self._review_context(registry, result, contract, task, attempt, context)
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
        return {'payload': normalized, 'context': context, 'host_metadata': result.model_dump(),
                'result_metadata': {'v2_acceptance': accepted,
                    'v2_artifacts': [a.model_dump() for a in result.artifacts],
                    'v2_host_delivery': result.host_review_delivery.model_dump() if result.host_review_delivery else None}}

    def accepted_artifacts(self, row):
        if not row or row['status'] != 'completed' or not row['result']:
            raise StudioError('RESULT_NOT_ACCEPTED', 'Dependency has no accepted result')
        result = json.loads(row['result'])
        if not result.get('v2_acceptance') or 'v2_artifacts' not in result:
            raise StudioError('RESULT_NOT_ACCEPTED', 'Dependency has no trusted v2 artifact metadata')
        artifacts = [Artifact.model_validate(a) for a in result['v2_artifacts']]
        for artifact in artifacts:
            if artifact.run_id != row['project_id'] or artifact.simulated is not result['simulated']:
                raise StudioError('ARTIFACT_INVALID', 'Accepted dependency provenance changed')
            self.store.verify(artifact)
        return [a.model_dump() for a in artifacts]

    def prepare_context(self, registry, task, attempt_id):
        """Per-attempt Context Pack reads only accepted immutable file metadata."""
        artifacts, accepted_state = [], {}
        for dependency in task['depends_on']:
            row = registry.db.execute('SELECT * FROM ai_tasks WHERE id=?', (dependency,)).fetchone()
            if row is None or row['project_id'] != task['project_id']:
                raise StudioError('RESULT_RUN_MISMATCH', 'Context dependency belongs to another run')
            artifacts.extend(self.accepted_artifacts(row))
            accepted_state[dependency] = json.loads(row['result'])['v2_acceptance']
        base = f"runs/{task['project_id']}/context/{task['task_id']}/attempts/{attempt_id}"
        for name, value in [('input_artifacts.json', artifacts), ('accepted_state.json', accepted_state)]:
            write_generated_json(self.root, base+'/'+name, value)
        return {'input_artifacts': artifacts, 'accepted_state': accepted_state, 'context_pack': base}

    def _review_context(self, registry, result, contract, task, attempt, context):
        review = Review.model_validate(result.payload)
        delivery, artifact = self.video.delivery(registry, result, contract, task, attempt, review)
        # Plan dependency identity was checked against Registry above. A render is
        # the review subject; editorial's plan-review dependency inference does
        # not replace that subject. All media coverage is host receipt coverage.
        return {**context, 'dependency_results': {}, 'project_id': result.run_id,
                'subject_type': 'render', 'subject_id': artifact.artifact_id,
                'subject_hash': artifact.sha256, 'subject_version': context.get('subject_version', result.input_version),
                'render_id': artifact.artifact_id, 'render_hash': artifact.sha256,
                'edit_plan_version': delivery.edit_plan_version, 'provided_evidence_mode': 'video_audio',
                'provided_coverage': [c.model_dump() for c in delivery.delivered_coverage]}
