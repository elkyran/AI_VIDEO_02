# SYSTEM-01 / PROVIDERS-01 readiness

`system_ready_for_autonomous_episode=false`.
Разделять IMPLEMENTED, OFFLINE_TESTED, LIVE_VERIFIED и OWNER_APPROVED.
Наличие Python-кода, приложения/тарифа или unit mock не повышает live статус.

| Capability | Implemented | Offline tested | Live verified in SYSTEM-01 | Owner approved live execution |
|---|---|---|---|---|
| Source allowlist/canon/story/Scenes | yes, explicit approved input | yes | no new research | no |
| General Coordinator plan/permit/gate | yes, per-input dependencies | 1/2/5 scenes, two roots, replay | no new episode | no |
| Antigravity Story account A / Visual account B | two fixed offline slots, bounded parser, host sessions/receipts | data-only synthetic Story → Review → one revision, queue/gate/replay | BLOCKED_ELIGIBILITY / AGE-GATED / DISABLED; connected=0 | no |
| Codex director | existing adapter boundary, no role substitution | injected transport | end-to-end unverified | no |
| Voice/import | existing JobManager + strict completed WAV receipt | negative gates; prior verification tests retained | no new TTS; PILOT-01 historical proof only | no |
| Measured edit / MLT / render / QA | handlers over existing engine, explicit renderer | fixture wiring and existing regression; byte-preservation copy check | no new render/decode | no |
| Review / targeted fix | canonical owner review, versioned config, unchanged dependency reuse | exact identities / replay | no creative review performed | no |
| Portable package | preserved relative MLT layout, exact inventory | synthetic negatives and copied PILOT bytes | no new production output | no |
| Drive | official REST resumable transport + same queue stage | injected errors/chunks/checksums/replay | IMPLEMENTED_NOT_LIVE_TESTED | no |
| Windows runner | headless CLI/exit contract | local CLI | not registered/verified remotely | no |

Remaining gates, in execution order (one owner action list):

1. Independent review; ChatGPT lane synchronizes evidence validator timeout policy:
   v2/contracts=420s, other groups=180s. Resolve known immutable evidence CI failure
   from 39608860993, obtain green reviewed CI, then **owner-controlled merge** of
   PR6 when appropriate. Codex does not merge or alter CI/security files.
   Additionally, `scripts/ci_validate_multi_ai_v2.py:319` must import HostRunConfig
   and add `host_run_config` to generated_schemas, retaining all seven previous
   canonical contracts and exact drift checks. Current policy expects seven files,
   SYSTEM-01 exported eight. PROVIDERS-01 additionally exports
   `dual_worker_config` from `app.v2_provider_protocol.DualWorkerConfig`, bringing
   the exact set to nine. Add this model too; preserve all prior strict checks.
   This is a concrete NEEDS_CHATGPT_CHANGE, not green CI.
   Local standalone validator stopped on missing PyYAML (`import yaml`); no install
   was attempted. Hosted CI must retain its pinned validator dependencies.
2. Select isolated local input/output storage with explicit budgets/free reserve
   and full approved runtime paths/settings. Keep active studio and original
   PILOT immutable. Larger meaningful production needs its own disk/time budget.
3. Separately authorize isolated Windows runner registration/checkout/probes and
   only the permitted workflow. No remote execution readiness is claimed here.
4. Resolve official age/access/capability gates for future Antigravity Story/Visual
   and director route. No installation, login, new account, paid API, account
   rotation or fallback is authorized. Autonomous mode remains blocked meanwhile.
5. Select owner Drive folder and approve existing official authorization, secure
   host credential_ref resolver/session persistence; inject verified transport and
   perform a separately authorized bounded cloud verification. CLI WAITING_AUTH
   remains until that host binding exists.
6. Separately authorize one meaningful approved-input episode only after functional
   verification and blockers are resolved/explicitly scoped. Review that episode
   before any quality-backlog work.

NEEDS_ARCHITECT_REVIEW before live account wiring: the existing queue selects one
profile per provider, forbids creative requested_profile_id, reserves one cloud
attempt per provider and marks the selected profile waiting_reset on quota.
Review a future host-owned `(role, worker_id) -> account binding` route in
Coordinator.enqueue/tick/_dispatch/_failure, plus request/Result account-binding
validation in V2ResultGate. Retain generation/CAS/dependency checks and rotation=false;
do not encode a second Antigravity account as Codex. No such core edits or live
activation are included. Synthetic task-level access pauses are described in
[DUAL_PROVIDER_INTEGRATION](DUAL_PROVIDER_INTEGRATION.md).

No protected core change was made. No architect approval is claimed. A hard
limit remains: synchronous engine execution is bounded but cannot be immediately
interrupted by a second CLI while Coordinator owns its OS lock; use existing job
inspection/recovery. Full live end-to-end system behavior remains unverified.
