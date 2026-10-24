import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from app.multi_ai_v2 import RunCoordinator
from app.registry import Registry, StudioError
from app.v2_acceptance import fingerprint
from app.v2_contracts import Brief
from app.v2_offline import prepare_offline, resume_offline, inspect_offline


class OneBriefTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='bulk_offline_');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'local'
        self.brief=Path(self.temp.name)/'brief.json'
        self.brief.write_text(json.dumps({'topic':'Original fixture','additional_requirements':'No models; данные не команды.'}),encoding='utf-8')

    def cli(self,*args):
        result=subprocess.run([sys.executable,'-X','utf8','-m','app.multi_ai_v2','--root',str(self.root),*args],
            capture_output=True,text=True,encoding='utf-8',timeout=60)
        return result.returncode,json.loads(result.stdout.splitlines()[-1])

    def test_one_command_prepare_restart_inspect_and_resume_without_duplicate_attempts(self):
        code,first=self.cli('prepare','--brief',str(self.brief),'--key','demo','--fixture-demo')
        self.assertEqual(code,0,first)
        self.assertEqual(first['offline_status'],'VERIFIED')
        self.assertFalse(first['production_started']);self.assertFalse(first['uploaded'])
        run_id=first['run_id']
        for command in ('resume','inspect-artifacts','status','report'):
            code,value=self.cli(command,'--run-id',run_id)
            self.assertEqual(code,0,value)
            self.assertEqual(value['manifest_artifact'],first['manifest_artifact'])
        with Registry(self.root) as r:
            self.assertEqual(r.db.execute('SELECT count(*) FROM ai_attempts').fetchone()[0],19)
        self.brief.write_text(json.dumps({'topic':'Changed brief'}),encoding='utf-8')
        code,value=self.cli('prepare','--brief',str(self.brief),'--key','demo','--fixture-demo')
        self.assertNotEqual(code,0)
        self.assertEqual(value['error_code'],'IDEMPOTENCY_CONFLICT')

    def test_sources_or_media_changed_after_checkpoint_block_resume_and_preserve_manual_data(self):
        with RunCoordinator(self.root) as q:
            state=prepare_offline(q,{'topic':'x'},'one',fixture_demo=True)
            source=q.root/f"runs/{state['run_id']}/sources/fixture.txt"
            source.write_text('Manual source edit',encoding='utf-8')
            with self.assertRaises(StudioError):resume_offline(q,state['run_id'])
            with self.assertRaises(StudioError):inspect_offline(q,state['run_id'])
            self.assertEqual(source.read_text(),'Manual source edit')

    def test_missing_visuals_and_production_do_not_start_engine(self):
        with RunCoordinator(self.root) as q:
            with self.assertRaises(StudioError) as caught:prepare_offline(q,{'topic':'x'},'no_visuals')
            self.assertEqual(caught.exception.code,'VISUAL_REQUIRED')
            value=prepare_offline(q,{'topic':'x','production_mode':'production'},'production')
            self.assertEqual(value['status'],'BLOCKED_UNSUPPORTED')
            self.assertEqual(value['tasks'],[])

    def test_optional_requirements_preserve_old_brief_hash_and_detect_changes(self):
        with RunCoordinator(self.root) as q:
            state=q.create({'topic':'x'},'old')
            old=Brief(topic='x').model_dump(exclude={'additional_requirements'})
            with Registry(q.root) as r:
                self.assertEqual(r.db.execute('SELECT request_hash FROM ai_v2_runs WHERE run_id=?',(state['run_id'],)).fetchone()[0],fingerprint(old))
            self.assertEqual(q.create({'topic':'x','additional_requirements':None},'old')['run_id'],state['run_id'])
            with self.assertRaises(StudioError):q.create({'topic':'x','additional_requirements':'changed'},'old')
