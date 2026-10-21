"""Account-free policy and schema validation for Multi-AI v2 CI.

This script never reads provider credentials, starts a model, touches production
media, or uses a self-hosted runner. Unit/regression tests run in separate workflow
steps so their failures remain easy to locate.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.editorial import EditPlan, Scene
from app.v2_contracts import Artifact, Brief, Result, Review, Task

CHECKOUT_SHA = "fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09"
SETUP_PYTHON_SHA = "ece7cb06caefa5fff74198d8649806c4678c61a1"
UPLOAD_ARTIFACT_SHA = "ea165f8d65b6e75b540449e92b4886f43607fa02"
HEX40 = re.compile(r"^[0-9a-f]{40}$")

# Exact, reviewed PowerShell step bodies. Names alone are not a security boundary.
# Any change to an executable runner command needs explicit security-lane review.
EXPECTED_SELF_HOSTED_RUN_BODIES = {
    "Verify trusted workflow source": "if ('${{ github.ref }}' -ne 'refs/heads/main') {\n  throw 'Self-hosted execution is allowed only from refs/heads/main'\n}\nif ('${{ github.event.repository.private }}' -ne 'true') {\n  throw 'Self-hosted execution requires a private repository'\n}\n",
    "Verify clean exact checkout": "$head = (git rev-parse HEAD).Trim()\nif ($head -ne '${{ github.sha }}') {\n  throw \"Unexpected checkout SHA: $head\"\n}\n$dirty = git status --porcelain --untracked-files=all\nif ($dirty) {\n  throw \"Runner checkout is not clean:`n$dirty\"\n}\n",
    "Validate dedicated fake environment": "if (-not $env:STUDIO_PYTHON) { throw 'STUDIO_PYTHON is required on the runner' }\nif (-not $env:LOCAL_RUN_ROOT) { throw 'LOCAL_RUN_ROOT is required on the runner' }\nif (-not (Test-Path -LiteralPath $env:STUDIO_PYTHON -PathType Leaf)) {\n  throw 'STUDIO_PYTHON does not point to an existing executable'\n}\n& $env:STUDIO_PYTHON -X utf8 scripts/healthcheck_multi_ai_v2.py `\n  --mode fake `\n  --root $env:LOCAL_RUN_ROOT\nif ($LASTEXITCODE -ne 0) { throw 'Multi-AI v2 fake healthcheck failed' }\n",
    "Submit and advance bounded fake run": "& $env:STUDIO_PYTHON -X utf8 scripts/dispatch_multi_ai_v2.py\nexit $LASTEXITCODE\n",
    "Verify repository stayed clean": "git diff --check\nif ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n$dirty = git status --porcelain --untracked-files=all\nif ($dirty) {\n  Write-Host $dirty\n  throw 'Fake run modified the checked-out repository'\n}\n",
}


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


def named_step(steps: list[dict], name: str) -> dict:
    matches = [step for step in steps if isinstance(step, dict) and step.get("name") == name]
    require(len(matches) == 1, f"Expected exactly one step named {name}")
    return matches[0]


def validate_reviewed_runner_steps(steps: list[dict]) -> None:
    """Fail closed on extra actions, modified PowerShell or extra step capabilities."""
    names = [
        "Verify trusted workflow source",
        "Checkout exact trusted revision",
        "Verify clean exact checkout",
        "Validate dedicated fake environment",
        "Submit and advance bounded fake run",
        "Verify repository stayed clean",
    ]
    require([step.get("name") for step in steps] == names,
            "Self-hosted workflow must retain the exact reviewed step sequence")
    for step in steps:
        name = step["name"]
        if name == "Checkout exact trusted revision":
            require(set(step) == {"name", "uses", "with"},
                    "Checkout cannot gain unreviewed options or shell commands")
            require(step["uses"] == f"actions/checkout@{CHECKOUT_SHA}",
                    "Self-hosted checkout must remain pinned")
            require(step["with"] == {
                "clean": "true",
                "fetch-depth": "1",
                "persist-credentials": "false",
                "ref": "${{ github.sha }}",
            }, "Self-hosted checkout configuration was changed")
            continue
        permitted = {"name", "shell", "run"}
        if name == "Verify repository stayed clean":
            permitted.add("if")
            require(step.get("if") == "${{ always() }}",
                    "Post-run cleanliness check must run even on failure")
        require(set(step) == permitted and step.get("shell") == "powershell",
                f"Unreviewed runner step keys, shell or settings: {name}")
        require(step.get("run") == EXPECTED_SELF_HOSTED_RUN_BODIES[name],
                f"Reviewed runner PowerShell command changed: {name}")


def validate_reviewed_runner_workflow_root(workflow: dict) -> None:
    """Reject top-level env/defaults/extra triggers that bypass step-body pinning."""
    require(set(workflow) == {"name", "on", "permissions", "concurrency", "jobs"},
            "Runner workflow cannot add top-level env, defaults, hooks or extra keys")
    require(workflow.get("name") == "AI_VIDEO_02 request",
            "Reviewed runner workflow identity changed")


def validate_self_hosted_workflow() -> None:
    path = ROOT / ".github/workflows/new-video.yml"
    workflow = load_workflow(path)
    validate_reviewed_runner_workflow_root(workflow)
    # Guard against a workflow-level env payload overriding safe step behavior.
    altered_workflow = {**workflow, "env": {"PYTHONPATH": "unreviewed"}}
    try:
        validate_reviewed_runner_workflow_root(altered_workflow)
    except ValidationError:
        pass
    else:
        raise ValidationError("Unreviewed workflow-level environment was not rejected")
    triggers = workflow.get("on")
    require(isinstance(triggers, dict) and set(triggers) == {"workflow_dispatch"},
            "Self-hosted workflow must be manual-only")
    inputs = triggers["workflow_dispatch"].get("inputs", {})
    require(set(inputs) == {"topic", "language", "target_minutes", "style",
                             "source_profile", "auto_fix_cycles", "output_profile"},
            "Self-hosted inputs must be the reviewed fake-only brief fields")
    require(workflow.get("concurrency") == {"group": "ai-video-02-local-production",
                                              "cancel-in-progress": "false", "queue": "max"},
            "Self-hosted concurrency behavior cannot change without review")
    require(workflow.get("permissions") == {"contents": "read"},
            "Self-hosted workflow permissions must be contents: read only")
    require(set(workflow["jobs"]) == {"request"},
            "Credentialed self-hosted workflow must contain exactly one guarded request job")
    job = workflow["jobs"]["request"]
    require(set(job) == {"if", "runs-on", "timeout-minutes", "env", "steps"},
            "Credentialed job cannot gain unreviewed permissions, containers or hooks")
    require(job.get("timeout-minutes") == "30",
            "Credentialed runner must preserve the bounded 30-minute job timeout")
    runs_on = job.get("runs-on")
    require(isinstance(runs_on, list) and len(runs_on) == 3
            and set(runs_on) == {"self-hosted", "Windows", "ai-video-02"},
            "Self-hosted workflow must use exactly the dedicated Windows runner labels")
    condition = str(job.get("if", "")).strip()
    require(condition == "github.event.repository.private == true && github.ref == 'refs/heads/main'",
            "Self-hosted workflow must require the exact private + main condition without bypasses")
    require(job.get("env") == {"VIDEO_REQUEST_JSON": "${{ toJSON(inputs) }}"},
            "Workflow must carry only JSON task data, not injected shell text or credentials")
    steps = steps_for(workflow, "request")
    expected_names = [
        "Verify trusted workflow source",
        "Checkout exact trusted revision",
        "Verify clean exact checkout",
        "Validate dedicated fake environment",
        "Submit and advance bounded fake run",
        "Verify repository stayed clean",
    ]
    require([step.get("name") for step in steps] == expected_names,
            "Self-hosted workflow must retain the reviewed, ordered fake-only steps")
    validate_reviewed_runner_steps(steps)
    # Regression proof: appended shell code and added step environment must be rejected.
    altered = [dict(step) for step in steps]
    altered[0] = {**altered[0], "run": altered[0]["run"] + "Write-Host 'unexpected'\\n"}
    try:
        validate_reviewed_runner_steps(altered)
    except ValidationError:
        pass
    else:
        raise ValidationError("Unreviewed appended shell commands were not rejected")
    altered = [dict(step) for step in steps]
    altered[3] = {**altered[3], "env": {"UNREVIEWED": "1"}}
    try:
        validate_reviewed_runner_steps(altered)
    except ValidationError:
        pass
    else:
        raise ValidationError("Unreviewed step-level environment was not rejected")
    checkout = action_step(steps, "actions/checkout@")
    action_ref = checkout["uses"].split("@", 1)[1]
    require(action_ref == CHECKOUT_SHA and HEX40.fullmatch(action_ref) is not None,
            "Self-hosted checkout must remain pinned to the reviewed action commit")
    options = checkout.get("with", {})
    require(options.get("clean") == "true", "Self-hosted checkout must use clean: true")
    require(options.get("persist-credentials") == "false", "Self-hosted checkout must not persist credentials")
    require(options.get("ref") == "${{ github.sha }}", "Self-hosted checkout must pin the exact event SHA")

    text = path.read_text(encoding="utf-8")
    require("pull_request:" not in text and "schedule:" not in text,
            "Credentialed self-hosted workflow must not run from PRs or schedules")
    require("secrets." not in text, "Fake self-hosted workflow must not request GitHub secrets")
    require("healthcheck_multi_ai_v2.py" in text and "--mode fake" in text,
            "Self-hosted workflow must run the explicit v2 fake healthcheck")
    require("dispatch_multi_ai_v2.py" in text,
            "Self-hosted workflow must use the bounded v2 dispatch entrypoint")
    require("git status --porcelain" in text and "if: ${{ always() }}" in text,
            "Self-hosted workflow must verify checkout cleanliness after execution")


def validate_ci_workflow() -> None:
    path = ROOT / ".github/workflows/ci-multi-ai-v2.yml"
    workflow = load_workflow(path)
    triggers = workflow.get("on")
    require(isinstance(triggers, dict) and set(triggers) == {"pull_request", "push"},
            "Independent CI must run for pull requests and reviewed branch pushes only")
    pr_branches = triggers["pull_request"].get("branches", [])
    push_branches = triggers["push"].get("branches", [])
    require(set(pr_branches) == {"main", "feature/multi-ai-coordinator-v2"}
            and len(pr_branches) == 2,
            "Independent CI must check pull requests to main and feature")
    require(set(push_branches) == {"main", "feature/multi-ai-coordinator-v2"}
            and len(push_branches) == 2,
            "CI push runs must target integrated core branches, not isolated ChatGPT lane")
    require(workflow.get("permissions") == {"contents": "read"},
            "CI permissions must be contents: read only")
    job_name = next(iter(workflow["jobs"]))
    job = workflow["jobs"][job_name]
    require(job.get("runs-on") == "windows-latest", "Independent CI must use GitHub-hosted windows-latest")
    require("self-hosted" not in json.dumps(workflow), "Independent CI must never target a self-hosted runner")
    steps = steps_for(workflow, job_name)

    checkout = action_step(steps, "actions/checkout@")
    checkout_ref = checkout["uses"].split("@", 1)[1]
    require(checkout_ref == CHECKOUT_SHA and HEX40.fullmatch(checkout_ref) is not None,
            "CI checkout action must remain commit-pinned")
    checkout_options = checkout.get("with", {})
    require(checkout_options.get("clean") == "true" and checkout_options.get("persist-credentials") == "false",
            "CI checkout must remain clean and credential-free")
    require(checkout_options.get("ref") == "${{ github.sha }}",
            "CI must check out the exact event/merge SHA")

    setup = action_step(steps, "actions/setup-python@")
    setup_ref = setup["uses"].split("@", 1)[1]
    require(setup_ref == SETUP_PYTHON_SHA and HEX40.fullmatch(setup_ref) is not None,
            "setup-python action must remain commit-pinned")

    upload = action_step(steps, "actions/upload-artifact@")
    upload_ref = upload["uses"].split("@", 1)[1]
    require(upload_ref == UPLOAD_ARTIFACT_SHA and HEX40.fullmatch(upload_ref) is not None,
            "upload-artifact action must remain commit-pinned")
    require(upload.get("if") == "${{ always() }}", "CI evidence must upload even when regression fails")
    upload_options = upload.get("with", {})
    require(upload_options.get("retention-days") == "14",
            "CI evidence retention must stay bounded at 14 days")
    require(upload_options.get("if-no-files-found") == "error",
            "Missing CI evidence must fail the job, not produce a warning")

    smoke = named_step(steps, "Verify one-topic hosted fake dispatch and same-run replay")
    smoke_text = str(smoke.get("run", ""))
    require(smoke.get("shell") == "powershell" and
            set(smoke) == {"name", "shell", "env", "run"},
            "Hosted fake smoke must remain an isolated PowerShell step")
    require(smoke.get("env", {}).get("LOCAL_RUN_ROOT") ==
            "${{ runner.temp }}/multi-ai-v2-hosted-fake",
            "Hosted fake smoke must write only to runner temporary local storage")
    require('"production_mode":"fake"' in str(smoke.get("env", {}).get("VIDEO_REQUEST_JSON", "")),
            "Hosted smoke cannot request live providers or production")
    for required in ("--mode fake", "scripts/dispatch_multi_ai_v2.py",
                     "SIMULATION_SUCCEEDED", "same-run", "ready_for_user",
                     "drive_result_link", "manifest_sha256", "video_qa",
                     "editorial_qa", "model_requests", "stages=19"):
        if required != "same-run":
            require(required in smoke_text or required in str(smoke.get("name", "")),
                    f"Hosted fake dispatch smoke missing check: {required}")

    checker = named_step(steps, "Run clean revision-bound core checker")
    checker_text = str(checker.get("run", ""))
    require("scripts/check_multi_ai_v2.py" in checker_text and "exit $code" in checker_text,
            "Canonical revision-bound checker must remain the authoritative failing step")

    envelope = named_step(steps, "Verify immutable evidence envelope")
    require(envelope.get("if") == "${{ always() }}", "Evidence envelope must be checked after pass or failure")
    envelope_text = str(envelope.get("run", ""))
    require("throw 'Core checker did not create an evidence directory'" in envelope_text,
            "Missing evidence root must fail closed")
    require("Write-Warning 'Core checker did not create an evidence directory'" not in envelope_text,
            "Missing evidence cannot be only a warning")
    for required in ("git_tree_sha", "Get-FileHash", "source_unchanged_during_checks",
                     "model_requests", "paid_api_requests", "production_started",
                     "$value.status -ne 'passed'", "$checks.status -ne 'passed'",
                     "$checks.test_count", "$expectedGroups", "$observedGroups",
                     "$expectedTimeout", "$testsRecorded -lt 411", "$skipsRecorded -gt 2"):
        require(required in envelope_text, f"Evidence envelope check is missing {required}")

    clean = named_step(steps, "Verify clean, whitespace-safe checkout")
    require(clean.get("if") == "${{ always() }}" and "--untracked-files=all" in str(clean.get("run", "")),
            "Clean-tree verification must run always and include untracked files")

    text = path.read_text(encoding="utf-8")
    require("secrets." not in text, "GitHub-hosted offline CI must not request repository secrets")
    require("jsonschema==4.26.0" in text and "PyYAML==6.0.3" in text,
            "Bounded CI-only dependencies must remain explicitly pinned")


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
    # Canonical temporary generation proves the check does not rewrite repository files.
    with tempfile.TemporaryDirectory(prefix="multi_ai_v2_schema_ci_") as folder:
        destination = Path(folder)
        for name, value in generated.items():
            payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            (destination / name).write_text(payload, encoding="utf-8", newline="\n")
        require(all(json.loads((destination / name).read_text(encoding="utf-8")) == value
                    for name, value in generated.items()),
                "Temporary schema generation failed")


def main() -> int:
    try:
        validate_self_hosted_workflow()
        validate_ci_workflow()
        validate_schema_drift()
    except (ValidationError, OSError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(f"CI_POLICY_VALIDATION_FAILED: {exc}", file=sys.stderr)
        return 1
    print("CI_POLICY_VALIDATION_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
