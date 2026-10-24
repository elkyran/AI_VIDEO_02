"""Official-protocol local fixtures only: no login, credentials or model usage."""
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from app.ai_accounts import isolated_environment
from app.ai_codex_diagnostics import _DiagnosticError, _stop, diagnose_codex
from app.multi_ai_preview import process_identity


STUB = r'''import json,os,subprocess,sys,time
mode, trace_path=sys.argv[1:3]
def send(value):
    print(json.dumps(value),flush=True)
methods=[]
for line in sys.stdin:
    req=json.loads(line); methods.append(req['method'])
    with open(trace_path,'w') as out:json.dump({'pid':os.getpid(),'methods':methods},out)
    if req['method']=='initialize':
        if mode=='timeout':time.sleep(60)
        if mode=='malformed':print('not json private@example.invalid sk-private-value',flush=True);continue
        if mode=='flood':
            for n in range(200):send({'method':'notice','params':{'email':'private@example.invalid'}})
            continue
        if mode=='orphan':
            child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
            with open(trace_path,'w') as out:json.dump({'pid':os.getpid(),'child_pid':child.pid,'methods':methods},out)
            raise SystemExit(1)
        send({'id':req['id'],'result':{'userAgent':'stub'}})
    elif req['method']=='account/read':
        assert methods[:2]==['initialize','initialized']
        assert req['params']=={'refreshToken':False}
        if mode=='error':send({'id':req['id'],'error':{'message':'secret@example.invalid sk-private-value'}});continue
        account={'type':'chatgpt','email':' Person@Example.Invalid ','planType':'prolite','token':'never-store-this'}
        if mode=='api':account={'type':'apiKey','key':'sk-private-value'}
        if mode=='noidentity':account['email']=None
        if mode=='loggedout':account=None
        send({'id':req['id'],'result':{'account':account,'requiresOpenaiAuth':True}})
    elif req['method']=='account/rateLimits/read':
        if mode=='quota_timeout':time.sleep(60)
        if mode=='quota_missing':send({'id':req['id'],'result':{}});continue
        if mode=='quota_error':send({'id':req['id'],'error':{'message':'secret@example.invalid sk-private-value'}});continue
        credits={'hasCredits':False,'unlimited':False,'balance':'0'}
        if mode=='paid':credits={'hasCredits':True,'unlimited':False,'balance':'19.75'}
        bucket={'limitId':'codex','planType':'prolite','primary':{'usedPercent':32,'windowDurationMins':10080,'resetsAt':1792874721},'secondary':None,'credits':credits,'access_token':'never-store-this'}
        send({'id':req['id'],'result':{'ordinaryUsageAllowed':True,'accountId':'never-store-account-id','rateLimits':bucket,'rateLimitsByLimitId':{'codex':bucket}}})
        print('stderr secret@example.invalid sk-private-value',file=sys.stderr,flush=True)
'''


class CodexDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.script = self.root / "stub.py"
        self.script.write_text(STUB, encoding="utf-8")
        self.env = isolated_environment("codex", self.root / "profile")

    def run_mode(self, mode, timeout=2):
        trace = self.root / (mode + ".json")
        before = {t.name for t in threading.enumerate()}
        start = time.monotonic()
        with mock.patch("app.ai_codex_diagnostics._stop", wraps=_stop) as cleanup:
            result = diagnose_codex([sys.executable, str(self.script), mode, str(trace)], cwd=self.root, env=self.env, timeout=timeout)
        self.assertLess(time.monotonic()-start, timeout + 8)
        self.assertFalse([t.name for t in threading.enumerate() if t.name not in before and t.name.startswith("codex-diagnostic-")])
        cleanup.assert_called_once()
        process = cleanup.call_args.args[0]
        self.assertIsNotNone(process.returncode)
        self.assertTrue(all(pipe.closed for pipe in (process.stdin, process.stdout, process.stderr)))
        if os.name == "nt":
            self.assertTrue(process._handle.closed)
        saved = json.loads(trace.read_text()) if trace.exists() else {}
        if saved.get("pid"):
            self.assertIs(process_identity(saved["pid"]), False)
        encoded = json.dumps(result)
        for secret in ("Person@Example", "private@example", "secret@example", "sk-private", "never-store-this", "never-store-account-id"):
            self.assertNotIn(secret, encoded)
        return result, saved

    def test_protocol_and_identity_hash_only(self):
        result, saved = self.run_mode("ok")
        self.assertEqual(saved["methods"], ["initialize", "initialized", "account/read", "account/rateLimits/read"])
        self.assertTrue(result["authenticated"])
        self.assertTrue(result["identity_verified"])
        self.assertEqual(result["identity_hash"], hashlib.sha256(b"codex:person@example.invalid").hexdigest())
        self.assertEqual(result["plan_type"], "prolite")
        self.assertEqual(result["model_requests"], 0)
        self.assertIsNone(result["error"])
        self.assertEqual(result["rate_limits"]["primary"], {"usedPercent":32,"windowDurationMins":10080,"resetsAt":1792874721})
        self.assertEqual(result["credits"], {"hasCredits":False,"unlimited":False,"balance":"0"})
        self.assertTrue(result["ordinary_usage_allowed"])

    def test_api_auth_and_missing_identity_fail_closed(self):
        for mode, code in (("api", "wrong_auth_mode"), ("noidentity", "identity_unavailable"), ("loggedout", "not_authenticated")):
            with self.subTest(mode=mode):
                result, saved = self.run_mode(mode)
                self.assertFalse(result["authenticated"])
                self.assertIsNone(result["identity_hash"])
                self.assertEqual(result["error"]["code"], code)
                self.assertNotIn("account/rateLimits/read", saved["methods"])

    def test_quota_failure_does_not_fake_zero_or_discard_identity(self):
        for mode in ("quota_error", "quota_missing", "quota_timeout"):
            with self.subTest(mode=mode):
                result, saved = self.run_mode(mode, timeout=.4)
                self.assertTrue(result["authenticated"])
                self.assertTrue(result["identity_verified"])
                self.assertIsNone(result["credits"])
                self.assertIsNone(result["rate_limits"])
                self.assertEqual(result["error"]["code"], "quota_unavailable")

    def test_credits_are_observations_without_spend_safety_claim(self):
        result, saved = self.run_mode("paid")
        self.assertEqual(result["credits"]["balance"], "19.75")
        self.assertTrue(result["credits"]["hasCredits"])
        self.assertNotIn("billing_safe", result)
        self.assertIn("not proof", result["billing_limitation"])

    def test_malformed_response_and_errors_do_not_leak_raw_content(self):
        for mode, code in (("malformed", "protocol_invalid"), ("error", "client_response_error"), ("flood", "output_limit")):
            with self.subTest(mode=mode):
                result, saved = self.run_mode(mode)
                self.assertFalse(result["authenticated"])
                self.assertEqual(result["error"]["code"], code)

    def test_timeout_cleans_process_and_threads(self):
        result, saved = self.run_mode("timeout", timeout=.3)
        self.assertEqual(result["error"]["code"], "timeout")

    def test_orphaned_stdout_child_is_owned_and_terminated(self):
        result, saved = self.run_mode("orphan", timeout=.3)
        self.assertFalse(result["authenticated"])
        self.assertNotEqual(result["error"]["code"], "cleanup_unverified")
        self.assertIs(process_identity(saved["child_pid"]), False)
        # No retry or delayed teardown: cwd and inherited pipes must already be free.
        self.temp.cleanup()
        self.assertFalse(self.root.exists())

    def test_cleanup_deadline_rejects_an_unterminated_owned_job(self):
        import ctypes

        class Accounting(ctypes.Structure):
            _fields_ = [("active", ctypes.c_uint32)]

        def still_active(handle, information, pointer, size, returned):
            pointer._obj.active = 1
            return True

        kernel = SimpleNamespace(TerminateJobObject=mock.Mock(return_value=True),
                                 QueryInformationJobObject=mock.Mock(side_effect=still_active),
                                 CloseHandle=mock.Mock(return_value=True))
        process = SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(), stderr=io.BytesIO(),
                                  returncode=0, wait=mock.Mock(), _handle=SimpleNamespace(Close=mock.Mock()))
        started = time.monotonic()
        with self.assertRaises(_DiagnosticError) as raised:
            _stop(process, (kernel, 123, Accounting), timeout=.03)
        self.assertEqual(raised.exception.code, "cleanup_unverified")
        self.assertLess(time.monotonic() - started, .5)
        kernel.TerminateJobObject.assert_called_once_with(123, 1)
        kernel.CloseHandle.assert_called_once_with(123)
        process.wait.assert_called_once()
        self.assertTrue(all(pipe.closed for pipe in (process.stdin, process.stdout, process.stderr)))

    def test_no_implicit_home_or_api_environment(self):
        result = diagnose_codex([sys.executable], cwd=self.root, env={}, timeout=1)
        self.assertEqual(result["error"]["code"], "isolation_unverified")
        self.env["OPENAI_API_KEY"] = "must-not-use"
        result = diagnose_codex([sys.executable], cwd=self.root, env=self.env, timeout=1)
        self.assertEqual(result["error"]["code"], "wrong_auth_mode")


if __name__ == "__main__":
    unittest.main()
