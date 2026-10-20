"""Local process lifetime tests; tiny Python children, no renderer/models/network."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from app.multi_ai_preview import PreviewAdapter, enqueue_preview, process_identity
from app.multi_ai import Coordinator


class PreviewProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.folder=self.root/'ai'/'attempts'/'attempt_test'
        self.folder.mkdir(parents=True)
        self.adapter=PreviewAdapter(self.root)

    def tearDown(self):
        for handle, run in self.adapter.runs.items():
            process=run['process']
            if process and process.poll() is None:
                self.adapter.cancel(handle)
                process.wait(timeout=5)
            if run['log'] and not run['log'].closed:
                run['log'].close()
        self.temp.cleanup()

    def wait_for(self,predicate,timeout=5):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            value=predicate()
            if value:return value
            time.sleep(.03)
        self.fail('Child process did not reach the expected state')

    def test_start_is_processless_until_durable_dispatch_poll(self):
        with patch('app.multi_ai_preview.subprocess.Popen') as spawn:
            handle=self.adapter.start({'kind':'render_fixture'},{},self.folder)
            spawn.assert_not_called()
            self.adapter.cancel(handle)
            events=self.adapter.poll(handle)
            spawn.assert_not_called()
        self.assertEqual(events[0]['failure']['kind'],'interrupted')

    def test_completed_child_recovers_result_without_relaunch(self):
        result={'schema_version':1,'simulated':True,'text':'technical test'}
        program='from pathlib import Path; import sys; Path(sys.argv[1]).write_text(sys.argv[2],encoding="utf-8")'
        self.adapter._command=lambda folder:[sys.executable,'-c',program,str(folder/'native_result.json'),json.dumps(result)]
        handle=self.adapter.start({'kind':'render_fixture'},{},self.folder)
        self.adapter.poll(handle)
        process=self.adapter.runs[handle]['process']
        process.wait(timeout=5)
        recovered=PreviewAdapter(self.root).recover(self.folder)
        self.assertEqual(recovered,{'status':'completed','result':result})
        events=self.adapter.poll(handle)
        self.assertEqual(events[0]['result'],result)

    @unittest.skipUnless(os.name=='nt' or Path('/proc').exists(),'Process identity is only verified on Windows/Linux')
    def test_stop_terminates_only_owned_python_process_tree(self):
        program=('import subprocess,sys,time; from pathlib import Path; '
                 'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); '
                 'Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(30)')
        self.adapter._command=lambda folder:[sys.executable,'-c',program,str(folder/'child.pid')]
        handle=self.adapter.start({'kind':'render_fixture'},{},self.folder)
        self.adapter.poll(handle)
        self.wait_for(lambda:(self.folder/'child.pid').is_file())
        child=int((self.folder/'child.pid').read_text())
        self.assertIsInstance(process_identity(child),str)
        self.assertEqual(PreviewAdapter(self.root).recover(self.folder)['status'],'running')
        self.adapter.cancel(handle)
        process=self.adapter.runs[handle]['process']
        process.wait(timeout=5)
        self.wait_for(lambda:process_identity(child) is False)
        self.assertEqual(self.adapter.poll(handle)[0]['failure']['kind'],'interrupted')

    def test_unknown_launch_gap_keeps_reservation(self):
        self.assertEqual(self.adapter.recover(self.folder),{'status':'unknown'})

    def test_preview_reserves_actual_accepted_director_plan(self):
        with Coordinator(self.root) as coordinator:
            coordinator.demo();coordinator.resume()
            for _ in range(8):coordinator.tick()
            plan=next(t for t in coordinator.snapshot()['tasks'] if t['expected_output']=='edit_plan')
            task=enqueue_preview(coordinator)
            self.assertEqual(task['depends_on'],[plan['task_id']])
            self.assertEqual(task['input_versions'],plan['input_versions'])
            self.assertEqual(task['resource_class'],'local_heavy')
            self.assertEqual(task['context']['plan_task_id'],plan['task_id'])


if __name__=='__main__':
    unittest.main()
