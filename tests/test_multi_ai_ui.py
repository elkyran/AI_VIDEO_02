"""HTTP integration tests with a real temporary coordinator; no media/model calls."""
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
import unittest

from app.multi_ai import Coordinator
from app.multi_ai_ui import PanelServer


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.coordinator = Coordinator(self.temp.name)
        self.server = PanelServer(('127.0.0.1', 0), self.coordinator, enable_worker=False)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval':.01}, daemon=True)
        self.thread.start()
        self.cookie = None

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.coordinator.close()
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        request_headers = {}
        if self.cookie:
            request_headers['Cookie'] = self.cookie
        if body is not None:
            request_headers.update({'Content-Type':'application/json', 'X-CSRF-Token':self.server.token})
        request_headers.update(headers or {})
        connection.request(method,path,json.dumps(body).encode() if body is not None else None,request_headers)
        response = connection.getresponse()
        content = response.read().decode('utf-8')
        cookie = response.getheader('Set-Cookie')
        if cookie:
            self.cookie = cookie.split(';')[0]
        result = response.status, content, dict(response.headers)
        connection.close()
        return result

    def action(self, name, **fields):
        status, text, _ = self.request('POST','/api/action',{'action':name,**fields})
        self.assertEqual(status,200,text)
        return json.loads(text)

    def test_panel_and_actions_survive_browser_reconnect(self):
        status, html, headers = self.request('GET','/')
        self.assertEqual(status,200)
        self.assertIn('Офлайн · симуляция',html)
        self.assertNotIn('https://',html)
        self.assertIn('frame-ancestors',headers['Content-Security-Policy'])
        self.action('demo')
        self.action('resume')
        for _ in range(8):
            self.coordinator.tick()
        self.action('pause')
        self.cookie=None
        self.request('GET','/')
        status, text, _=self.request('GET','/api/state')
        state=json.loads(text)
        self.assertEqual(status,200)
        self.assertEqual(len(state['profiles']),8)
        self.assertEqual({p['profile_id'] for p in state['profiles']},
                         {f'{provider}-{slot}' for provider in ('codex','gemini') for slot in range(1,5)})
        self.assertEqual(len(state['roles']),3)
        self.assertEqual(state['counts']['completed'],3)
        self.assertEqual(len(state['artifacts']),3)
        self.assertEqual(state['control'],'paused')
        self.assertTrue(all(p['quota_remaining'] is None for p in state['profiles']))

    def test_host_origin_and_csrf_reject_foreign_requests(self):
        self.assertEqual(self.request('GET','/api/state')[0],403)
        self.assertEqual(self.request('GET','/',headers={'Host':'attacker.invalid'})[0],403)
        self.request('GET','/')
        self.assertEqual(self.request('POST','/api/action',{'action':'demo'}, {'X-CSRF-Token':'bad'})[0],403)
        self.assertEqual(self.request('POST','/api/action',{'action':'demo'}, {'Origin':'https://attacker.invalid'})[0],403)
        self.assertEqual(self.request('POST','/api/action',{'action':'delete_everything'})[0],400)
        self.assertFalse(self.coordinator.snapshot()['tasks'])

    def test_only_registered_artifacts_are_downloaded(self):
        self.request('GET','/')
        self.action('demo');self.action('resume')
        for _ in range(8):self.coordinator.tick()
        artifact=self.coordinator.snapshot()['artifacts'][0]
        status,text,_=self.request('GET','/api/artifact?id='+artifact['id'])
        self.assertEqual(status,200,text)
        self.assertTrue(json.loads(text)['simulated'])
        self.assertEqual(self.request('GET','/api/artifact?id=../registry.sqlite')[0],400)
        self.assertEqual(self.request('GET','/registry.sqlite')[0],404)

    def test_sse_replays_only_events_after_cursor(self):
        self.request('GET','/');self.action('demo')
        rows=self.coordinator.events()['events']
        cursor=rows[-2]['sequence']
        connection=HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        connection.request('GET','/api/events',headers={'Cookie':self.cookie,'Last-Event-ID':str(cursor)})
        response=connection.getresponse()
        self.assertEqual(response.status,200)
        self.assertEqual(response.readline().decode().strip(),'id: '+str(rows[-1]['sequence']))
        line=response.readline().decode()
        event=json.loads(line.removeprefix('data: '))
        self.assertEqual(event['sequence'],rows[-1]['sequence'])
        self.assertEqual(event['payload'],rows[-1]['payload'])
        response.close();connection.close()

    def test_loopback_only(self):
        with self.assertRaises(ValueError):
            PanelServer(('0.0.0.0',0),self.coordinator)


if __name__ == '__main__':
    unittest.main()
