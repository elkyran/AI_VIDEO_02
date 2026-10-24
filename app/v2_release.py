"""Immutable release inventory and local fixture delivery; no Google client/auth."""
import json
from pathlib import Path
import tempfile

from .registry import Registry, StudioError, workspace_path
from .v2_contracts import Artifact

CATEGORIES=('sources','canon','story','voice','visuals','edit','previews','reviews','final','manifests','backups')


def verify_release(coordinator, artifact):
    artifact=Artifact.model_validate(artifact)
    value=json.loads(coordinator.store.verify(artifact).read_text(encoding='utf-8'))
    if (value['run_id']!=artifact.run_id or value['simulated'] is not artifact.simulated
            or value['uploaded'] is not False or value['remote_sync']!='UNVERIFIED'):
        raise StudioError('ARTIFACT_INVALID','Release identity/provenance changed')
    with Registry(coordinator.root) as registry:
        rows=registry.db.execute("SELECT payload FROM ai_events WHERE kind='offline_release_prepared' AND task_id=?",(artifact.run_id,)).fetchall()
        if not any(json.loads(row[0])['artifact']==artifact.model_dump() for row in rows):
            raise StudioError('RESULT_NOT_ACCEPTED','Release has no host preparation checkpoint')
    for entry in value['files']:
        item=Artifact.model_validate(entry['artifact'])
        if item.run_id!=artifact.run_id or item.simulated is not artifact.simulated:
            raise StudioError('RESULT_RUN_MISMATCH','Release mixes run/provenance')
        coordinator.store.verify(item)
    return value


def prepare_release(bridge, mlt_artifact, *, additional_artifacts=()):
    coordinator=bridge.coordinator
    with coordinator.queue._lock:
        data=bridge.read_mlt(mlt_artifact)
        task_id=data['candidate']['run_id']
        with Registry(coordinator.root) as registry:
            for row in registry.db.execute("SELECT payload FROM ai_events WHERE kind='offline_release_prepared' AND task_id=?",(task_id,)):
                prior=json.loads(row[0])
                if prior['mlt']==mlt_artifact and prior['additional']==list(additional_artifacts):
                    verify_release(coordinator,prior['artifact'])
                    return Artifact.model_validate(prior['artifact']).model_dump()
        version=data['edit_plan']['scene_version'];run_id=mlt_artifact['run_id']
        for category in CATEGORIES:
            workspace_path(coordinator.root,f'runs/{run_id}/{category}',for_write=True).mkdir(parents=True,exist_ok=True)
        project=Path(data['compiled']['project_mlt']);files=[]
        for name in sorted({*data['compiled']['file_hashes'],'bridge_manifest.json'}):
            source=workspace_path(project.parent,name)
            category='voice' if source.suffix=='.wav' else 'visuals' if source.suffix in {'.png','.jpg','.jpeg','.mp4'} else 'edit'
            item=coordinator.store.put_file(run_id,source,category=category,source_root=bridge.engine_root,
                version=version,simulated=data['simulated'])
            files.append({'artifact':item.model_dump(),'provenance':'existing_MLT_bridge_offline',
                          'production_status':'NOT_STARTED','delivery_status':'LOCAL_ONLY'})
        for artifact in (data['candidate'],mlt_artifact,*additional_artifacts):
            item=Artifact.model_validate(artifact)
            if item.run_id!=run_id or item.simulated is not data['simulated']:
                raise StudioError('RESULT_RUN_MISMATCH','Additional artifact belongs to another run/provenance')
            coordinator.store.verify(item)
            files.append({'artifact':item.model_dump(),'provenance':'host_offline_preparation',
                          'production_status':'NOT_STARTED','delivery_status':'LOCAL_ONLY'})
        bridge.read_mlt(mlt_artifact)
        release=coordinator.store.put_json(run_id,{'run_id':run_id,'version':version,'simulated':data['simulated'],
            'files':files,'layout':CATEGORIES,'production_status':'NOT_STARTED','delivery_status':'PREPARED_LOCAL',
            'remote_sync':'UNVERIFIED','uploaded':False,'production_started':False},simulated=data['simulated'])
        with Registry(coordinator.root) as registry,registry.transaction():
            coordinator.queue._event(registry,'offline_release_prepared',{'mlt':mlt_artifact,
                'additional':list(additional_artifacts),'artifact':release.model_dump()},task_id)
        return release.model_dump()


def delivery_plan(coordinator, artifact):
    release=verify_release(coordinator,artifact)
    return {'client':'future_official_Drive_client','status':'PREPARED_NOT_UPLOADED',
            'run_id':release['run_id'],'files':[entry['artifact'] for entry in release['files']],
            'remote_sync':'UNVERIFIED','uploaded':False,'cloud_receipt':None}


def copy_to_fixture(coordinator, artifact, fixture_root):
    """Explicit test harness interface. Never called automatically by the CLI."""
    target=Path(fixture_root).resolve();temporary=Path(tempfile.gettempdir()).resolve()
    if target==temporary or not target.is_relative_to(temporary):
        raise ValueError('Fixture delivery is restricted to a dedicated OS temporary subdirectory')
    release=verify_release(coordinator,artifact)
    receipts=[coordinator.store.copy_to_drive(Artifact.model_validate(entry['artifact']),fixture_root)
              for entry in release['files']]
    return {'status':'COPIED_VERIFIED','scope':'local_fixture_bytes_only','receipts':receipts,
            'remote_sync':'UNVERIFIED','uploaded':False,'cloud_receipt':None}
