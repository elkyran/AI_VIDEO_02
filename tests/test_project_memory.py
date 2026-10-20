"""Temporary-vault regression checks: never launch studio production or models."""
from pathlib import Path
import json
import shutil
import tempfile
import unittest

from app.development import Journal
from app.project_memory import context, create_note, inventory, map_sync, note_text, read_note, safe_path, task_context, validate


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Карта с пробелами ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = Path(__file__).resolve().parents[1]
        shutil.copytree(source / "knowledge", self.root / "knowledge")
        (self.root / "development").mkdir()
        (self.root / "development/tasks.json").write_bytes((source / "development/tasks.json").read_bytes())
        (self.root / "development/01_DEVELOPMENT_PLAN.md").write_bytes((source / "development/01_DEVELOPMENT_PLAN.md").read_bytes())
        # Copy only explicitly referenced small sources/evidence, no models or production data.
        refs = set()
        for note in inventory(self.root).values():
            refs.update(note["properties"]["code_refs"] + note["properties"]["evidence_refs"])
        for ref in refs:
            target = safe_path(self.root, ref)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(safe_path(source, ref).read_bytes())

    def sync(self):
        map_sync(self.root)
        return map_sync(self.root, apply=True, active=False)

    def test_ids_schema_links_cycles_and_rename(self):
        self.assertEqual(validate(self.root)["status"], "passed")
        path = self.root / "knowledge/capabilities/AUD-ASR.md"
        p, body = read_note(path)
        p.update(title="Распознавание --- с новым названием", aliases=["Новое имя"], unknown_owner={"text": "keep"})
        path.write_text(note_text(p, body + "\nМой текст\n"), encoding="utf-8")
        self.sync()
        self.assertEqual(read_note(path)[0]["unknown_owner"], {"text": "keep"})
        self.assertIn("Мой текст", path.read_text(encoding="utf-8"))
        self.assertIn("AUD-ASR", inventory(self.root))
        self.assertEqual(context(self.root, "Новое имя")["id"], "AUD-ASR")
        p["requires"] = ["AUD-ASR"]
        path.write_text(note_text(p, body), encoding="utf-8")
        self.assertIn("requires cycle", " ".join(validate(self.root)["errors"]))
        p["requires"] = []
        p["parent"] = "AUD-ASR"
        path.write_text(note_text(p, body), encoding="utf-8")
        self.assertIn("parent cycle", " ".join(validate(self.root)["errors"]))

    def test_manual_layout_and_text_survive_dynamic_add_and_two_syncs(self):
        path = self.root / "knowledge/maps/Audio.canvas"
        c = json.loads(path.read_text(encoding="utf-8"))
        n = next(n for n in c["nodes"] if n["id"] == "AUD-ASR")
        n.update(x=9876, y=6543, width=500, height=370, color="2", owner_field="keep")
        manual = dict(id="manual", type="text", text="Ручная заметка", x=-900, y=900, width=400, height=280)
        e = dict(id="manual-edge", fromNode="manual", toNode="AUD-ASR", label="моя связь")
        c["nodes"].append(manual)
        c["edges"].append(e)
        path.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
        create_note(self.root, "capability", "AUD-TEMP", "Временная возможность", area="AREA-AUD")
        self.sync()
        after = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(next(x for x in after["nodes"] if x["id"] == "AUD-ASR"), n)
        self.assertIn(manual, after["nodes"])
        self.assertIn(e, after["edges"])
        self.assertTrue(any(x["id"] == "AUD-TEMP" for x in after["nodes"]))
        before, mtime = path.read_bytes(), path.stat().st_mtime_ns
        for _ in range(2):
            self.assertEqual(self.sync()["applied"], [])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(path.stat().st_mtime_ns, mtime)

    def test_conflict_and_active_editor_create_proposal(self):
        path = self.root / "knowledge/maps/Audio.canvas"
        create_note(self.root, "capability", "AUD-TEMP", "Временная возможность", area="AREA-AUD")
        map_sync(self.root)
        c = json.loads(path.read_text(encoding="utf-8"))
        c["nodes"][0]["x"] += 1700
        path.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
        before = path.read_bytes()
        self.assertTrue(map_sync(self.root, apply=True, active=False)["proposed"])
        self.assertEqual(before, path.read_bytes())
        map_sync(self.root)
        self.assertTrue(map_sync(self.root, apply=True, active=True)["proposed"])
        self.assertEqual(before, path.read_bytes())
        proposal = json.loads(path.with_name("Audio.proposed.canvas").read_text(encoding="utf-8"))
        self.assertEqual(proposal["nodes"][0]["x"], c["nodes"][0]["x"])

    def test_new_area_and_submap_are_data_driven(self):
        create_note(self.root, "area", "AREA-TEMP", "Новая область")
        create_note(self.root, "capability", "TEMP-ONE", "Новая возможность", area="AREA-TEMP")
        for i in range(13):
            create_note(self.root, "capability", f"TEMP-{i:02d}", f"Возможность {i}", area="AREA-TEMP")
        self.sync()
        self.assertTrue((self.root / "knowledge/maps/AREA-TEMP_2.canvas").is_file())
        self.assertEqual(validate(self.root)["status"], "passed")
        state = Journal(self.root).read()[0]
        self.assertEqual(len(state["tasks"]), 13)

    def test_unsafe_paths_duplicate_ids_unknown_parent_rejected(self):
        for ref in ("../outside", "C:/secret", "/tmp/secret", "..\\secret"):
            with self.assertRaises(ValueError):
                safe_path(self.root, ref)
        with self.assertRaises(ValueError):
            create_note(self.root, "capability", "AUD-ASR", "Дубликат", area="AREA-AUD")
        with self.assertRaises(ValueError):
            create_note(self.root, "capability", "AUD-TEMP", "Временная", area="UNKNOWN")

    def test_context_bounded_relevant_and_honest_without_graph(self):
        result = context(self.root, "AUD-ASR")
        self.assertLessEqual(result["metrics"]["characters"], 12000)
        self.assertEqual(result["graph_status"]["state"], "missing")
        refs = [v["ref"] for v in result["references"]]
        self.assertIn("app/speech_review.py", refs)
        self.assertIn("scripts/transcribe_audio.py", refs)
        self.assertNotIn("app/timeline.py", refs)
        self.assertTrue(any("T04" in item for item in result["constraints"]))
        with self.assertRaises(ValueError):
            context(self.root, "UNKNOWN-ID")
        t = task_context(self.root, "T04")
        self.assertIn("Разделить кеш ASR", t["definition"])
        self.assertNotIn("### T05", t["definition"])
        self.assertFalse(t["authorized"])
        p = context(self.root, "AUD-ASR", mode="production")
        self.assertIn("inspect_job", p["operations"])
        self.assertFalse(any(r["kind"] == "code_refs" for r in p["references"]))
        self.assertEqual(p["graph_results"], [])

    def test_large_constraints_are_never_silently_clipped(self):
        path = self.root / "knowledge/capabilities/AUD-ASR.md"
        p, body = read_note(path)
        body = body.replace("## Ограничения\n", "## Ограничения\n" + "Важное ограничение. " * 1000)
        path.write_text(note_text(p, body), encoding="utf-8")
        result = context(self.root, "AUD-ASR", max_chars=2000)
        self.assertIn("budget_exceeded", result)
        self.assertIn("Важное ограничение. " * 1000, result["constraints"][-1])

    def test_symlink_escape_rejected_when_supported(self):
        with tempfile.TemporaryDirectory() as outside:
            link = self.root / "outside"
            try:
                link.symlink_to(Path(outside), target_is_directory=True)
            except OSError:
                self.skipTest("OS does not permit creating a temporary symlink")
            with self.assertRaises(ValueError):
                safe_path(self.root, "outside/file.md")


if __name__ == "__main__":
    unittest.main()
