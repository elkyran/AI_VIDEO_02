# CLOSURE-02-REVIEW-FIX: CI owner handoff

Apply CI_SYNC_PROPOSAL.patch only in the ChatGPT lane after review. It is based on
assistant/providers-01-ci-sync ec9ee23fb08daa9076604888ac2e28a4b384f356.
No protected file was edited here; PR9/PR6 were not merged.

The canonical checker retains all nine old groups and their exact 623 test IDs.
It adds five DISJOINT bounded groups: review_recovery (11), review_story (1),
review_visual (1), review_source_revision (1), review_crash (5). Total642. Story/Visual measured together
154.204s locally before final evidence assertions, so each has a separate180s
shard; the crash matrix measured233.110s under concurrent focused load, so source/edition revision has its own180s shard as well. No timeout was increased. Existing v2/contracts420s, all others180s,
job30min remain. Hosted timing must verify aggregate/headroom on the new exact SHA.

Schema names/models remain the existing twelve. HostRunConfig now includes
optional acquisition.scoped_fix: ScopedFixPermit (story|visual, existing scene_id,
segment_id, max_cycles literal1). Existing **ACQUISITION_OUTPUTS and drift checker
on the current lane already derive this nested schema; no new registry name,
unknown-field relaxation or schema whitelist expansion is needed.

Independent run39831668255 failed, not timed out. Its checkout eb3ef726afac9079fe346120ee9b082905e5821e
is confirmed in the job log; closure reported3 failures/12 errors. Concrete causes:
unresolved Windows temp path in GenericRecovery/GenericQA fixtures; NativeIO
prepare_request used missing private production-profile proof files in a clean
checkout. New fixtures resolve roots and generate disposable provenance proofs
while calling the ACTUAL load_profile validator with unchanged voice/video/settings.
No native command or private studio file is used. Old timeout root cause is not
inferred from last recorded test. A new exact-SHA hosted CI is still REQUIRED;
this local package does not declare it green or trigger self-hosted execution.
