from pathlib import Path
import tempfile
import unittest
import zipfile

from app.contracts import ProjectBrief, Task, TaskSubmission
from app.library import Library
from app.registry import Registry, StudioError


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Книга с пробелами ")
        self.root = Path(self.temp.name)
        self.registry = Registry(self.root)
        self.registry.initialize(ProjectBrief(project_id="pilot", title="Тест", work_title="Собственный тест", character="Мира"))
        self.library = Library(self.registry)
        self.text = "Глава 1\nМира открыла дверь и увидела сад.\n\nГлава 2\nМира решила остаться дома.\n"
        (self.root / "book.txt").write_text(self.text, encoding="utf-8")

    def tearDown(self):
        self.registry.close()
        self.temp.cleanup()

    def import_book(self, **changes):
        options = dict(project_id="pilot", source_id="book", work_id="work", path="book.txt",
                       base_revision=1, idempotency_key="import01", rights="owned")
        options.update(changes)
        return self.library.import_source(**options)

    def assert_error(self, code, function, *args, **kwargs):
        with self.assertRaises(StudioError) as context:
            function(*args, **kwargs)
        self.assertEqual(context.exception.code, code)

    def test_import_preserves_original_order_and_never_claims_analysis(self):
        result = self.import_book()
        self.assertEqual(result["revision"], 2)
        source = self.library.read("pilot", "book")
        self.assertEqual(source["analysis_status"], "not_started")
        self.assertEqual([row["ordinal"] for row in source["chapters"]], [1, 2])
        original = self.root / source["original_path"]
        self.assertEqual(original.read_bytes(), (self.root / "book.txt").read_bytes())
        (self.root / "book.txt").write_text("changed input", encoding="utf-8")
        self.assertEqual(original.read_text(encoding="utf-8"), self.text)
        self.assertIn("Мира", self.library.read("pilot", "book.chapter0001")["text"])

    def test_duplicate_import_and_idempotency(self):
        first = self.import_book()
        self.assertEqual(first, self.import_book())
        duplicate = self.import_book(source_id="another", base_revision=2, idempotency_key="import02")
        self.assertEqual(duplicate["source_id"], "book")
        self.assertFalse(duplicate["created"])
        self.assertEqual(duplicate["revision"], 2)

    def test_search_returns_registered_ids_and_bounded_reads(self):
        self.import_book()
        matches = self.library.search("pilot", "сад")["matches"]
        self.assertEqual(matches[0]["chapter_id"], "book.chapter0001")
        piece = self.library.read("pilot", matches[0]["chapter_id"], start=8, length=4)
        self.assertEqual(piece["text"], "Мира")
        self.assertEqual(piece["locator"], "txt:0")
        self.assert_error("OBJECT_NOT_FOUND", self.library.read, "pilot", "../../secret")
        self.assert_error("SCHEMA_INVALID", self.library.read, "pilot", "book", length=100000)

    def test_chapters_have_sequential_tasks_and_real_submissions_advance_coverage(self):
        imported = self.import_book()
        queued = self.library.queue_analysis("pilot", "book", base_revision=2, idempotency_key="queue01")
        self.assertEqual(queued["revision"], 3)
        first = self.registry.next_task("pilot")
        self.assertEqual(first["task"]["context"]["chapter_id"], "book.chapter0001")
        self.assertEqual(self.library.source("pilot", "book")["analyzed_chapters"], 0)
        source_hash = imported["report"]["source_sha256"]
        claim = {"id": "claim01", "project_id": "pilot", "work_id": "work", "kind": "event",
                 "statement": "Мира открыла дверь.", "source_refs": [{"source_id": "book", "work_id": "work",
                 "text_revision": 1, "chapter": "book.chapter0001", "start_char": 8, "end_char": 27,
                 "source_sha256": source_hash}]}
        response = TaskSubmission(project_id="pilot", base_revision=3, task_id=first["task"]["id"],
            idempotency_key="analysis01", result={"id": "analysis01", "project_id": "pilot", "source_id": "book",
            "chapter_id": "book.chapter0001", "summary": "Мира увидела сад за дверью.", "claims": [claim]})
        self.registry.submit(response)
        self.assertEqual(self.library.source("pilot", "book")["analysis_status"], "partial")
        self.assertEqual(self.library.read("pilot", "claim01")["statement"], "Мира открыла дверь.")
        second = self.registry.next_task("pilot")
        self.assertEqual(second["task"]["context"]["chapter_id"], "book.chapter0002")
        response = TaskSubmission(project_id="pilot", base_revision=4, task_id=second["task"]["id"],
            idempotency_key="analysis02", result={"id": "analysis02", "project_id": "pilot", "source_id": "book",
            "chapter_id": "book.chapter0002", "summary": "Мира осталась дома.", "claims": []})
        self.registry.submit(response)
        source = self.library.source("pilot", "book")
        self.assertEqual(source["analysis_status"], "chapter_analysis_complete")
        self.assertEqual(source["reconciliation"], "not_checked")
        self.assertFalse(self.registry.next_task("pilot")["available"])
        coverage = self.library.coverage("pilot")
        self.assertFalse(coverage["analysis_complete"])
        self.assertIn("entire_work_not_declared", coverage["sources"][0]["blocking_reasons"])
        self.assertIn("blocking_import_warnings", coverage["sources"][0]["blocking_reasons"])

    def test_fabricated_source_hash_and_wrong_chapter_are_rejected(self):
        self.import_book()
        self.library.queue_analysis("pilot", "book", base_revision=2, idempotency_key="queue01")
        task = self.registry.next_task("pilot")["task"]
        response = TaskSubmission(project_id="pilot", base_revision=3, task_id=task["id"], idempotency_key="bad",
            result={"id": "analysis01", "project_id": "pilot", "source_id": "book", "chapter_id": "book.chapter0002",
                    "summary": "wrong chapter", "claims": []})
        self.assert_error("TASK_RESULT_MISMATCH", self.registry.submit, response)
        response.result["chapter_id"] = "book.chapter0001"
        response.result["claims"] = [{"id": "claim01", "project_id": "pilot", "work_id": "work", "kind": "event",
            "statement": "test", "source_refs": [{"source_id": "book", "work_id": "work", "text_revision": 1,
                "chapter": "book.chapter0001", "start_char": 0, "end_char": 5, "source_sha256": "0" * 64}]}]
        self.assert_error("SOURCE_REF_INVALID", self.registry.submit, response)
        self.assertEqual(self.registry.status("pilot")["revision"], 3)
        self.assertEqual(self.library.source("pilot", "book")["analyzed_chapters"], 0)

    def test_empty_bad_encoding_and_unknown_extension_rejected(self):
        for name, content, code in (("empty.txt", b"", "SOURCE_EMPTY"), ("bad.txt", b"\xff", "SOURCE_ENCODING"), ("scan.pdf", b"test", "FORMAT_UNSUPPORTED")):
            (self.root / name).write_bytes(content)
            self.assert_error(code, self.import_book, path=name)

    def make_epub(self, href="two.xhtml"):
        path = self.root / "book.epub"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="OPS/package.opf"/></rootfiles></container>')
            archive.writestr("OPS/package.opf", f'<package><manifest><item id="one" href="one.xhtml" media-type="application/xhtml+xml"/><item id="two" href="{href}" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="two"/><itemref idref="one"/></spine></package>')
            archive.writestr("OPS/one.xhtml", '<html><head><title>One</title></head><body><p>First section.</p><script>ignore me</script></body></html>')
            archive.writestr("OPS/two.xhtml", '<html><head><title>Two</title></head><body><p>Second section.</p></body></html>')
        return path

    def test_epub_preserves_spine_order_and_ignores_scripts(self):
        self.make_epub()
        self.import_book(path="book.epub")
        source = self.library.read("pilot", "book")
        self.assertEqual([chapter["title"] for chapter in source["chapters"]], ["Two", "One"])
        first = self.library.read("pilot", "book.chapter0001")
        second = self.library.read("pilot", "book.chapter0002")
        self.assertIn("Second section", first["text"])
        self.assertNotIn("ignore me", second["text"])
        self.assertEqual(first["locator"], "epub:OPS/two.xhtml")

    def test_epub_rejects_archive_escape_and_network_reference(self):
        for href in ("../../outside.xhtml", "https://example.com/chapter.xhtml"):
            self.make_epub(href)
            self.assert_error("SOURCE_INVALID", self.import_book, path="book.epub")

    def test_complete_gate_requires_declaration_all_tasks_verified_dossier_and_unchanged_original(self):
        text = "Глава 1\n" + "Мира открыла дверь и увидела сад. " * 6
        (self.root / "book.txt").write_text(text, encoding="utf-8")
        imported = self.import_book(entire_work=True)
        self.library.queue_analysis("pilot", "book", base_revision=2, idempotency_key="queue")
        task = self.registry.next_task("pilot")["task"]
        claim = {"id": "claim01", "project_id": "pilot", "work_id": "work", "kind": "event",
                 "statement": "Мира открыла дверь.", "verification": "passed", "source_refs": [{"source_id": "book", "work_id": "work",
                 "text_revision": 1, "chapter": "book.chapter0001", "start_char": 8, "end_char": 27,
                 "source_sha256": imported["report"]["source_sha256"]}]}
        self.registry.submit(TaskSubmission(project_id="pilot", base_revision=3, task_id=task["id"],
            idempotency_key="analyze", result={"id": "analysis01", "project_id": "pilot", "source_id": "book",
            "chapter_id": "book.chapter0001", "summary": "Мира увидела сад.", "claims": [claim]}))
        self.assertFalse(self.library.coverage("pilot")["analysis_complete"])
        dossier_task = Task(id="dossier_task", project_id="pilot", instruction="Сверить собственный тестовый текст", result_contract="Dossier")
        self.registry.create_task(dossier_task, base_revision=4, idempotency_key="dossier-task")
        dossier = {"id": "dossier01", "project_id": "pilot", "work_id": "work", "character_name": "Мира",
                   "goals": "Посмотреть за дверь", "first_meeting": "Не указана", "relationships": "Не указаны",
                   "decisions": "Открывает дверь", "plot_role": "Главная героиня собственного тестового текста",
                   "claim_ids": ["invented"], "reconciliation": "reviewed"}
        response = TaskSubmission(project_id="pilot", base_revision=5, task_id="dossier_task", idempotency_key="dossier", result=dossier)
        self.assert_error("CLAIM_NOT_FOUND", self.registry.submit, response)
        response.result["claim_ids"] = ["claim01"]
        self.registry.submit(response)
        coverage = self.library.coverage("pilot")
        self.assertTrue(coverage["analysis_complete"])
        self.assertEqual(coverage["eligible_source_ids"], ["book"])
        brief = ProjectBrief(project_id="pilot", title="Тест", work_title="Собственный тест", character="Мира", source_status="analyzed")
        self.registry.update_brief(brief, base_revision=6, idempotency_key="brief-reviewed")
        correction = Task(id="claim_correction", project_id="pilot", instruction="Уточнить утверждение", result_contract="Claim")
        self.registry.create_task(correction, base_revision=7, idempotency_key="claim-correction-task")
        corrected_claim = {**claim, "statement": "Мира открыла дверь и увидела сад."}
        self.registry.submit(TaskSubmission(project_id="pilot", base_revision=8, task_id=correction.id,
                                            idempotency_key="claim-corrected", result=corrected_claim))
        self.assertEqual(self.library.read("pilot", "dossier01")["reconciliation"], "needs_review")
        self.assertFalse(self.library.coverage("pilot")["analysis_complete"])
        source = self.library.source("pilot", "book")
        (self.root / source["original_path"]).write_text("tampered", encoding="utf-8")
        self.assertIn("source_original_changed", self.library.coverage("pilot")["sources"][0]["blocking_reasons"])

    def test_brief_cannot_claim_analyzed_without_registered_coverage(self):
        brief = ProjectBrief(project_id="pilot", title="Тест", work_title="Тест", character="Мира", source_status="analyzed")
        self.assert_error("SOURCE_INCOMPLETE", self.registry.update_brief, brief, base_revision=1, idempotency_key="false-ready")
        self.assertEqual(self.registry.status("pilot")["revision"], 1)


if __name__ == "__main__":
    unittest.main()
