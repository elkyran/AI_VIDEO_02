"""Package A evidence: unit tests and a temporary simulated chain, never production."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import time
from unittest.mock import patch

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sys.path.insert(0, str(ROOT))


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    baseline = json.loads((OUT / "baseline.json").read_text(encoding="utf-8"))
    suites = json.loads((OUT / "baseline_tests.json").read_text())["command"][3:-1]
    suites += ["tests.test_production_profile", "tests.test_development", "tests.test_exchange"]
    command = [sys.executable, "-X", "utf8", "-m", "unittest", *suites, "-v"]
    started = time.monotonic()
    completed = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (OUT / "final_tests.log").write_bytes(completed.stdout)
    result = {"command": [".venv-core/Scripts/python.exe", "-X", "utf8", "-m", "unittest", *suites, "-v"],
              "exit_code": completed.returncode, "duration_seconds": round(time.monotonic() - started, 3),
              "log_ref": "reports/development/2026-10-17_A/final_tests.log"}
    save("final_tests.json", result)
    if completed.returncode:
        print(completed.stdout.decode("utf-8", errors="replace")[-2500:])
        return completed.returncode

    from tests.test_exchange import ExchangeTests
    from app.registry import StudioError
    samples = {}
    for scenario in ("complete_and_repeat", "timeout", "error"):
        case = ExchangeTests()
        case.setUp()
        try:
            if scenario == "complete_and_repeat":
                with case.workers(), patch("app.operations.execute", side_effect=case.execute) as execute:
                    answer = case.chain(repair_reason="T03 isolated smoke; no production content")
                    repeated = case.chain()
                assert answer["state"] == "done" and repeated["job_id"] == answer["job_id"]
                assert execute.call_count == 5 and len(case.manager.list("demo")) == 5
                samples[scenario] = {"response": answer, "repeat_response": repeated, "worker_executions": execute.call_count,
                                     "saved_log": case.log(answer), "repeat_log": case.log(repeated)}
            elif scenario == "timeout":
                job_id = case.pending()
                answer = case.client.wait(job_id, timeout=.02, poll_interval=.01, task_id="T03")
                cancelled = case.client.wait(job_id, cancel_wait=lambda: True, task_id="T03")
                assert answer["wait_status"] == "timeout" and cancelled["wait_status"] == "cancelled"
                assert case.manager.status(job_id)["state"] == "pending"
                samples[scenario] = {"response": answer, "cancelled_response": cancelled, "saved_log": case.log(answer)}
            else:
                with case.workers(), patch("app.operations.execute", side_effect=StudioError("SIMULATED_ERROR", "Deliberate local test error")):
                    answer = case.chain()
                assert answer["errors"] and answer["state"] == "failed"
                samples[scenario] = {"response": answer, "saved_log": case.log(answer)}
            # Temporary workspace logs are embedded here so evidence survives cleanup.
            samples[scenario]["source"] = "temporary registry; mocked worker/execute; original log_ref is workspace-relative"
        finally:
            case.doCleanups()
    save("exchange_smoke.json", samples)

    comparison = {ref: {"same_sha256": sha(ROOT / ref) == value["sha256"],
                        "same_size": (ROOT / ref).stat().st_size == value["size"],
                        "same_mtime": (ROOT / ref).stat().st_mtime_ns == value["mtime_ns"]}
                  for ref, value in baseline["protected_files"].items()}
    existing_changes = [ref for ref, old_hash in baseline["source_files"].items() if sha(ROOT / ref) != old_hash]
    save("preservation_checks.json", {"status": "passed" if all(v["same_sha256"] for v in comparison.values()) else "failed",
                                      "protected_files": comparison, "changed_existing_source_files": existing_changes})
    assert all(v["same_sha256"] for v in comparison.values())
    assert existing_changes == [".gitignore"], existing_changes
    print(json.dumps({"test_exit_code": completed.returncode, "seconds": result["duration_seconds"],
                      "smoke_scenarios": list(samples), "protected_files_unchanged": len(comparison)}))
    print(completed.stdout.decode("utf-8", errors="replace")[-130:].strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
