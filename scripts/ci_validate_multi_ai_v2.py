"""Independent GitHub-hosted validation for Multi-AI v2.

This entrypoint is intentionally account-free: it never reads provider credentials,
starts a model, touches production media, or uses a self-hosted runner. It validates
checked-in contracts/workflow safety and runs the bounded unit/regression suites.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.editorial import EditPlan, Scene
from app.v2_contracts import Artifact, Brief, Result, Review, Task
from scripts.check_multi_ai import GROUPS

CHECKOUT_SHA = "11bd71901bbe5b1630ceea73d27597364c9af683"
SETUP_PYTHON_SHA = "a26af69be951a213d495a4c3e4e4022e16d87065"
HEX40 = re.compile(r"^[0-9a-f]{40}$")


class ValidationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def load_workflow(path: Path) -> dict:
    value = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    require(isinstance(value, dict), f"Workflow is not a mapping: {path}")
    return value


def steps_for(workflow: dict, job: str) -> list[dict]:
    jobs = workflow.get("jobs")
    require(isinstance(jobs, dict) and job in jobs, f"Missing job {job}")
    steps = jobs[job].get("steps")
    require(isinstance(steps, list), f"Missing steps for job {job}")
    return steps


def action_step(steps: list[dict], prefix: str) -> dict:
    matches = [step for step in steps if isinstance(step, dict) and str(step.get("uses", "")).startswith(prefix)]
    require(len(matches) == 1, f"Expected exactly one {prefix} step")
    return matches[0]


def validate_self_hosted_workflow() -> None:
    path = ROOT / ".github/workflows/new-video.yml"
    workflow = load_workflow(path)
    triggers = workflow.get("on")
    require(isinstance(triggers, dict) and set(triggers) == {"workflow_dispatch"},
            "Self-hosted workflow must be manual-only")
    inputs = triggers["workflow_dispatch"].get("inputs", {})
    require("production_mode" not in inputs,
            "Self-hosted workflow must not expose production mode before live review")
    permissions = workflow.get("permissions", {})
    require(permissions == {"contents": "read"}, "Self-hosted workflow permissions must be contents: read only")
    job = workflow["jobs"]["request"]
    runs_on = job.get("runs-on")
    require(isinstance(runs_on, list) and {"self-hosted", "Windows", "ai-video-02"} <= set(runs_on),
            "Self-hosted workflow must use the dedicated Windows runner labels")
    condition = str(job.get("if", ""))
    require("github.event.repository.private == true" in condition and "github.ref == 'refs/heads/main'" in condition,
            "Self-hosted workflow must require a private repository and refs/heads/main")
    steps = steps_for(workflow, "request")
    checkout = action_step(steps, "actions/checkout@")
    action_ref = checkout["uses"].split("@", 1)[1]
    require(action_ref == CHECKOUT_SHA and HEX40.fullmatch(action_ref) is not None,
            "actions/checkout must remain pinned to the reviewed commit")
    options = checkout.get("with", {})
    require(options.get("clean") == "true", "Self-hosted checkout must use clean: true")
    require(options.get("persist-credentials") == "false", "Self-hosted checkout must not persist credentials")
    require(options.get("ref") == "${{ github.sha }}", "Self-hosted checkout must pin the exact event SHA")
    text = path.read_text(encoding="utf-8")
    require("pull_request:" not in text and "schedule:" not in text,
            "Credentialed self-hosted workflow must not run from PRs or schedules")


def validate_ci_workflow() -> None:
    path = ROOT / ".github/workflows/ci-multi-ai-v2.yml"
    workflow = load_workflow(path)
    triggers = workflow.get("on")
    require(isinstance(triggers, dict) and "pull_request" in triggers and "push" in triggers,
            "CI must run for pull requests and reviewed branch pushes")
    permissions = workflow.get("permissions", {})
    require(permissions == {"contents": "read"}, "CI permissions must be contents: read only")
    job = workflow["jobs"]["test"]
    require(job.get("runs-on") == "windows-latest", "Independent CI must use GitHub-hosted windows-latest")
    require("self-hosted" not in json.dumps(workflow), "Independent CI must never target a self-hosted runner")
    steps = steps_for(workflow, "test")
    checkout = action_step(steps, "actions/checkout@")
    checkout_ref = checkout["uses"].split("@", 1)[1]
    require(checkout_ref == CHECKOUT_SHA and HEX40.fullmatch(checkout_ref) is not None,
            "CI checkout action must remain commit-pinned")
    setup = action_step(steps, "actions/setup-python@")
    setup_ref = setup["uses"].split("@", 1)[1]
    require(setup_ref == SETUP_PYTHON_SHA and HEX40.fullmatch(setup_ref) is not None,
            "setup-python action must remain commit-pinned")


def generated_schemas() -> dict[str, dict]:
    models = {
        "brief": Brief,
        "task": Task,
        "result": Result,
        "artifact": Artifact,
        "scene": Scene,
        "edit_plan": EditPlan,
        "review": Review,
    }
    return {f"{name}.schema.json": model.model_json_schema() for name, model in models.items()}


def validate_schema_drift() -> None:
    expected_dir = ROOT / "config/multi_ai_v2_schemas"
    generated = generated_schemas()
    checked_in = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in expected_dir.glob("*.schema.json")
    }
    require(set(checked_in) == set(generated),
            f"Schema file set drift: checked-in={sorted(checked_in)} generated={sorted(generated)}")
    mismatches = [name for name in sorted(generated) if generated[name] != checked_in[name]]
    require(not mismatches, "Schema drift detected: " + ", ".join(mismatches))
    # Prove generation is isolated from the repository. Canonical JSON avoids
    # platform line-ending differences while preserving every schema value.
    with tempfile.TemporaryDirectory(prefix="multi_ai_v2_schema_ci_") as folder:
        destination = Path(folder)
        for name, value in generated.items():
            payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            (destination / name).write_text(payload, encoding="utf-8", newline="\n")
        require(all(json.loads((destination / name).read_text(encoding="utf-8")) == value
                    for name, value in generated.items()),
                "Temporary schema generation failed")


def run(command: list[str], label: str) -> None:
    print(f"\n=== {label} ===", flush=True)
    result = subprocess.run(command, cwd=ROOT, check=False)
    if result.returncode:
        raise ValidationError(f"{label} failed with exit code {result.returncode}")


def run_test_groups() -> None:
    groups = {"v2": ["tests.test_multi_ai_v2"], **GROUPS}
    for name, modules in groups.items():
        run([sys.executable, "-X", "utf8", "-m", "unittest", "-v", *modules], f"tests:{name}")


def main() -> int:
    try:
        validate_self_hosted_workflow()
        validate_ci_workflow()
        validate_schema_drift()
        run([sys.executable, "-X", "utf8", "-m", "compileall", "-q", "app", "scripts", "tests"],
            "compileall")
        run_test_groups()
    except (ValidationError, OSError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(f"CI_VALIDATION_FAILED: {exc}", file=sys.stderr)
        return 1
    print("CI_VALIDATION_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
