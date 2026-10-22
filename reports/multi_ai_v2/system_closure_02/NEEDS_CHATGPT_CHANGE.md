# CI changes proposed for ChatGPT review only

Patches use zero context to keep review artifacts whitespace-clean. Check/apply only against the named base with `git apply --unidiff-zero`; neither patch was applied here.

Patch base CI lane: `c8b4a252d064c8a67512a8777c5b8db9d5e54920`. Not applied to protected files.
The feature still contains the old seven-model validator; CI lane already adds HostRunConfig and DualWorkerConfig.
After integration the exact schema set is 12: brief, task, result, artifact, scene, edit_plan, review,
host_run_config, dual_worker_config, canon_bible, story_outline, segment_visuals.
New imports: ACQUISITION_OUTPUTS from app.v2_acquisition_contracts; its models reuse CanonBible and SegmentVisuals.

The seven old groups stay intact. Add acquisition=13 and closure=12; v2=111, contracts=174,
queue=50, adapters=16, mai05=108, panel=12, legacy=127. Total=623. All previous596 IDs retained.
TEST_INVENTORY.json contains exact disjoint IDs and the previous baseline. No native opt-in cases were added.
Budgets unchanged: v2/contracts420s; all other test groups180s; job30min.
Keep the existing known-skip policy; no new skips.
Artifacts now also contain GROUP.timing.jsonl and GROUP.stacks.log and external compileall bytecode/**, covered by the existing immutable file manifest.
New groups use the same bounded timing wrapper. The entire external artifact directory is already uploaded.
Telemetry contains IDs, statuses, phase times and stacks without locals; no provider payload/environment.

Per-test telemetry must be retained on failure. Independent hosted timeout cause remains UNPROVEN.
Historical run39798692447 is not reclassified by local results.

Exact proposed patch (also CI_SYNC_PROPOSAL.patch):

```diff
--- a/scripts/ci_validate_multi_ai_v2.py
+++ b/scripts/ci_validate_multi_ai_v2.py
@@ -20,0 +21 @@
+from app.v2_acquisition_contracts import ACQUISITION_OUTPUTS
@@ -308 +309 @@
-                     "$expectedTimeout", "$testsRecorded -lt 541", "$skipsRecorded -gt 3", "short-path alias limitation",
+                     "$expectedTimeout", "$testsRecorded -lt 623", "$skipsRecorded -gt 3", "short-path alias limitation",
@@ -332,0 +334 @@
+        **ACQUISITION_OUTPUTS,
--- a/.github/workflows/ci-multi-ai-v2.yml
+++ b/.github/workflows/ci-multi-ai-v2.yml
@@ -169 +169 @@
-                              'panel', 'legacy', 'compileall', 'check_git_export', 'diff_check')
+                              'panel', 'legacy', 'acquisition', 'closure', 'compileall', 'check_git_export', 'diff_check')
@@ -187,2 +187,2 @@
-          if ([int]$checks.test_count -ne $testsRecorded -or $testsRecorded -lt 541) {
-            throw "Test count mismatch or regression below 541: $testsRecorded"
+          if ([int]$checks.test_count -ne $testsRecorded -or $testsRecorded -lt 623) {
+            throw "Test count mismatch or regression below 623: $testsRecorded"
```

Protected production-core gap SG02 is separate: JobManager does not expose the exact spawned child PID/birth.
Minimal next-task API proposal is an optional trusted spawn_observer callback in JobManager.__init__,
called with (job_id, process.pid) immediately after its existing Popen. The v2 caller then persists birth
through its existing coordinator event log and still holds unknown identity. No second queue/table is needed.
Proposed core diff below is NOT APPLIED or tested; it requires a separate production-core authorization.

```diff
--- a/app/jobs.py
+++ b/app/jobs.py
@@ -37 +37,2 @@
-    def __init__(self, root: str | Path):
+    def __init__(self, root: str | Path, *, spawn_observer=None):
+        self.spawn_observer = spawn_observer
@@ -84 +85 @@
-            subprocess.Popen([sys.executable,'-m','app.worker','--workspace',str(self.root),'--job',job_id],
+            process = subprocess.Popen([sys.executable,'-m','app.worker','--workspace',str(self.root),'--job',job_id],
@@ -86,0 +88,2 @@
+        if self.spawn_observer is not None:
+            self.spawn_observer(job_id, process.pid)
```

Required regression for that separate patch: patch only app.jobs.subprocess.Popen to return a child PID;
call the actual _spawn with a host observer and assert that exact PID is received. Then exercise actual
resume pending→running→done with the observer-created birth receipt, foreign PID and unknown birth negatives.
The observer is in-process trusted host code, never a JSON import/callback. Existing helpers keep default None.
