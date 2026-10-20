import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class CliTests(unittest.TestCase):
    def test_windows_console_encoding_does_not_change_json_exchange(self):
        with tempfile.TemporaryDirectory(prefix="Кириллица в CLI ") as temporary:
            root = Path(temporary)
            brief = {"project_id": "pilot", "title": "Второй шанс", "work_title": "Реинкарнация безработного", "character": "Рудеус"}
            (root / "brief.json").write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
            environment = {**os.environ, "PYTHONIOENCODING": "cp1251"}
            command = [sys.executable, "-m", "app", "--workspace", str(root)]
            initialized = subprocess.run([*command, "init", "--input", "brief.json"], capture_output=True, env=environment)
            self.assertEqual(initialized.returncode, 0, initialized.stderr.decode("utf-8"))
            state = subprocess.run([*command, "status", "pilot"], capture_output=True, env=environment)
            self.assertEqual(state.returncode, 0, state.stderr.decode("utf-8"))
            decoded = json.loads(state.stdout.decode("utf-8"))
            self.assertEqual(decoded["brief"]["character"], "Рудеус")
            self.assertEqual(decoded["brief"]["title"], "Второй шанс")
            self.assertIn("Рудеус".encode("utf-8"), state.stdout)

    def test_invalid_schema_produces_json_error_and_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "bad.json").write_text('{"project_id":"bad","invented":true}', encoding="utf-8")
            result = subprocess.run([sys.executable, "-m", "app", "--workspace", str(root),
                                     "validate", "ProjectBrief", "--input", "bad.json"], capture_output=True)
            self.assertEqual(result.returncode, 2)
            error = json.loads(result.stderr.decode("utf-8"))
            self.assertEqual(error["error"]["code"], "SCHEMA_INVALID")


if __name__ == "__main__":
    unittest.main()
