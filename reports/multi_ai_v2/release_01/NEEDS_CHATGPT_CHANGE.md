# Independent release gate requests

Do not apply these changes in the Codex lane. PR9 remains separate and unmerged.

1. Review/integrate `assistant/providers-01-ci-sync` at the independently checked
   revision. Expected snapshot `c6d48c858b72cd56586858ad0ed2ff4b99981165`; this package
   does not merge, cherry-pick or reproduce its protected two-file patch.
2. Retain nine generated schemas (the original seven, HostRunConfig,
   DualWorkerConfig), exact drift/strict checks, v2/contracts=420s and others=180s.
   RELEASE-01 changes only the nested HostRunConfig definitions: optional host
   WorkerRoutes and BriefInputs. Schema membership remains nine; exporter is
   still `scripts/export_multi_ai_v2_schemas.py`. No extra standalone schema is
   needed. The existing fake19 smoke CLI remains unchanged.
3. Run hosted Windows CI on the exact reviewed integration SHA. Require all
   pre-existing 541 tests plus RELEASE-01 additions, preserve the old Piper skip,
   and retain nonzero process exit as failure. The final count/source binding
   belongs to REPORT/STATUS; local test success is not independent CI evidence.
4. Previous failed run 39656666964 is preserved as failed evidence. Its orphan
   stdout child cleanup regression is fixed in this branch; verify job-tree,
   reader/pipe/native handle release on hosted Windows. Do not hide the failure
   or replace lifetime assertions with cleanup retries/skips.
5. `scripts/check_multi_ai_v2.py` now parses skipped counts from both OK and
   FAILED summaries. Do not reinterpret failed+skipped as success or rewrite old
   checks.json. Preserve old hashes and compare fresh evidence independently.

Protected files were not edited. PR9→feature and PR6→main integration/merge remain
the separate owner/ChatGPT release gate.
