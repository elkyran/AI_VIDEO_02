"""Ordinary launcher -> exact host resolver -> real Drive HTTP with fake request IO."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs,urlparse

from app.v2_delivery_host import DriveHostAuthorization,DriveHostContext,DriveHostResolver
from app.v2_drive import GoogleDriveHTTP
from app.v2_host_services import HostServices
from app.v2_narration import profile_spec
from app.v2_run_config import HostRunConfig
from scripts.launch_multi_ai_v2 import main as launch
from tests.test_v2_system import fixture
from tests.test_v2_system_delivery import MemoryDrive
from tests.test_v2_closure_integration import NativeIO


class OrdinaryDeliveryTests(unittest.TestCase):
    def test_ordinary_launcher_passes_exact_scope_budget_and_fake_http_without_live_claim(self):
        with tempfile.TemporaryDirectory(prefix='closure_drive_entry_') as temp:
            folder=Path(temp);data=fixture(folder,1).model_dump();data['mode']='approved_input';data['brief']['production_mode']='production'
            data['inputs']['canon']['simulated']=False
            for scene in data['inputs']['scenes']:
                scene['simulated']=False
                for part in scene['narration_segments']:part['simulated']=False
            data['drive']={'folder_id':'exact_folder','credential_ref':'approved_callback','network_write_authorized':True}
            data['operations'].append('deliver');data['budget']['external_requests']=100
            voice=profile_spec()['voice']
            data['runtime']={'tts_python':sys.executable,'tts_model':'resource-boundary','melt_path':sys.executable,
                'ffprobe_path':'no-native-probe','ffmpeg_path':'no-native-decode','tts_engine':'piper',
                'tts_normalization_scope':voice['normalization_scope'],'pronunciation_lexicon':profile_spec()['pronunciation_lexicon'],
                **{'tts_'+k:v for k,v in voice['settings'].items() if k!='speaker_id'}}
            config=HostRunConfig.model_validate(data);file=folder/'config.json';file.write_text(config.model_dump_json(),encoding='utf-8')
            host=DriveHostContext(DriveHostResolver([DriveHostAuthorization('approved_callback','exact_folder',config.run_id,1,
                lambda:'synthetic-opaque-callback')]),folder/'private_sessions',authorized=True)
            services=HostServices(drive_host=host);drive=MemoryDrive();requests=[];scopes=[]
            original=DriveHostResolver.resolve
            def resolve(resolver,**scope):scopes.append(scope);return original(resolver,**scope)
            def http(transport,method,url,*,body=None,headers=None):
                requests.append((method,url));self.assertEqual(transport.token_supplier(),'synthetic-opaque-callback')
                self.assertEqual(transport.timeout,30);self.assertLessEqual(transport.remaining,100)
                transport.remaining-=1
                parsed=urlparse(url);query=parse_qs(parsed.query)
                if 'generateIds' in parsed.path:return 200,{}, {'ids':[drive.allocate()]}
                if method=='GET' and 'q' in query:
                    name=re.search("name='([^']+)'",query['q'][0]).group(1)
                    return 200,{}, {'files':[v for v in drive.objects.values() if v['name']==name]}
                if method=='GET':return 200,{},drive.inspect(parsed.path.rsplit('/',1)[1])
                if method=='POST':
                    value=json.loads(body);identifier=value['id'];drive.begin(identifier,value['parents'][0],value['name'],value['appProperties']['system_key'],0)
                    return 200,{'Location':'https://www.googleapis.com/upload/drive/v3/'+identifier},{}
                identifier=parsed.path.rsplit('/',1)[1];total=int(headers['Content-Range'].rsplit('/',1)[1])
                if not body:return 200,{},{}
                offset=int(headers['Content-Range'].split()[1].split('-')[0]);drive.chunk(identifier,offset,body,total)
                return 200,{},{}
            native=NativeIO();native.duration=1
            def call(*args,expected=0,bindings=services):
                with redirect_stdout(io.StringIO()) as out:code=launch(['--config',str(file),*args],host_services=bindings)
                text=out.getvalue();self.assertNotIn('synthetic-opaque',text);self.assertNotIn('/upload/drive/v3/',text)
                value=json.loads(text);self.assertEqual(code,expected,value);return value
            with native(None,{'stage':'native_boundaries'}), \
                 patch.object(DriveHostResolver,'resolve',new=resolve), \
                 patch('app.v2_delivery_host.PrivateSessionStore',return_value={}), \
                 patch.object(GoogleDriveHTTP,'is_simulated',True),patch.object(GoogleDriveHTTP,'request',new=http):
                self.assertEqual(call('--execute-system','--authorize','start')['status'],'READY_FOR_OWNER_REVIEW')
                self.assertEqual(scopes,[])
                self.assertEqual(call('--execute-system','deliver',expected=3,bindings=None)['status'],'WAITING_AUTH')
                value=call('--execute-system','deliver');self.assertEqual(value['status'],'SIMULATED_DELIVERY_VERIFIED')
                self.assertFalse(value['uploaded']);self.assertNotEqual(value['remote_sync'],'VERIFIED')
                count=len(requests);replay=call('--execute-system','deliver');self.assertEqual(value,replay);self.assertEqual(count,len(requests))
                self.assertEqual(call('--execute-system','inspect-artifacts')['status'],'RECORDED_LOCAL_PACKAGE')
            self.assertEqual(scopes,[dict(credential_ref='approved_callback',folder_id='exact_folder',run_id=config.run_id,version=1)]*2)
            self.assertEqual(len(drive.objects),len(value['objects']));self.assertGreaterEqual(len(drive.objects),3)
