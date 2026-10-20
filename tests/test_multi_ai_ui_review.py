"""Independent artifact identity/integrity regressions; no browser or media tools."""
from pathlib import Path
import tempfile
import unittest

from app.multi_ai import Coordinator
from app.multi_ai_ui import PanelServer
from app.registry import StudioError


class PanelArtifactIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='studio_ui_review_')
        self.coordinator = Coordinator(self.temporary.name)
        self.server = PanelServer(('127.0.0.1', 0), self.coordinator, enable_worker=False)
        self.coordinator.demo()
        self.coordinator.resume()
        for _ in range(8):
            self.coordinator.tick()
        self.artifact = self.coordinator.snapshot()['artifacts'][0]

    def tearDown(self):
        self.server.server_close()
        self.coordinator.close()
        self.temporary.cleanup()

    def test_missing_metadata_field_is_not_an_artifact_identifier(self):
        with self.assertRaises(FileNotFoundError):
            self.server.artifact('None')
        self.assertTrue(self.server.artifact(self.artifact['id']).is_file())

    def test_changed_result_is_not_served_under_its_accepted_identity(self):
        path = Path(self.temporary.name) / self.artifact['path']
        path.write_text('{"payload":"changed outside the accepted attempt"}', encoding='utf-8')
        with self.assertRaises((StudioError, ValueError)):
            self.server.artifact(self.artifact['id'])


if __name__ == '__main__':
    unittest.main()
