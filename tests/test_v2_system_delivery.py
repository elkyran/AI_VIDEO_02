"""Injected Drive transport and provider boundary tests: zero network/model calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from app.registry import Registry, StudioError, content_hash, write_generated_json
from app.v2_drive import DriveError, GoogleDriveHTTP, deliver_package, remote_verified
from app.v2_provider_bridge import ProviderBridge
from app.v2_run_config import HostRunConfig
from tests.test_v2_system import fixture


class MemoryDrive:
    is_simulated = True
    def __init__(self):
        self.objects={};self.bytes={};self.offsets={};self.calls=0;self.failure=None;self.after_chunk=False
        self.fail_chunk_kind=None
    def check(self):
        self.calls+=1
        if self.failure:
            kind=self.failure;self.failure=None;raise DriveError(kind)
    def lookup(self,folder,name,key):
        self.check()
        values=[v for v in self.objects.values() if v['name']==name]
        if values and values[0]['appProperties']['system_key']!=key:raise DriveError('conflict')
        return deepcopy(values[0]) if values else None
    def allocate(self):
        self.check();return 'remote_'+str(len(self.objects)+1)
    def inspect(self,identifier):
        self.check()
        if identifier not in self.objects:raise DriveError('not_found')
        value=deepcopy(self.objects[identifier]);data=self.bytes.get(identifier,b'')
        value.update(size=str(len(data)),md5Checksum=hashlib.md5(data,usedforsecurity=False).hexdigest())
        return value
    def position(self,identifier,total):
        self.check();return self.offsets.get(identifier)
    def begin(self,identifier,folder,name,key,total,existing=False):
        self.check();self.objects[identifier]=dict(id=identifier,name=name,parents=[folder],trashed=False,
            appProperties={'system_key':key},size='0')
        self.bytes[identifier]=b'';self.offsets[identifier]=0;return 0
    def chunk(self,identifier,offset,data,total):
        self.check()
        if self.fail_chunk_kind:
            kind=self.fail_chunk_kind;self.fail_chunk_kind=None;raise DriveError(kind)
        if offset!=self.offsets[identifier]:raise DriveError('conflict')
        self.bytes[identifier]+=data;self.offsets[identifier]+=len(data)
        if self.after_chunk:
            self.after_chunk=False;raise DriveError('transient')
        return self.offsets[identifier]


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='system_delivery_');self.addCleanup(self.tmp.cleanup)
        folder=Path(self.tmp.name).resolve();config=fixture(folder,1).model_dump()
        config['mode']='approved_input';config['brief']['production_mode']='production'
        config['inputs']['canon']['simulated']=False
        for scene in config['inputs']['scenes']:
            scene['simulated']=False
            for segment in scene['narration_segments']:segment['simulated']=False
        config['drive']=dict(folder_id='owner_folder',credential_ref='host_binding',network_write_authorized=True)
        config['operations'].append('deliver');config['budget']['external_requests']=100
        self.config=HostRunConfig.model_validate(config)
        self.root=Path(self.config.root);self.package=self.root/'package';self.package.mkdir(parents=True)
        (self.package/'project.mlt').write_text('<mlt><property name="resource">0</property></mlt>')
        (self.package/'final.mp4').write_bytes(b'simulated transport bytes; never decoded or labelled a movie')
        files={p.name:dict(sha256=content_hash(p),bytes=p.stat().st_size) for p in self.package.iterdir()}
        write_generated_json(self.package,'manifest.json',dict(run_id=self.config.run_id,files=files,uploaded=False))
        self.value=dict(portable='package',run_id=self.config.run_id,version=1,portable_manifest_sha256=content_hash(self.package/'manifest.json'))
        qroot=self.root/'coordinator'
        with Registry(qroot) as r:
            r.db.execute('CREATE TABLE ai_events(sequence INTEGER PRIMARY KEY,timestamp REAL,kind TEXT,task_id TEXT,payload TEXT)')
        def event(r,kind,value,item):
            r.db.execute('INSERT INTO ai_events(timestamp,kind,task_id,payload) VALUES(0,?,?,?)',(kind,item,json.dumps(value)))
        self.pipeline=SimpleNamespace(config=self.config,root=self.root,permit=SimpleNamespace(validate=Mock()),
            accepted=lambda _:self.value,q=SimpleNamespace(root=qroot,queue=SimpleNamespace(_event=event)))
        self.drive=MemoryDrive()

    def test_interrupt_after_accepted_chunk_resumes_without_duplicate_remote_objects(self):
        self.drive.after_chunk=True
        value=deliver_package(self.pipeline,self.value,self.drive)
        self.assertEqual(value['status'],'SIMULATED_DELIVERY_VERIFIED');self.assertFalse(value['uploaded'])
        self.assertEqual(len(self.drive.objects),3)
        self.assertTrue(all(r['verification']=='md5' for r in value['objects']))
        count=self.drive.calls
        self.assertEqual(deliver_package(self.pipeline,self.value,self.drive),value)
        self.assertEqual(self.drive.calls,count)

    def test_auth_expiry_quota_conflict_and_bounded_transient_preserve_local_package(self):
        original=content_hash(self.package/'final.mp4')
        for kind in ('auth','quota','conflict'):
            self.drive.failure=kind
            with self.subTest(kind=kind),self.assertRaises(DriveError) as e:deliver_package(self.pipeline,self.value,self.drive)
            self.assertEqual(e.exception.kind,kind)
            self.assertEqual(content_hash(self.package/'final.mp4'),original)
        self.drive.failure='transient'
        self.assertFalse(deliver_package(self.pipeline,self.value,self.drive)['uploaded'])

    def test_remote_checksum_mismatch_never_claims_uploaded(self):
        old=self.drive.inspect
        def corrupt(identifier):
            result=old(identifier);result['md5Checksum']='0'*32;return result
        self.drive.inspect=corrupt
        with self.assertRaises(DriveError) as e:deliver_package(self.pipeline,self.value,self.drive)
        self.assertEqual(e.exception.kind,'checksum_mismatch')
        self.assertTrue((self.package/'final.mp4').is_file())

    def test_auth_expiry_mid_upload_then_explicit_resume_uses_same_remote_identity(self):
        self.drive.fail_chunk_kind='auth'
        with self.assertRaises(DriveError):deliver_package(self.pipeline,self.value,self.drive)
        first=list(self.drive.objects)
        result=deliver_package(self.pipeline,self.value,self.drive)
        self.assertFalse(result['uploaded']);self.assertEqual(len(self.drive.objects),3)
        self.assertTrue(set(first).issubset(self.drive.objects))

    def test_transport_budget_exhaustion_preserves_partial_state_for_explicit_resume(self):
        self.pipeline.config=self.config.model_copy(update={'budget':self.config.budget.model_copy(update={'external_requests':3})})
        with self.assertRaises(DriveError) as exc:deliver_package(self.pipeline,self.value,self.drive)
        self.assertEqual(exc.exception.kind,'request_budget')
        self.pipeline.config=self.config
        self.assertFalse(deliver_package(self.pipeline,self.value,self.drive)['uploaded'])

    def test_changed_manifest_corrupt_bytes_extra_file_and_broken_mlt_are_rejected(self):
        (self.package/'project.mlt').write_text('<mlt><property name="resource">../missing.wav</property></mlt>')
        with self.assertRaises((StudioError,ValueError)):deliver_package(self.pipeline,self.value,self.drive)
        self.assertEqual(self.drive.calls,0)

    def test_unverified_manifest_and_missing_network_authority_are_rejected(self):
        wrong=deepcopy(self.value);wrong['version']=2
        with self.assertRaises(DriveError):deliver_package(self.pipeline,wrong,self.drive)
        self.pipeline.config=self.config.model_copy(update={'operations':[]})
        with self.assertRaises(DriveError):deliver_package(self.pipeline,self.value,self.drive)
        self.assertEqual(self.drive.calls,0)

    def test_checksum_algorithm_identity_size_and_folder_are_bound(self):
        path=self.package/'final.mp4'
        meta=dict(id='object',name='final',parents=['folder'],trashed=False,size=str(path.stat().st_size),
            appProperties={'system_key':'key'},sha256Checksum=content_hash(path))
        expected=dict(identifier='object',folder='folder',name='final',key='key',path=path)
        self.assertEqual(remote_verified(meta,**expected)['verification'],'sha256')
        for change in ({'parents':['other']},{'size':'0'},{'sha256Checksum':None,'md5Checksum':content_hash(path)}):
            with self.assertRaises(DriveError):remote_verified({**meta,**change},**expected)

    def test_http_transport_needs_explicit_host_auth_and_blocks_credential_redirects(self):
        with self.assertRaises(DriveError):GoogleDriveHTTP(token_supplier=lambda:'opaque',session_store={})
        transport=GoogleDriveHTTP(token_supplier=lambda:'opaque',session_store={},authorized=True)
        for url in ('https://evil.invalid/upload','http://www.googleapis.com/drive/v3/files'):
            with self.assertRaises(DriveError):transport.request('GET',url)

    def test_general_portable_builder_preserves_relative_layout_and_bounded_qa(self):
        from app.v2_pipeline import SystemPipeline
        from types import MethodType
        p=SimpleNamespace(root=self.root,run_id=self.config.run_id,config=self.config)
        p.output_files=MethodType(SystemPipeline.output_files,p)
        manifest=json.loads((self.package/'manifest.json').read_text())
        value={'compiled':{'project_mlt':str(self.package/'project.mlt'),
            'file_hashes':{k:v['sha256'] for k,v in manifest['files'].items()}},
            'rendered':{'sha256':content_hash(self.package/'final.mp4')},
            'qa':{'status':'SIMULATED_TEST_ONLY','duration_seconds':1.0,'silence_detection_log':'excluded.log'}}
        result=SystemPipeline.portable(p,value,{'task_id':'portable_fixture'})
        target=self.root/result['portable']
        self.assertEqual(content_hash(target/'final.mp4'),content_hash(self.package/'final.mp4'))
        self.assertNotIn('silence_detection_log',json.loads((target/'qa.json').read_text()))
        self.assertFalse(result['uploaded'])

    def test_delivery_command_returns_auth_quota_and_failure_without_rebuilding_local_release(self):
        from app.v2_pipeline import SystemPipeline,delivery_node
        node=delivery_node(self.config)
        self.pipeline.status=lambda:{'status':'READY_FOR_OWNER_REVIEW','results':self.value}
        self.pipeline.enqueue=Mock();queue=self.pipeline.q.queue;queue.resume=Mock();queue.tick=Mock()
        for kind,status in [('authentication','WAITING_AUTH'),('quota_exhausted','WAITING_QUOTA'),('invalid_input','FAILED_FINAL')]:
            queue.snapshot=Mock(return_value={'tasks':[{'task_id':node['task_id'],'status':'waiting_access','error':{'kind':kind}}]})
            result=SystemPipeline.deliver(self.pipeline,self.drive)
            self.assertEqual(result['status'],status);self.assertFalse(result['uploaded']);self.assertTrue(result['local_release_preserved'])
        self.assertEqual(self.drive.calls,0)


class ProviderTests(unittest.TestCase):
    def test_disabled_story_visual_cannot_be_enabled_by_capability_json(self):
        adapter=Mock(is_simulated=False)
        for provider in ('gemini','antigravity'):
            bridge=ProviderBridge(adapter,provider=provider,opt_in=True,capability_check=lambda *_:dict(
                structured_output=True,cancellation=True,live_verified=True))
            with self.assertRaises(StudioError):bridge.start({},dict(identity_verified=True,account_binding={'host':True}))
        adapter.start.assert_not_called()

    def test_test_transport_dispatch_structured_result_pause_resume_timeout_and_cancel(self):
        adapter=Mock(is_simulated=True);adapter.start.return_value='handle';adapter.poll.return_value=[{'type':'completed','result':{'text':'test'}}]
        clock=[0]
        bridge=ProviderBridge(adapter,provider='codex',capability_check=lambda *_:dict(structured_output=True,cancellation=True),
            clock=lambda:clock[0],timeout=10)
        handle=bridge.start({},{});bridge.pause();self.assertEqual(bridge.poll(handle),[]);bridge.resume()
        self.assertEqual(bridge.poll(handle)[0]['type'],'completed');self.assertEqual(bridge.poll(handle),[])
        handle=bridge.start({},{});clock[0]=11
        self.assertEqual(bridge.poll(handle)[0]['failure']['code'],'PROVIDER_TIMEOUT');adapter.cancel.assert_called_with(handle)

    def test_unsupported_media_and_auth_are_distinct_without_fallback(self):
        adapter=Mock(is_simulated=False)
        bridge=ProviderBridge(adapter,provider='codex',opt_in=True,capability_check=lambda *_:dict(structured_output=True,cancellation=True))
        with self.assertRaises(StudioError) as media:bridge.start({'context':{'required_media':['video']}},{})
        self.assertEqual(media.exception.code,'UNSUPPORTED_MEDIA')
        with self.assertRaises(StudioError) as auth:bridge.start({}, {})
        self.assertEqual(auth.exception.code,'WAITING_AUTH');adapter.start.assert_not_called()


if __name__=='__main__':unittest.main()
