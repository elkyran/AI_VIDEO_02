# SYSTEM-CLOSURE-02 — requested CI-lane synchronization

Implementation base: `16edb1d4f05d981ca35bac179d072f683a779168`.
Do not apply these changes to the protected lane from Codex.

Expected additional canonical outputs: existing `CanonBible`, existing
`SegmentVisuals`, and one new bounded `StoryOutline`. Their schema names,
imports and exact proposed validator patch will be frozen with the implementation.
Keep the nine existing schema names and canonical Scene/Review/EditPlan meanings.

The checker will retain v2/contracts=420s, other groups=180s. Per-test timing,
unfinished-test records and stack snapshots belong to external evidence.
All previous 596 exact test IDs are captured in external
`evidence/baseline_test_ids.json`; final additions/retention and any measured
sharding proposal will be listed here. No workflow/security file is edited.

Live providers, production, runner, auth and real Drive remain disabled.
