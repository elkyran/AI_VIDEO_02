"""Real stdio protocol tests against the optional official MCP SDK."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

from app.contracts import ProjectBrief, Task
from app.registry import Registry, canonical_json, content_hash


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(importlib.util.find_spec("mcp"), "Install optional MCP SDK to test stdio transport")
class MCPIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_protocol_core_parity_idempotency_and_restricted_tools(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        with tempfile.TemporaryDirectory(prefix="studio_mcp_") as folder:
            workspace = Path(folder) / "Проект с пробелами"
            with Registry(workspace) as registry:
                registry.initialize(ProjectBrief(project_id="mcp_demo", title="Protocol test", work_title="Original demo", character="Narrator"))
                registry.create_task(Task(id="task_demo", project_id="mcp_demo", instruction="Write one original sentence", result_contract="ScriptBlock"),
                                     base_revision=1, idempotency_key="create_task_demo")
                expected_status = registry.status("mcp_demo")
                expected_task = registry.next_task("mcp_demo")
            environment = os.environ.copy()
            environment["PYTHONUTF8"] = "1"
            parameters = StdioServerParameters(command=sys.executable, args=["-m", "app.mcp_server", "--workspace", str(workspace)],
                                               cwd=str(ROOT), env=environment)
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    initialization = await session.initialize()
                    self.assertEqual(initialization.serverInfo.name, "Universal AI Video Studio")
                    listing = await session.list_tools()
                    names = {tool.name for tool in listing.tools}
                    self.assertEqual(names, {"studio_status", "studio_next_task", "studio_search", "studio_read", "studio_submit",
                                             "studio_run", "studio_job", "studio_review", "studio_patch", "studio_package"})
                    status = await session.call_tool("studio_status", {"project_id": "mcp_demo"})
                    self.assertFalse(status.isError)
                    self.assertEqual(status.structuredContent, expected_status)
                    task = await session.call_tool("studio_next_task", {"project_id": "mcp_demo"})
                    self.assertEqual(task.structuredContent, expected_task)
                    missing = await session.call_tool("studio_read", {"project_id": "mcp_demo", "object_id": "../../secret.txt"})
                    self.assertIn("error", missing.structuredContent)
                    invalid = await session.call_tool("studio_run", {"project_id": "mcp_demo", "operation": "shell",
                        "parameters": {"command": "echo forbidden"}, "base_revision": 2, "idempotency_key": "bad_op"})
                    self.assertTrue(invalid.isError)
                    submission = {"project_id": "mcp_demo", "base_revision": 2, "task_id": "task_demo", "idempotency_key": "response_demo",
                                  "result": {"id": "block_demo", "project_id": "mcp_demo", "revision": 1,
                                             "purpose": "demo", "text": "Это собственный тестовый текст."}}
                    first = await session.call_tool("studio_submit", {"submission": submission})
                    second = await session.call_tool("studio_submit", {"submission": submission})
                    self.assertEqual(first.structuredContent, second.structuredContent)
                    self.assertEqual(first.structuredContent["state"], "done")
                    self.assertEqual(first.structuredContent["revision"], 3)
                    changed = json.loads(json.dumps(submission))
                    changed["result"]["text"] = "Changed text"
                    conflict = await session.call_tool("studio_submit", {"submission": changed})
                    self.assertEqual(conflict.structuredContent["error"]["code"], "IDEMPOTENCY_CONFLICT")
                    # A saved review fixture checks protocol access and integrity, not media quality.
                    from app.jobs import JobManager
                    JobManager(workspace)
                    qa_id = "job_review_fixture"
                    qa_directory = workspace / "jobs" / qa_id
                    qa_directory.mkdir(parents=True)
                    report = {"schema_version": "1.0", "project_id": "mcp_demo", "build_id": "fixture_build",
                              "build_sha256": "0" * 64, "status": "draft", "checks": [
                                  {"name": "fixture_only", "status": "not_checked", "evidence": "Reader test only"}]}
                    report_path = qa_directory / "qa_report.json"
                    report_path.write_text(json.dumps(report), encoding="utf-8")
                    speech_path = qa_directory / "asr_report.json"
                    speech_path.write_text(json.dumps({"status": "automated_warning", "human_review": "not_checked", "segments": [
                        {"block_id": "block_demo", "status": "automated_warning", "wer": .2,
                         "warnings": ["critical_terms_mismatch"], "missing_critical_terms": ["Demo"]}]}), encoding="utf-8")
                    relative_report = report_path.relative_to(workspace).as_posix()
                    relative_speech = speech_path.relative_to(workspace).as_posix()
                    qa_result = {"report": relative_report, "report_sha256": content_hash(report_path), "frames": [],
                                 "asr_report": relative_speech, "artifact_hashes": {relative_speech: content_hash(speech_path)}}
                    with Registry(workspace) as registry:
                        registry.db.execute("INSERT INTO jobs(id,project_id,operation,parameters,state,result,created,input_revision) VALUES(?,?,?,?,?,?,?,?)",
                                            (qa_id, "mcp_demo", "qa", "{}", "done", canonical_json(qa_result), time.time(), 3))
                    review = await session.call_tool("studio_review", {"job_id": qa_id})
                    self.assertFalse(review.isError)
                    self.assertEqual(review.structuredContent["report"]["checks"], report["checks"])
                    self.assertEqual(review.structuredContent["speech"]["warning_segment_count"], 1)
                    report_path.write_text(json.dumps({**report, "status": "accepted"}), encoding="utf-8")
                    modified = await session.call_tool("studio_review", {"job_id": qa_id})
                    self.assertEqual(modified.structuredContent["error"]["code"], "ASSET_CHANGED")
                    with Registry(workspace) as registry:
                        registry.db.execute("UPDATE jobs SET result=? WHERE id=?", (canonical_json({**qa_result, "report": "../outside.json"}), qa_id))
                    traversal = await session.call_tool("studio_review", {"job_id": qa_id})
                    self.assertEqual(traversal.structuredContent["error"]["code"], "PATH_UNSAFE")
                    with Registry(workspace) as registry:
                        registry.db.execute("UPDATE jobs SET state='pending' WHERE id=?", (qa_id,))
                    unfinished = await session.call_tool("studio_review", {"job_id": qa_id})
                    self.assertEqual(unfinished.structuredContent["error"]["code"], "REVIEW_NOT_READY")
            # A new process sees the same committed result after the client closes.
            with Registry(workspace) as registry:
                self.assertEqual(registry.status("mcp_demo")["revision"], 3)
                self.assertEqual(registry.read_task("mcp_demo", "task_demo").state, "done")


if __name__ == "__main__":
    unittest.main()
