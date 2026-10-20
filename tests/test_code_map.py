from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from app.code_map import corpus, graph_status, publish_export, record_corpus, stage_export


class CodeMapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Граф тестовый ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "app").mkdir()
        (self.root / "graphify-out").mkdir()
        self.code = self.root / "app/example.py"
        self.code.write_text("def example(): return 1\n", encoding="utf-8")
        self.graph = self.root / "graphify-out/graph.json"
        self.graph.write_text(json.dumps({"graph": {"schema_version": 1, "graphify_version": "fixture"}, "nodes": [{"id": "example", "source_file": "app/example.py"}], "links": []}), encoding="utf-8")
        (self.root / "graphify-out/manifest.json").write_text(json.dumps(corpus(self.root)), encoding="utf-8")
        record_corpus(self.root, warnings=[], log_ref="fixture")

    def test_edit_delete_rename_add_and_graph_tampering_detected(self):
        self.assertEqual(graph_status(self.root)["state"], "current")
        original = self.code.read_text(encoding="utf-8")
        self.code.write_text(original + "# changed\n", encoding="utf-8")
        self.assertEqual(graph_status(self.root)["changed_refs"], ["app/example.py"])
        self.code.write_text(original, encoding="utf-8")
        moved = self.code.with_name("renamed.py")
        self.code.rename(moved)
        self.assertEqual(graph_status(self.root)["changed_count"], 2)
        moved.unlink()
        self.assertEqual(graph_status(self.root)["state"], "stale")
        self.code.write_text(original, encoding="utf-8")
        self.graph.write_text(self.graph.read_text(encoding="utf-8") + " ", encoding="utf-8")
        self.assertTrue(graph_status(self.root)["graph_changed"])

    def test_export_preserves_user_notes_unknown_files_and_obsidian(self):
        with patch("app.project_memory.obsidian_running", return_value=False):
            publish_export(self.root, {"nodes/example.md": "original"})
            p = self.root / "knowledge/generated/code/nodes/example.md"
            p.write_text("owner text", encoding="utf-8")
            other = p.with_name("owner.md")
            other.write_text("other", encoding="utf-8")
            config = self.root / "knowledge/.obsidian/graph.json"
            config.parent.mkdir()
            config.write_text('{"owner":true}', encoding="utf-8")
            result = publish_export(self.root, {"nodes/example.md": "new", "nodes/added.md": "added"})
            self.assertTrue(result["proposed"])
            self.assertEqual(p.read_text(encoding="utf-8"), "owner text")
            self.assertEqual(other.read_text(encoding="utf-8"), "other")
            self.assertEqual(config.read_text(encoding="utf-8"), '{"owner":true}')
            self.assertEqual(p.with_name("example.proposed.md").read_text(encoding="utf-8"), "new")

    def test_native_relocation_links_are_namespaced(self):
        s = self.root / "stage"
        s.mkdir()
        (s / "graph.canvas").write_text(json.dumps({"nodes": [{"id": "n_one", "type": "file", "file": "one().md"}, {"id": "n_two", "type": "file", "file": "two().md"}], "edges": []}), encoding="utf-8")
        (s / "one().md").write_text("[[two()|Second]]", encoding="utf-8")
        (s / "two().md").write_text("[[one()]]", encoding="utf-8")
        content = stage_export(s)
        self.assertEqual(content["nodes/one.md"], "[[generated/code/nodes/two.md|Second]]")
        self.assertNotIn(".obsidian", content)


if __name__ == "__main__":
    unittest.main()
