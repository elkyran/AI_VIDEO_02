"""Account routes share the panel session, CSRF policy and queue reservations."""
from tests import test_multi_ai_ui as panel_tests
from app.gemini_free_route import make_route
from app.multi_ai_ui import PAGE
import shutil
import subprocess
import unittest


class Accounts:
    def __init__(self):
        self.calls = []

    def snapshot(self):
        return {'profiles': [], 'wizard': {'status': 'paused', 'current': 'codex-1'}}

    def start_wizard(self):
        self.calls.append('start')
        return self.snapshot()

    def check(self, profile_id):
        self.calls.append(('check', profile_id))

    def queue_profile(self, profile_id):
        return {'profile_id': profile_id, 'can_probe': False, 'reason': 'billing_unverified'}

    def prepare_gemini_free_route(self, **fields):
        route = make_route(**fields)
        self.calls.append(('prepare_gemini_free', fields))
        return {'profile_id': fields['profile_id'], 'access_route': 'gemini_api_free',
                'model': fields['model'], 'gemini_free_route': route,
                'access_state': 'unverified', 'can_probe': False}


class AccountPanelTests(unittest.TestCase):
    tearDown = panel_tests.PanelTests.tearDown
    request = panel_tests.PanelTests.request
    action = panel_tests.PanelTests.action

    def setUp(self):
        panel_tests.PanelTests.setUp(self)
        self.accounts = Accounts()
        self.server.accounts = self.accounts

    def test_login_action_requires_same_origin_and_csrf(self):
        self.request('GET', '/')
        self.assertEqual(self.request('POST', '/api/action', {'action':'wizard_start'},
                                      {'Origin':'https://attacker.invalid'})[0], 403)
        self.assertFalse(self.accounts.calls)
        self.action('wizard_start')
        self.assertEqual(self.accounts.calls, ['start'])

    def test_account_endpoint_rejects_secrets_and_unknown_fields(self):
        self.request('GET', '/')
        status, _, _ = self.request('POST', '/api/action',
                                    {'action':'account_confirm', 'profile_id':'codex-1', 'password':'do-not-store'})
        self.assertEqual(status, 400)
        self.assertFalse(self.accounts.calls)

    def test_unverified_billing_cannot_enqueue_probe(self):
        self.request('GET', '/')
        result = self.action('account_check', profile_id='codex-1')
        self.assertIsNone(result['probe'])
        self.assertFalse(self.coordinator.snapshot()['tasks'])

    def test_verified_profile_check_preserves_model_quota(self):
        self.request('GET', '/')
        self.accounts.queue_profile = lambda profile_id: {
            'profile_id': profile_id, 'can_probe': True, 'access_state': 'verified'}
        result = self.action('account_check', profile_id='codex-1')
        self.assertIsNone(result['probe'])
        self.assertFalse(self.coordinator.snapshot()['tasks'])

    def test_profile_pause_and_resume_use_account_manager(self):
        self.request('GET', '/')
        self.accounts.pause_profile = lambda profile_id: {'profile_id': profile_id, 'paused': True}
        self.accounts.resume_profile = lambda profile_id: {'profile_id': profile_id, 'paused': False}
        self.assertTrue(self.action('account_pause', profile_id='codex-4')['paused'])
        self.assertFalse(self.action('account_resume', profile_id='codex-4')['paused'])

    def test_active_task_blocks_new_login(self):
        self.request('GET', '/')
        self.coordinator.demo()
        self.coordinator.resume()
        self.coordinator.tick()
        status, _, _ = self.request('POST', '/api/action', {'action':'wizard_start'})
        self.assertEqual(status, 400)
        self.assertFalse(self.accounts.calls)

    def free_route_fields(self):
        return {'profile_id': 'gemini-4', 'model': 'gemini-3.8-flash',
                'project_label': 'studio-free-project', 'free_tier_confirmed': True,
                'no_billing_confirmed': True}

    def test_free_route_preparation_only_prepares_selected_profile(self):
        self.request('GET', '/')
        before = self.coordinator.snapshot()
        fields = self.free_route_fields()
        result = self.action('account_prepare_gemini_free', **fields)
        self.assertEqual(self.accounts.calls, [('prepare_gemini_free', fields)])
        self.assertEqual(result['access_route'], 'gemini_api_free')
        self.assertEqual(result['gemini_free_route']['project_label'], fields['project_label'])
        self.assertEqual(result['access_state'], 'unverified')
        after = self.coordinator.snapshot()
        self.assertEqual(after['tasks'], before['tasks'])
        self.assertEqual(after['attempts'], before['attempts'])
        self.assertEqual(after['control'], before['control'])

    def test_free_route_preparation_rejects_secrets_and_incomplete_attestations(self):
        self.request('GET', '/')
        fields = self.free_route_fields()
        for extra in ('api_key', 'token', 'password'):
            with self.subTest(extra=extra):
                status, _, _ = self.request('POST', '/api/action',
                    {'action': 'account_prepare_gemini_free', **fields, extra: 'do-not-store'})
                self.assertEqual(status, 400)
        for missing in ('project_label', 'model', 'free_tier_confirmed', 'no_billing_confirmed'):
            with self.subTest(missing=missing):
                partial = {key: value for key, value in fields.items() if key != missing}
                self.assertEqual(self.request('POST', '/api/action',
                    {'action': 'account_prepare_gemini_free', **partial})[0], 400)
        for flag in ('free_tier_confirmed', 'no_billing_confirmed'):
            with self.subTest(unconfirmed=flag):
                self.assertEqual(self.request('POST', '/api/action',
                    {'action': 'account_prepare_gemini_free', **fields, flag: False})[0], 400)
        self.assertEqual(self.request('POST', '/api/action',
            {'action': 'account_prepare_gemini_free', **fields,
             'project_label': 'AIzaEXAMPLE_NOT_A_REAL_KEY'})[0], 400)
        self.assertFalse(self.accounts.calls)
        self.assertFalse(self.coordinator.snapshot()['tasks'])

    def test_active_attempt_blocks_free_route_preparation(self):
        self.request('GET', '/')
        self.coordinator.demo()
        self.coordinator.resume()
        self.coordinator.tick()
        status, _, _ = self.request('POST', '/api/action',
            {'action': 'account_prepare_gemini_free', **self.free_route_fields()})
        self.assertEqual(status, 400)
        self.assertFalse(self.accounts.calls)

    @unittest.skipUnless(shutil.which('node'), 'Node is required for the offline form behavior check')
    def test_free_route_forms_send_no_key_or_google_identity_claim(self):
        # Execute the actual form handlers with a tiny DOM; no browser, network,
        # credentials, CLI launch or server-side model executor is involved.
        script = PAGE.split('<script>', 1)[1].split('function renderScenes()', 1)[0]
        dom = r'''
const assert=require('node:assert/strict');
class Element {
 constructor(tag='div'){this.tag=tag;this.children=[];this.hidden=false;this.checked=false;this.value='';}
 append(...nodes){this.children.push(...nodes)}
 prepend(...nodes){this.children.unshift(...nodes)}
 scrollIntoView(){} focus(){document.activeElement=this} blur(){document.activeElement=null}
 closest(){return null}
}
const elements=new Map();
const document={activeElement:null,createElement:tag=>new Element(tag),getElementById:id=>{
 if(!elements.has(id))elements.set(id,new Element());return elements.get(id);
}};
'''
        assertions = r'''
(async()=>{
 const calls=[];action=async(name,args)=>{calls.push({name,args});return true};renderProfiles=()=>{};
 const profile={profile_id:'gemini-4',provider:'gemini',slot:4,access_route:'gemini_api_free',
  model:'gemini-3.8-flash',gemini_free_route:{project_label:'chosen-project',model:'gemini-3.8-flash'}};
 const details=geminiFreePreparation(profile),form=details.children.find(e=>e.tag==='form');
 const all=[];function walk(e){all.push(e);for(const c of e.children)walk(c)}walk(form);
 const inputs=all.filter(e=>e.tag==='input');
 assert.deepEqual(inputs.map(e=>e.name).sort(),['free_tier_confirmed','model','no_billing_confirmed','project_label']);
 assert.equal(inputs.find(e=>e.name==='model').value,'gemini-3.8-flash');
 assert.equal(inputs.find(e=>e.name==='model').readOnly,true);
 inputs.find(e=>e.name==='project_label').value='owner-selected-project';
 inputs.filter(e=>e.type==='checkbox').forEach(e=>{e.checked=true});
 await form.onsubmit({preventDefault(){}});
 assert.deepEqual(calls.pop(),{name:'account_prepare_gemini_free',args:{profile_id:'gemini-4',model:'gemini-3.8-flash',project_label:'owner-selected-project',free_tier_confirmed:true,no_billing_confirmed:true}});
 openIdentity(profile);
 assert.equal($('identity-manual').hidden,true);assert.equal($('identity-subscription').hidden,true);
 assert.equal($('identity-api-free').hidden,false);assert.match($('identity-project').textContent,/chosen-project/);
 $('identity-key-saved').checked=true;$('identity-free-tier').checked=true;$('identity-no-billing').checked=false;
 await $('identity-form').onsubmit({preventDefault(){}});
 assert.deepEqual(calls.pop(),{name:'account_confirm',args:{profile_id:'gemini-4',auth_confirmed:true,billing_confirmed:false}});
 $('identity-no-billing').checked=true;await $('identity-form').onsubmit({preventDefault(){}});
 assert.deepEqual(calls.pop(),{name:'account_confirm',args:{profile_id:'gemini-4',auth_confirmed:true,billing_confirmed:true}});
 openIdentity({profile_id:'codex-4',provider:'codex',slot:4,identity_verification:'verified'});
 assert.equal($('identity-api-free').hidden,true);assert.equal($('identity-subscription').hidden,false);
 assert.equal($('identity-key-saved').required,false);assert.equal($('identity-no-billing').checked,false);
 assert.equal(calls.length,0);console.log('offline form behavior passed');
})().catch(error=>{console.error(error);process.exitCode=1});
'''
        result = subprocess.run([shutil.which('node'), '-'], input=dom + script + assertions,
                                text=True, encoding='utf-8', capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('offline form behavior passed', result.stdout)
