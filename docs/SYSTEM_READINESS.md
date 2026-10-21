# SYSTEM-01 readiness

`system_ready_for_autonomous_episode=false`.
Разделять IMPLEMENTED, OFFLINE_TESTED, LIVE_VERIFIED и OWNER_APPROVED.
Наличие Python-кода, приложения/тарифа или unit mock не повышает live статус.

| Capability | Implemented | Offline tested | Live verified in SYSTEM-01 | Owner approved live execution |
|---|---|---|---|---|
| Source allowlist/canon/story/Scenes | yes, explicit approved input | yes | no new research | no |
| General Coordinator plan/permit/gate | yes, per-input dependencies | 1/2/5 scenes, two roots, replay | no new episode | no |
| Story / Visual provider | capability wrapper, no supported Antigravity integration | injected transport | BLOCKED AGE-GATED / DISABLED | no |
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

No protected core change was needed. No architect approval is claimed. A hard
limit remains: synchronous engine execution is bounded but cannot be immediately
interrupted by a second CLI while Coordinator owns its OS lock; use existing job
inspection/recovery. Full live end-to-end system behavior remains unverified.
