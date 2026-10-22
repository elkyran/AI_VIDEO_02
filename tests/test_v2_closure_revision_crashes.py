"""Faults only at real receipt/acceptance/publication boundaries."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.multi_ai import Coordinator
from app.registry import Registry,StudioError,content_hash
from app.v2_acceptance import fingerprint
from app.v2_pipeline import SystemPipeline,SystemPermit
from app.v2_provider_bridge import SyntheticTransport
from tests.closure_review_support import config,services,call,snapshot,save,InjectedCrash


class CrashRevisionTests(unittest.TestCase):
    def test_response_without_receipt_is_ambiguous_and_never_resent(self):
        with tempfile.TemporaryDirectory(prefix='crash_response_') as temp:
            cfg=config(temp,1);host,_=services(count=1);p=SystemPipeline(cfg,opt_in=True,authorize=True,host_services=host)
            exchange=SyntheticTransport.exchange;seen=[]
            def crash(transport,binding,ctx):
                result=exchange(transport,binding,ctx)
                if binding.task.expected_output_schema=='canon_bible':seen.append(binding.attempt_id);raise InjectedCrash('response not receipted')
                return result
            with patch.object(SyntheticTransport,'exchange',new=crash):
                with self.assertRaises(InjectedCrash):p.run()
            before=snapshot(cfg.root);p.close();count=len(host.worker_transport.calls)
            p=SystemPipeline(cfg,opt_in=True,host_services=host)
            try:
                state=p.run(ticks=2);after=snapshot(cfg.root)
                self.assertEqual(state['status'],'AMBIGUOUS_PROVIDER_HOLD')
                self.assertEqual(len(host.worker_transport.calls),count)
                self.assertEqual(before['attempts'],after['attempts'])
                self.assertEqual(before['tasks'],after['tasks'])
                self.assertFalse(p.expanded)
                with Registry(p.q.root) as db:
                    events=[json.loads(r[0]) for r in db.db.execute("SELECT payload FROM ai_events WHERE kind='recovery_error'")]
                self.assertTrue(any(e['reservation_retained'] and 'may have executed' in e['message'] for e in events))
                save('crash_response_ambiguous',{'before':before,'restart':after,'calls':count,'reservation_retained':True})
            finally:p.close()

    def test_completion_acquisition_and_committed_expansion_crashes_reuse_exact_accepted_ids(self):
        for point in ('completion_before_acceptance','accepted_before_expansion','expansion_before_flags'):
            with self.subTest(point=point),tempfile.TemporaryDirectory(prefix='crash_'+point+'_') as temp:
                cfg=config(temp,1);host,_=services(count=1)
                p=SystemPipeline(cfg,opt_in=True,authorize=True,host_services=host);hit=[]
                if point=='completion_before_acceptance':
                    original=Coordinator._write_completion
                    def injected(queue,registry,attempt,result,**kwargs):
                        value=original(queue,registry,attempt,result,**kwargs)
                        task=json.loads(registry.db.execute('SELECT payload FROM ai_tasks WHERE id=?',(attempt['task_id'],)).fetchone()[0])
                        if task['expected_output']=='canon_bible' and not hit:
                            hit.append(attempt['id']);raise InjectedCrash(point)
                        return value
                    target=patch.object(Coordinator,'_write_completion',new=injected)
                elif point=='accepted_before_expansion':
                    original=SystemPermit.install_expansion
                    def injected(permit,*args,**kwargs):
                        if not hit:hit.append(True);raise InjectedCrash(point)
                        return original(permit,*args,**kwargs)
                    target=patch.object(SystemPermit,'install_expansion',new=injected)
                else:
                    original=Coordinator.enqueue_batch
                    def injected(queue,*args,**kwargs):
                        value=original(queue,*args,**kwargs)
                        if not hit:hit.append(True);raise InjectedCrash(point)
                        return value
                    target=patch.object(Coordinator,'enqueue_batch',new=injected)
                with target:
                    with self.assertRaises(InjectedCrash):p.run()
                before=snapshot(cfg.root);p.close();calls=len(host.worker_transport.calls)
                p=SystemPipeline(cfg,opt_in=True,host_services=host)
                try:
                    self.assertEqual(p.run()['status'],'SIMULATION_SUCCEEDED')
                    after=snapshot(cfg.root);p.close()
                    p=SystemPipeline(cfg,opt_in=True,host_services=host)
                    self.assertEqual(p.run()['status'],'SIMULATION_SUCCEEDED')
                    self.assertEqual(after,snapshot(cfg.root))
                    self.assertTrue(set(r['id'] for r in before['attempts'])<=set(r['id'] for r in after['attempts']))
                    for task,row in before['tasks'].items():
                        if row['status']=='completed':self.assertEqual(row,after['tasks'][task])
                    canon_calls=[c for c in host.worker_transport.calls if c['binding']['task']['expected_output_schema']=='canon_bible']
                    self.assertEqual(len(canon_calls),1)
                    save('crash_'+point,{'before':before,'after':after,'replay':snapshot(cfg.root),'calls_at_fault':calls,
                        'calls_after_restart':len(host.worker_transport.calls),'canonical_canon_turns':1})
                finally:p.close()

    def test_portable_publication_before_and_after_manifest_restarts_without_native_reexecution(self):
        with tempfile.TemporaryDirectory(prefix='crash_publish_') as temp:
            cfg=config(temp,1);host,native=services(native=True,count=1);path=Path(temp)/'host.json'
            import app.v2_pipeline as module
            write=module.write_generated_json;points=[]
            def crash(root,name,value):
                if name=='manifest.json' and Path(root).parent.name=='packages':
                    if not points:points.append('before');raise InjectedCrash('before publication')
                    answer=write(root,name,value)
                    if len(points)==1:points.append('after');raise InjectedCrash('after publication')
                    return answer
                return write(root,name,value)
            with patch.object(module,'write_generated_json',side_effect=crash):
                with self.assertRaises(InjectedCrash):call(self,cfg,path,host,'--execute-system','--authorize','start')
                before=snapshot(cfg.root);commands=len(native.commands);turns=len(host.worker_transport.calls)
                with self.assertRaises(InjectedCrash):call(self,cfg,path,host,'--execute-system','resume')
                published=snapshot(cfg.root)
                value=call(self,cfg,path,host,'--execute-system','resume')
            self.assertEqual(value['status'],'SIMULATION_SUCCEEDED')
            self.assertEqual(commands,len(native.commands));self.assertEqual(turns,len(host.worker_transport.calls))
            final=snapshot(cfg.root)
            self.assertEqual([r['id'] for r in before['attempts']],[r['id'] for r in final['attempts']])
            self.assertEqual([r['id'] for r in published['attempts']],[r['id'] for r in final['attempts']])
            call(self,cfg,path,host,'--execute-system','resume');self.assertEqual(final,snapshot(cfg.root))
            save('crash_publication',{'before':before,'published':published,'final':final,'native_commands_unchanged':commands,
                'manifest_sha256':value['results']['portable_manifest_sha256']})

    def test_authorized_source_edition_revision_restarts_invalidates_dependents_and_preserves_manual_conflict(self):
        with tempfile.TemporaryDirectory(prefix='source_revision_') as temp:
            from tests.test_v2_acquisition import source_config,synthetic_reply
            control=source_config(Path(temp)/'independent',1,run='independent_run')
            control_host,_=services(responder=synthetic_reply,count=1)
            unrelated=SystemPipeline(control,opt_in=True,authorize=True,host_services=control_host)
            self.assertEqual(unrelated.run()['status'],'SIMULATION_SUCCEEDED');unrelated.close()
            control_before=snapshot(control.root)
            cfg=config(temp,1);host,_=services(count=1);p=SystemPipeline(cfg,opt_in=True,authorize=True,host_services=host)
            self.assertEqual(p.run()['status'],'SIMULATION_SUCCEEDED');before=snapshot(cfg.root)
            old_nodes=deepcopy(p.nodes);p.close();previous=fingerprint(cfg.pinned())
            source=Path(cfg.input_root)/cfg.inputs.sources[0].path;source.write_text('Новая разрешённая редакция источника.',encoding='utf-8')
            calls=len(host.worker_transport.calls)
            with self.assertRaises(StudioError):SystemPipeline(cfg,opt_in=True,host_services=host)
            self.assertEqual(calls,len(host.worker_transport.calls))
            cfg.version=2;cfg.parent_sha256=previous;cfg.inputs.version=2
            cfg.inputs.sources[0].edition='edition_b';cfg.inputs.sources[0].sha256=content_hash(source)
            cfg.inputs.sources[0].size=source.stat().st_size
            wrong=cfg.model_copy(deep=True);wrong.inputs.version=1
            with self.assertRaises(StudioError) as stale:SystemPipeline(wrong,opt_in=True,authorize=True,host_services=host)
            self.assertEqual(stale.exception.code,'STALE_INPUTS')
            p=SystemPipeline(cfg,opt_in=True,authorize=True,host_services=host)
            original=Coordinator.enqueue_batch;hit=[]
            def crash(queue,*args,**kwargs):
                value=original(queue,*args,**kwargs)
                if not hit:hit.append(snapshot(cfg.root));raise InjectedCrash('source revision expansion')
                return value
            with patch.object(Coordinator,'enqueue_batch',new=crash):
                with self.assertRaises(InjectedCrash):p.run()
            p.close();p=SystemPipeline(cfg,opt_in=True,host_services=host)
            try:
                self.assertEqual(p.run()['status'],'SIMULATION_SUCCEEDED')
                new_ids={n['task_id'] for n in p.nodes}
                self.assertFalse(new_ids&{n['task_id'] for n in old_nodes})
                after=snapshot(cfg.root)
                self.assertEqual(control_before,snapshot(control.root))
                self.assertTrue(set(hit[0]['tasks'])<=set(after['tasks']))
                self.assertTrue({r['id'] for r in hit[0]['attempts']}<={r['id'] for r in after['attempts']})
                self.assertEqual(len([e for e in after['events'] if e['kind']=='system_expansion']),2)
                p.close();p=SystemPipeline(cfg,opt_in=True,host_services=host)
                self.assertEqual(p.run()['status'],'SIMULATION_SUCCEEDED')
                self.assertEqual(after,snapshot(cfg.root))
                for task,row in before['tasks'].items():self.assertEqual(row,after['tasks'][task])
                # Old response cannot be inserted as a new-version acceptance.
                node=next(n for n in p.nodes if n['stage']=='canon')
                with Registry(p.q.root) as db:
                    row=db.db.execute('SELECT * FROM ai_tasks WHERE id=?',(node['task_id'],)).fetchone()
                    result=json.loads(row['result']);artifact=result['v2_artifacts'][0]
                    completion=json.loads((p.q.root/result['path']).read_text(encoding='utf-8'))
                    task=p.q.queue._task(row);attempt=db.db.execute('SELECT * FROM ai_attempts WHERE id=?',(row['current_attempt'],)).fetchone()
                    for field,value in [('run_id','independent_run'),('input_version',1)]:
                        stale=deepcopy(completion['host_metadata']);stale[field]=value
                        expected=ValueError if field=='run_id' else StudioError
                        with self.subTest(stale=field),self.assertRaises(expected):
                            p.q.result_gate(db,row,task,attempt,stale['payload'],stale)
                file=p.q.store.verify(__import__('app.v2_contracts',fromlist=['Artifact']).Artifact.model_validate(artifact))
                manual=file.read_bytes()+b'\nmanual change';file.write_bytes(manual)
                with self.assertRaisesRegex(ValueError,'hash/size mismatch'):p.accepted(node['task_id'])
                self.assertEqual(file.read_bytes(),manual)
                save('source_revision',{'before':before,'fault':hit[0],'after':after,
                    'source_sha256':cfg.inputs.sources[0].sha256,'source_edition':'edition_b','manual_conflict_preserved':True})
            finally:p.close()


class ProfileFixtureTests(unittest.TestCase):
    def test_real_profile_validator_checks_generated_proof_and_preserves_corruption(self):
        from tests.closure_profile_fixture import profile_fixture
        from app.production_profile import load_profile
        from app.v2_narration import profile_spec
        with tempfile.TemporaryDirectory(prefix='profile_proof_') as temp:
            root=profile_fixture(temp);profile=load_profile(root)
            for field in ('voice','video','pronunciation_lexicon','pronunciation_terms'):
                self.assertEqual(profile[field],profile_spec()[field])
            path=root/profile['provenance']['base_manifest_ref'];changed=path.read_bytes()+b'\nmanual';path.write_bytes(changed)
            with self.assertRaises(ValueError):load_profile(root)
            self.assertEqual(path.read_bytes(),changed)

    def test_windows_temp_short_alias_is_resolved_like_real_context_without_user_name(self):
        import os
        with tempfile.TemporaryDirectory(prefix='alias fixture long ') as temp:
            path=Path(temp).resolve()
            if os.name=='nt':
                import ctypes
                kernel=ctypes.WinDLL('kernel32',use_last_error=True)
                buffer=ctypes.create_unicode_buffer(32768)
                count=kernel.GetShortPathNameW(str(path),buffer,len(buffer))
                self.assertGreater(count,0)
                self.assertEqual(Path(buffer.value).resolve(),path)
            self.assertEqual(Path(temp).resolve(),path)
