"""Acquisition uses the existing queue/gate and original host authority throughout."""
from .editorial import validate_result
from .registry import Registry, StudioError
from .v2_acceptance import fingerprint
from .v2_acquisition import acquisition_scope, extract_sources, resolve_refs, unwrapped, validate_citations
from .v2_acquisition_contracts import SourceExtraction
from .v2_release_plan import acquisition_graph, reconcile_inputs
from .v2_run_config import graph, source_inventory
from .v2_sources import source_manifest


def collect(pipeline, config, *, expansion=False):
    """Rebuild topologically from accepted_dependency, never cached model dictionaries."""
    nodes=acquisition_graph(config)
    chosen=nodes if expansion else nodes[:-1]
    values, bindings = {}, {}
    for node in chosen:
        with Registry(pipeline.q.root) as registry:
            row=registry.db.execute('SELECT * FROM ai_tasks WHERE id=?',(node['task_id'],)).fetchone()
            accepted=pipeline.q.result_gate.accepted_dependency(registry,row)
        payload=accepted['payload']
        value=unwrapped(payload)
        if node['stage']=='extract_sources':
            extracted=SourceExtraction.model_validate(value)
            if (extracted.run_id!=config.run_id or extracted.version!=config.inputs.version
                    or extracted.source_manifest_sha256!=fingerprint(source_manifest(source_inventory(config)))):
                raise StudioError('SOURCE_CHANGED','Accepted extraction differs from host source identity')
            if fingerprint(config.pinned())==pipeline.permit.sha256 and value!=extract_sources(config):
                raise StudioError('SOURCE_CHANGED','Accepted passages differ from current permitted source bytes')
        elif node['schema']!='note':
            context={'project_id':config.run_id,'acquisition_scope':acquisition_scope(config,node['provider_role']),
                'dependency_results':{d:values[d]['payload'] for d in node['dependencies']}}
            if node['scene_id']:
                context.update(scene_id=node['scene_id'],scene_version=node['version'])
            if validate_result(node['schema'],payload,context)!=payload:
                raise StudioError('ARTIFACT_INVALID','Accepted acquisition output is not canonical')
        values[node['task_id']]={'payload':payload,'value':value}
        bindings[node['task_id']]={'acceptance':accepted['acceptance'],
            'artifacts':[a['sha256'] for a in accepted['artifacts']]}
    return nodes,values,bindings


def expansion_receipt(pipeline, config):
    nodes,values,bindings=collect(pipeline,config)
    canon=next(v['value'] for v in values.values() if 'facts' in v['value'])
    extraction=next(v['value'] for v in values.values() if 'passages' in v['value'])
    scenes=[v['value'] for v in values.values() if 'narration_segments' in v['value']]
    return {'simulated':config.mode=='simulation','stage':'production_expansion','files':{},
        'run_id':config.run_id,'version':config.version,'authority':fingerprint(config.pinned()),
        'accepted_set':bindings,'citation_bindings':validate_citations(canon,extraction),
        'scene_references':{s['scene_id']:{'facts':resolve_refs(canon,s['fact_refs']),
            'assumptions':resolve_refs(canon,s['assumption_refs'],assumptions=True)} for s in scenes},
        'production_started':False,'uploaded':False}


def derive_expansion(pipeline, config):
    nodes,values,_=collect(pipeline,config,expansion=True)
    receipt=values[nodes[-1]['task_id']]['value']
    expected=expansion_receipt(pipeline,config)
    if receipt!=expected:
        raise StudioError('ARTIFACT_INVALID','Accepted expansion does not bind the complete accepted acquisition set')
    canon=next(v['value'] for v in values.values() if 'facts' in v['value'])
    outline=next(v['value'] for v in values.values() if 'brief_sha256' in v['value'])
    scenes=[v['value'] for v in values.values() if 'narration_segments' in v['value']]
    mapping_results=[v['value'] for v in values.values() if 'scene_sha256' in v['value']]
    order={s['scene_id']:i for i,s in enumerate(outline['scenes'])}
    scenes.sort(key=lambda s:order[s['scene_id']])
    approved=reconcile_inputs(config,canon=canon,story=outline['summary'],scenes=scenes,
        visuals=[m for result in mapping_results for m in result['mappings']])
    effective=config.model_copy(update={'inputs':approved})
    production=graph(effective,acquired=True,acquisition_parent=nodes[-1]['task_id'])
    record={'run_id':config.run_id,'authority':fingerprint(config.pinned()),'version':config.version,
        'inputs_sha256':fingerprint(approved.model_dump()),'acquisition_receipt_sha256':fingerprint(receipt),
        'nodes':[{'task_id':n['task_id'],'stage':n['stage'],'scene_id':n['scene_id'],'version':n['version']} for n in production]}
    return effective,production,record


def maybe_expand(pipeline):
    config=pipeline.host_config
    acquisition=acquisition_graph(config)
    if pipeline.expanded:
        return False
    with Registry(pipeline.q.root) as registry:
        statuses={r['id']:r['status'] for r in registry.db.execute('SELECT id,status FROM ai_tasks WHERE project_id=?',(config.run_id,))}
    if any(statuses.get(n['task_id'])!='completed' for n in acquisition):
        return False
    effective,production,record=pipeline.permit.install_expansion(pipeline,config)
    # One transaction inserts every production task and its durable expansion
    # marker. A crash mid-batch rolls back to the accepted acquisition checkpoint.
    pipeline.q.queue.enqueue_batch([pipeline.task_spec(n) for n in production],receipt=record,
        operation_key=acquisition[-1]['task_id'])
    pipeline.nodes=acquisition+production
    pipeline.config=effective
    pipeline.expanded=True
    pipeline.initialize_bridge(effective)
    return True
