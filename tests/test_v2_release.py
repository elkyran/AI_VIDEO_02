from pathlib import Path
import unittest
from unittest.mock import patch

import tests.test_v2_production as fixtures
from app.multi_ai_v2 import RunCoordinator
from app.v2_production import OfflineProductionBridge
from app.v2_release import prepare_release,verify_release,delivery_plan,copy_to_fixture
from app.v2_contracts import Artifact
from app.registry import StudioError


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        fixtures.ProductionCandidateTests.setUp(self)
        self.candidate=fixtures.ProductionCandidateTests.candidate(self)
        self.mlt=self.bridge.prepare_mlt(self.candidate)

    def test_release_restart_and_local_copy_never_claim_remote_upload(self):
        release=prepare_release(self.bridge,self.mlt)
        value=verify_release(self.q,release)
        self.assertFalse(value['uploaded']);self.assertEqual(value['remote_sync'],'UNVERIFIED')
        self.assertTrue(all('registry.sqlite' not in entry['artifact']['path'] for entry in value['files']))
        self.q.close();self.q=RunCoordinator(self.root/'queue');self.addCleanup(self.q.close)
        self.bridge=OfflineProductionBridge(self.q,self.engine)
        self.assertEqual(release,prepare_release(self.bridge,self.mlt))
        target=self.root/'fixture_delivery';target.mkdir()
        one=copy_to_fixture(self.q,release,target)
        self.assertEqual(one,copy_to_fixture(self.q,release,target))
        self.assertFalse(one['uploaded']);self.assertIsNone(delivery_plan(self.q,release)['cloud_receipt'])

    def test_partial_delivery_retry_and_conflict_preserve_files(self):
        release=prepare_release(self.bridge,self.mlt)
        target=self.root/'fixture_delivery';target.mkdir()
        original=self.q.store.copy_to_drive;calls=[]
        def interrupted(artifact,root):
            calls.append(artifact)
            if len(calls)==2:raise OSError('Injected network/sync interruption')
            return original(artifact,root)
        with patch.object(self.q.store,'copy_to_drive',side_effect=interrupted):
            with self.assertRaises(OSError):copy_to_fixture(self.q,release,target)
        self.assertEqual(copy_to_fixture(self.q,release,target)['remote_sync'],'UNVERIFIED')
        artifact=calls[0];path=target/artifact.path;path.write_bytes(b'Manual destination edit')
        with self.assertRaises(ValueError):copy_to_fixture(self.q,release,target)
        self.assertEqual(path.read_bytes(),b'Manual destination edit')

    def test_changed_release_file_missing_file_and_forbidden_database_fail(self):
        release=prepare_release(self.bridge,self.mlt)
        value=verify_release(self.q,release)
        path=self.q.store.verify(Artifact.model_validate(value['files'][0]['artifact']))
        path.write_bytes(b'changed')
        with self.assertRaises(ValueError):verify_release(self.q,release)
        path.unlink()
        with self.assertRaises(ValueError):verify_release(self.q,release)
        with self.assertRaises(ValueError):self.q.store.put_file(self.run_id,self.q.root/'registry.sqlite',
            category='backups',simulated=True)

    def test_interrupted_file_import_retries_without_publishing_corrupt_artifact(self):
        source=self.engine/'copy.txt';source.write_bytes(b'Original fixture content')
        import shutil
        def partial(src,dst):dst.write(b'partial');raise OSError('Injected copy interruption')
        with patch.object(shutil,'copyfileobj',side_effect=partial):
            with self.assertRaises(OSError):self.q.store.put_file(self.run_id,source,category='sources',
                source_root=self.engine,simulated=True)
        artifact=self.q.store.put_file(self.run_id,source,category='sources',source_root=self.engine,simulated=True)
        self.assertEqual(self.q.store.verify(artifact).read_bytes(),source.read_bytes())

    def test_non_host_release_and_non_temporary_destination_are_rejected(self):
        release=prepare_release(self.bridge,self.mlt)
        value=verify_release(self.q,release);value['version']+=1
        forged=self.q.store.put_json(self.run_id,value,simulated=True)
        with self.assertRaises(StudioError):verify_release(self.q,forged.model_dump())
        with self.assertRaises(ValueError):copy_to_fixture(self.q,release,Path(self.root.anchor))
