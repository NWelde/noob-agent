# Changelog

## Unreleased

2026-09-25 | [CLEANED] | Narrowed the README and reproducibility/status docs to the active Minecraft redstone computer work. Removed tests for retired demo commands and corrected server setup coverage to check only actual data-pack directories. The old demo failures no longer define the current test baseline.

2026-09-24 | [FIXED] | Provider-mode Jev calls now start at least four seconds apart after repeated HTTP 429s; a paced V4-Pro trial reached 135 charged Jev calls without rate limiting but repeated already placed wires and produced no public grade. The loop now rejects identical verified re-placements. After interrupting that unproductive trial, its final reset failed on a dead sidecar pipe; a separate trusted two-reset check verified the original baseline. Final-reset cleanup now reconnects once and repeats full verification after an error, with both outcomes recorded. Milestone 4 remains incomplete.

2026-09-24 | [FIXED] | A bounded DeepSeek V4-Pro-0813 planner call proposed levers and wiring. Its first connected trial stopped when a 12-offer build intention declared `max_actions=16`; the loop now rejects caps too small for offers and readbacks and returns unfinished grading handoffs to compact construction. A second connected V4-Pro trial made 38 build attempts, then Jev returned HTTP 429. Both trials reset to the original baseline; no public module grade ran.

2026-09-24 | [FIXED] | Provider construction feedback now reports verified stone and signal/control counts. Placement intentions must include a signal or control component until one is verified; static redstone power alone is insufficient. A DeepSeek planner-only diagnostic returned unparseable JSON. A Qwen planner-only comparison returned a valid but all-stone plan; automatic review rejected a second Qwen request with synthetic feedback before dispatch. A connected DeepSeek/Jev trial placed 38 blocks, rejected an invalid lever facing, then stopped on Jev HTTP 429; both resets verified and no module grade ran. The focused redstone suite passed 235 runnable tests. Milestone 4 remains incomplete.

2026-09-24 | [VERIFIED] | Milestone 4 construction now requests the full declaration schema only after an explicit grading handoff. Trusted resets disable mob griefing and spawning after sheep grazing changed two protected grass cells during an idle hold; a guarded 65-second hold and both resets verified the original baseline hash. Fresh DeepSeek/Jev trials made 12 and 45 build actions, then stopped on Jev gateway HTTP 500 and 429 before a module grade; all resets verified. No module passed.

2026-09-24 | [OPTIMIZED] | The first provider planner call now uses a compact eight-action construction schema, and build intentions may omit module inspection until grading. A planner-only call returned a valid eight-action intention; a connected trial made 31 build actions, then an aliased declaration and later planner timeout prevented grading. Both resets verified. Public grades still require complete recipes; no module passed.

2026-09-24 | [VERIFIED] | Two fresh DeepSeek/Jev trials after feedback compaction timed out on their first structured planner call with no world actions; both final resets verified. A tiny DeepSeek diagnostic succeeded, while full-prompt plain-text and JSON-object diagnostics returned invalid or incomplete JSON. Strict parsing remains required; milestone 4 remains unverified.

2026-09-24 | [VERIFIED] | After user authorization, bounded Qwen planner calls returned an HTTP 400, an output-cap reply with no visible content, and JSON with invalid probe fields. A connected DeepSeek/Jev trial reached 45 build actions and register failures, then exhausted 24 repair rounds; no module passed. Its late final reset was unverified, but a separate trusted two-reset check restored the baseline. Planner context now retains six recent feedback entries and asks for physical repair before repeated grading; local tests pass, live verification of this prompt change is pending.

2026-09-24 | [OPTIMIZED] | Trusted Minecraft timelines now sprint the same scheduled game ticks, verify exact server tick gaps and control effects, and stop uncertain sprints during cleanup/recovery. A provider-free live 408/800/1000/1600-tick proof passed in about 1.2–1.4 wall seconds per timeline; a sprint-enabled negative register proof still failed on observed A=8 versus expected A=0 and cleaned all seven fixture cells. The focused suite passes 167 tests. The advisory four-module forecast is about 985 seconds including a 240-second program reserve. No model-built module passed at this checkpoint.

2026-09-24 | [ADDED] | Provider trials now track public register/arithmetic/storage/output passes on one construction epoch, invalidate earlier passes on later build attempts, and stop after all four pass. Milestone 4 is marked passed only after verified final reset; full-machine model success remains separate. Offline tests pass, live four-module acceptance is pending.

2026-09-24 | [FIXED] | Planner schema failures now return a bounded safe field path and Pydantic error code for repair without echoing untrusted input. Focused offline tests pass; no new provider call or module pass.

2026-09-24 | [VERIFIED] | Trusted mixed STEP/sample timelines now combine register load/hold/reset and output load/OUT/persist plus load/ADD/OUT/reset without reducing required game ticks. Compact suite forecast is 55,616 ticks in 63 timelines. Provider-free dedicated-server proof recorded exact tick-2 pulse and tick-200/600/1000 settled snapshots, verified controls and 60 fixture removals; no model module passed.

2026-09-24 | [OPTIMIZED] | Milestone 4 register hold and reset now share one sample-only server timeline with two raw checkpoints per value. Required waits and checks stay intact; compact fixture estimate falls from 127 to 111 timelines at the same 55,616 ticks. Focused tests pass; positive circuit evidence remains pending.

2026-09-24 | [FIXED] | Provider intentions now validate all recipes required by the declared module before Jev dispatch and give module-specific missing-recipe feedback. A later live attempt stopped on a planner timeout after negative control evidence; both resets verified. No module pass.

2026-09-24 | [FIXED] | Provider response schemas now require a module declaration and limit normal intentions to 16 actions with concise summary/criteria fields; the eight-action truncation repair remains. Local tests pass. A separate Qwen planner comparison was rejected by automatic approval review before any request was sent at this checkpoint; later user authorization and call results are recorded above.

2026-09-24 | [VERIFIED] | A live model-built register candidate passed reset/LOAD 0/hold then failed LOAD 1 with observed A=0 versus expected A=1; the planner received that result. Twelve block placements and both baseline resets verified. The Jev HTTP 503 retry worked once, but a later pair of 503s stopped the trial. The charged retry allowance is now three per selection with offline tests; no module passed.

2026-09-24 | [FIXED] | Milestone 4 planner bounds feedback now identifies the invalid action and coordinates; truncated replies receive compact repair guidance. Completed registry validation failures return to the planner without tearing down the sidecar. Live trials reached eleven verified placements and a negative register control check, then Jev HTTP 503; two verified resets restored the world. A subsequent change allows one charged and journaled HTTP 503 retry per Jev selection; live retry verification is pending. No module passed or milestone acceptance.

2026-09-24 | [FIXED] | A later planner timeout after output-cap truncation led to a compact repair response schema with eight actions, shorter summary and criteria. Both trial resets verified; no Jev call or module result from that run. A subsequent live trial exercised the schema.

2026-09-24 | [FIXED] | The compact repair schema produced a complete eight-action live reply, but a declared interface coordinate failed validation. Probe/control bounds feedback now identifies the precise declaration and coordinates. Focused tests and static checks pass; positive module evidence is still pending.

2026-09-24 | [FIXED] | Milestone 4 grading now returns completed missing-probe and mismatched-control evidence as failed public checks for planner repair, while standalone timelines still reject missing probes and uncertain transport/cleanup failures stop the trial. No provider or positive model-designed module evidence is included.

2026-09-24 | [FIXED] | Milestone 4 provider intentions now cap Jev offers at 32, require behavioral module recipes before dispatch, allow up to two strict validation repairs, and include exact build coordinates in bounds feedback. Live calls showed a 70 KB Jev payload rejected at 256 offers, missing recipes, out-of-bounds placements, and a later 60-second planner timeout. No new world actions, module pass or acceptance resulted. Syntax and diff checks passed; tests were not run.

2026-09-22 | [FIXED] | Milestone 4 planner-facing grading stops at the first observed behavioral mismatch and returns saved incomplete evidence for repair. Added strictly validated parameter-bit recipe templates for compact value/address declarations; explicit recipes and exhaustive diagnostic grading remain available. Scheduling optimization and legacy cleanup remain pending in this bounded continuation.

2026-09-22 | [ADDED] | Milestone 4 public register/arithmetic/storage/output behavioral checks with finite value-addressed level/wait recipes, raw observed snapshots compared in Python, shared module allowances and separate program deadlines, sample-only hold/reset timelines, and planner repair feedback. Controlled reader tests and live negative evidence are distinct from deferred model-designed circuit evidence. Focused verification: 122 Python tests and 10 Node tests pass; live reset correctly fails with observed A=8, expected 0, and seven verified fixture removals. Legacy scheduling cleanup and full hardware identity remain pending.

2026-09-22 | [ADDED] | Milestone 4 bounded server timeline: strict level/wait recipes, chained 200-tick cycles, two-tick STEP and same-tick observed snapshots, aggregate generated-command/tick accounting, and journaled cleanup/recovery. Behavioral graders and model success remain pending.

2026-09-22 | [ADDED] | Milestone 4 trusted grader control foundation: declared server-side probes and lever levels, two-server-tick scheduled STEP with game-time evidence, shared action guards and bounded grading tick accounting, sanitized uncertain failures, and dedicated-world control proof. Harness scheduling templates contain only control/timing operations. Programming recipes and four behavioral graders remain pending; no model success or acceptance.

2026-09-22 | [ADDED] | Milestone 4 bounded interface foundation: strict planner-selected module probes/control declarations, charged read-only world inspection with detailed failures, block-specific structural identity separate from signal state, canonical --inspect-module mode, and provider-capable planner inspection feedback that continues after a checkpoint. Behavioral graders and trusted tick/control operations remain pending; inspection cannot establish module success. Tests-first and live evidence are recorded in docs/redstone-trials.md.

2026-09-22 | [VERIFIED] | Retained milestone-3 fixture-driven live missing-dust failure and feedback-authorized repair under one ID, direct 15/true then 0/false dust/lamp readbacks, and two verified full-region resets; 4 planner/5 Jev fixture calls, 1 repair, 35 charged operations. Added fixture feedback/isolation regression. Focused checks: 128 Python and 12 Node tests, Ruff/format/mypy; README and trial docs retain exact artifacts and pending real-provider/full-machine/recording evidence. model_success remains false.


2026-09-22 | [FIXED] | Added a failing connected-run cleanup regression and preserve the original loop failure stage while attempting final restoration; explicit provider wiring/fresh adapter tests use no network and prove missing credentials never trigger fixture fallback.


2026-09-22 | [ADDED] | Milestone 3 canonical explicit fixture/provider trial modes, immutable credential-free provider configuration, fresh W&B/shared-handler adapters and verified-reset orchestration. Added failing command-selection tests before implementation and a clearly labeled feedback-driven missing-dust fixture using the bounded Jev subprocess protocol. Live and focused verification results follow in trial documentation; no model success or milestone acceptance.


2026-09-22 | [ADDED] | Milestone 3 adapter boundary: strict bounded planner intentions and fresh public context; reuse shared Jev handler through a deadline/output-bounded subprocess with exact offered-ID validation and preserved usage/model identity; reuse W&B ModelClient completion with zero SDK retries and explicit request timeout. Offline subprocess/provider tests retained; trial-loop accounting, canonical mode and live repair proof remain pending.

2026-09-22 | [DOCUMENTED] | Retained two live full-region reset comparisons and passing normal lever/dust/lamp off/on/off with 33 charged operations; retained the earlier failed smoke. Documented 77 passing Python tests, 8 Node tests, Ruff/format/mypy checks, exact evidence paths, finite snapshot scope and remaining recording/review work. No model success or milestone acceptance claimed.

2026-09-22 | [FIXED] | Added a failing persistence regression and now durably mark the action runtime stopped immediately on uncertain transport outcomes, before any attempted subsequent action.

2026-09-22 | [FIXED] | Retained a real failed smoke where Mineflayer reported dust power as a string; added a failing regression then normalized integer block states through the 1.21.1 registry for consistent validation and effect readbacks. Tightened tick-rate matching and runtime target typing.

2026-09-22 | [ADDED] | Added tests-first charged single-target placement/removal/lever use, whitelist and registry property validation, action/time limits, unknown-outcome stopping, and actual effect readbacks. Added --smoke for independent dust/lamp off/on/off observations between complete hashed resets; infrastructure only, recording remains missing.

2026-09-22 | [ADDED] | Added tests-first trusted hashed baseline restoration, full 387072-cell state hashing, exact player/inventory checks, effective settings probes, and canonical --reset-check for two restores with an intervening dirty block. Added trusted sidecar settling and baseline scan; planner actions remain separate and pending. Live verification and checks are being retained under development-m2-reset.

2026-09-22 | [ADDED] | Added a Vercel AI Gateway Jev evaluation handler using the local AI_GATEWAY_API_KEY, pinned AI SDK, reusable state/questions interface, JSON stdin command and billing example. Calls have a deadline and no automatic retries. Added offline handler tests and docs/jev-handler.md; Minecraft trial-loop integration remains separate.

2026-09-22 | [DOCUMENTED] | Retained milestone-2 sidecar red/green evidence (67 Python tests, 6 Node tests, Ruff/format and mypy), actual dedicated-server attachment/readbacks and the next reset/runtime handoff. Live player position and empty inventory differ from the frozen initial conditions; the unique manifest remains incomplete and no live lever smoke or reset is claimed.

2026-09-22 | [ADDED] | Added a separate fixed-endpoint redstone Mineflayer sidecar with actual player/block readbacks, registry property validation and bounded normal lever interaction. Added a deadline-bound Python JSONL client with pre-delivery journaling, sanitized durable failures and no retries after uncertainty, plus canonical --observe evidence mode. Tests preceded implementation. Reset, action budgets, full construction and module smoke remain pending; no trial or model success claimed.

2026-09-22 | [DOCUMENTED] | Recorded milestone-2 transport/preflight red/green checks (58 focused tests, Ruff and mypy), two live read-only dedicated-server manifests and the remaining foundation handoff in docs/redstone-trials.md. Added the canonical incomplete-preflight command to README. No full reset, bot attachment, module smoke, recording or milestone acceptance is claimed.

2026-09-22 | [FIXED] | Redstone preflight preserves the interruption stage and exception type before propagating cancellation; the pending probe stays unknown. Added a behavioral interruption regression before the fix.

2026-09-22 | [FIXED] | Redstone preflight now records sanitized cleanup failures after a lost probe, preserving its unknown outcome and durable incomplete status; added a failing-then-passing regression. Unique manifest directory entries are fsynced before recording startup evidence.

2026-09-22 | [ADDED] | Added unique, atomic, fsynced redstone manifests and a read-only dedicated-server preflight command. Attempts are persisted as unknown before transport delivery; startup failures, absent recording and pending reset/player/module evidence remain explicitly incomplete. This bounded milestone-2 chunk does not implement or claim a complete trial.

2026-09-22 | [ADDED] | Began milestone 2 with a separate dedicated-server RCON adapter: bounded packet/deadline handling, multipart response barriers, local-only endpoint checks, sanitized authentication errors and explicit unknown outcomes without retries. Wire-level behavioral tests precede implementation. This is transport infrastructure; verified template reset and live module grading remain pending.

2026-09-22 | [FIXED] | Reject numeric type drift in the frozen redstone JSON artifact, including float-valued integer literals. Added regression coverage for literal widths/capacity, nested numeric fields, booleans and harmless JSON reformatting; retained the existing failing behavioral test. Software-only verification is recorded in the contract documentation; live checks remain pending.

2026-09-22 | [ADDED] | Implemented the frozen v1 redstone behavioral contract, strict scenario loader, bounded construction validation, software-only reference traces, and behavioral tests. Documented live acceptance requirements and pending adapters; no Minecraft success claimed.

2026-09-22 19:28 PDT | [DOCUMENTED] | Saved the installed-CLI controller verification after interrupted-change recovery: 82/82 tests passed with no skips.

2026-09-22 19:28 PDT | [FIXED] | Preserved interrupted worker edits through usage continuation and provisional retirement for full milestone review; baseline-free interruptions now block acceptance.

2026-09-22 19:28 PDT | [FIXED] | Treated worker file assignments as advisory scope so unassigned edits are recorded and reviewed without pausing a full-permission worker.

2026-09-22 19:22 PDT | [FIXED] | Ran coding workers directly with host shell and filesystem permissions while keeping coordinator and reviewer calls read-only; verified real CLI shell commands, assigned and unassigned edits, and usage recovery.

2026-09-22 19:22 PDT | [DOCUMENTED] | Updated the milestone-0 plan and controller documentation for trusted full-permission workers and the resulting review scope.

2026-09-22 17:04 PDT | [DOCUMENTED] | Saved the post-review installed-CLI controller verification: 85/85 tests passed, with nested-shell execution still open.

2026-09-22 17:03 PDT | [FIXED] | Charged coordinator dispatch and interrupted recovery to the active milestone after milestone 0, with a regression test.

2026-09-22 17:03 PDT | [DOCUMENTED] | Recorded the full-scope reviewer findings and the failed external-sandbox CLI probe; nested-shell execution remains unverified.

2026-09-22 16:54 PDT | [DOCUMENTED] | Saved the full-scope milestone-0 installed-CLI verification at `.noob-agent/astra-controller/verification/milestone-0-full-review-20260922T1653.tap`: 84/84 tests passed with no skips.

2026-09-22 16:53 PDT | [FIXED] | Expanded milestone-0 independent review to the controller implementation, tests, and plan after a documentation-only final worker; added an audited invalidation path for the prior limited review.

2026-09-22 16:48 PDT | [DOCUMENTED] | Saved a fresh installed-CLI controller test run at `.noob-agent/astra-controller/verification/milestone-0-controller-20260922T1647.tap`: 82/82 passing with no skips.

2026-09-22 16:47 PDT | [ADDED] | Added an audited operator transfer command for an idle coordinator whose milestone pool cannot cover the next dispatch estimate; it enforces the existing future-pool gates.

2026-09-22 16:46 PDT | [DOCUMENTED] | Verified the milestone-0 controller suite with the installed CLI at 81/81 passing and corrected README evidence to distinguish native patch confinement from failed nested-shell execution. The temporary test output was not retained; a fresh durable verification run is needed before review.

2026-09-22 16:43 PDT | [DOCUMENTED] | Clarified milestone-0 acceptance under the installed CLI's unverified internal model-call cap: bounded fresh calls, estimated context handoffs, controller tests, independent review, and explicit disclosure of the limit.

2026-09-22 16:42 PDT | [FIXED] | Allowed provisional interrupted-worker accounting to transfer unused credits from a future milestone when the reserve is exhausted, with an exact run ID and recorded transfer.

2026-09-22 16:40 PDT | [ADDED] | Added an opt-in local Responses fixture that drives the installed CLI through confined owned and denied unowned writes, emits usage, and verifies exactly-once interrupted recovery; the full controller suite passed 80/80 with the real CLI checks enabled.

2026-09-22 16:40 PDT | [DOCUMENTED] | Clarified that the installed CLI has no verified pre-request model-call cap and that the 50% context handoff remains an estimate.

2026-09-22 16:31 PDT | [ADDED] | Added opt-in installed-CLI offline startup-failure/recovery coverage using temporary state and a network namespace. Codex 0.155.1 rejected an unknown provider; confirmed exit, pending-state preservation, unchanged spending, and redispatch blocking passed. Full controller suite: 79/79. Successful CLI tool execution and usage-bearing recovery remain unverified.

2026-09-22 16:31 PDT | [DOCUMENTED] | Recorded capability inspection, the unsupported model-turn limit, exact offline CLI evidence, and a precise milestone-0 handoff. Default dispatch still has no internal model-turn bound; runtime/output limits are not a substitute.

2026-09-22 16:31 PDT | [FIXED] | Reject explicitly requested `codexRunner` model-turn limits before launch instead of silently ignoring them. This is a fail-closed API guard; default dispatch remains unbounded in model turns.

2026-09-22 16:25 PDT | [DOCUMENTED] | Recorded the usage-only continuation checks and remaining milestone-0 gaps in README.md.

2026-09-22 16:25 PDT | [FIXED] | Added `continue-usage RUN_ID` for recovered usage-only interrupted workers after confirmed exit and matching durable usage receipts; preserves artifacts and spending, invalidates prior results/review, and retains evidence/budget blocking. The controller suite passed 77/77 using fake fixtures.

2026-09-22 16:18 PDT | [FIXED] | Added provisional retirement for an interrupted worker with no usage receipt, reserving credits from the milestone reserve while retaining the prior actual billing checkpoint.

2026-09-22 16:15 PDT | [FIXED] | Added manual retirement for a legacy interrupted worker after observed exit and total billing reconciliation; preserved artifacts and invalidated stale review evidence.

2026-09-22 16:12 PDT | [FIXED] | Stopped running agent calls when their response file exceeds the read ceiling and preserved a bounded partial copy; added a fake-process regression and documented the polling limit.

2026-09-22 16:08 PDT | [FIXED] | Added conservative stale-lock recovery with process identity, serialized lock reclamation, worker exit receipts, and atomic interrupted-usage accounting that retains pending dispatch protection. Verification and remaining recovery limits are recorded in README.md.

2026-09-22 16:01 PDT | [FIXED] | Made worker call estimates informational while keeping milestone and global budget gates, and updated the hackathon plan and controller documentation to match.

2026-09-22 16:01 PDT | [FIXED] | Added a 1-MiB response read ceiling and 8-MiB combined stdout/stderr ceiling, preserving capped failure artifacts and pending dispatch protection; verified with fake executables.

2026-09-22 16:01 PDT | [DOCUMENTED] | Documented response and stream limit boundaries, fake-worker evidence, and remaining real CLI and interruption-recovery gaps.

2026-09-22 15:40 PDT | [FIXED] | Disabled the CLI's incompatible nested sandbox inside the Bubblewrap boundary while retaining role-specific read-only and owned-file write mounts.

2026-09-22 15:38 PDT | [FIXED] | Attribute reconciled billed credits proportionally to milestone pools and the active work section, preserve later estimates, and allow a budget pause to resume only when its gates fit.

2026-09-22 15:35 PDT | [FIXED] | Added a five-minute agent runtime checkpoint that waits for active tool items, preserves partial traces after interruption, and stops a hung item after one additional minute.

2026-09-22 15:32 PDT | [FIXED] | Added a reconciled worker-overrun resume path that preserves section usage and returns control to the coordinator without repeating a model call.

2026-09-22 15:32 PDT | [DOCUMENTED] | Documented the verified CLI adapter, Bubblewrap requirements and limitations, fake-agent smoke checks, and remaining runtime and recovery gaps.

2026-09-22 15:32 PDT | [FIXED] | Confined CLI calls with Bubblewrap file mounts and fresh sessions, added prompt checkpoints, tied review and acceptance to content fingerprints, and added fake-runner regressions for confinement, stale evidence, and recovery.

2026-09-22 14:56 PDT | [FIXED] | Raised the coordinator's per-call estimate allowance and added replay of a saved decision after the earlier allowance paused a completed run; documented live trace monitoring.

2026-09-22 14:52 PDT | [FIXED] | Let the Astra controller reconcile included plan usage to zero billed credits while retaining later token estimates and milestone work allocations.

2026-09-22 14:20 PDT | [ADDED] | Added the JavaScript Astra build controller with bounded dispatch, context handoffs, durable usage and run artifacts, file ownership checks, independent review and acceptance gates, recovery, and fake-agent tests; documented operation and credit reconciliation.

2026-09-22 14:04 PDT | [DOCUMENTED] | Added the coordinator-only Astra development workflow, JavaScript controller bootstrap, 50% context handoffs, independent review gates, and a 2,000-credit allocation and usage ledger to the current hackathon plan.

2026-09-22 13:56 PDT | [DOCUMENTED] | Replaced the obsolete multi-project hackathon plan with the current programmable 4-bit Minecraft machine direction, trial contract, implementation milestones, demo readiness criteria, and unresolved design decisions.

2026-09-22 13:44 PDT | [ADDED] | Added a per-attempt Windows OBS recorder for TLauncher Minecraft with a dedicated window capture scene, run manifests, MKV originals, MP4 remuxing, and focused tests. Configured OBS WebSocket and verified a playable 1.21.1 capture; documented the redstone computer demo direction and recording workflow, and removed the requested repository AGENTS.md.

2026-09-20 16:45 PDT | [DOCUMENTED] | Retired the three Minecraft learning-demo scripts and their runner-specific tests for the approved mod-based open-model/Jev evaluation pivot. Preserved data-pack checks, connectors, episode recording, Weave tracing, historical results, and shared legacy skill/Doom modules. Updated the plan and current usage documentation; a replacement evaluation runner and Jev integration are not yet implemented.

2026-09-18 00:42 PDT | [DOCUMENTED] | Added the `production-ready-submission` Claude Code skill under `.claude/skills/`: a phased workflow (audit, plan approval, hardening, demo video, submission) for the CoreWeave Hacks Part 2 Most Production-Ready award, with a judge-facing rubric, a recorded-video shot list, and a submission and ceremony checklist that keep the event's eligibility rules and the repository's honesty and workflow rules.

2026-09-13 15:42 PDT | [ADDED] | Made the demos runnable from a fresh clone. `.env.demo.example` is a ready-to-fill configuration: add a W&B API key and inference project, and model, Weave tracing, and the local skill executor are already enabled. `scripts/run_doom_learning_sequence.py` now stops before any episode with a clear message when `WANDB_API_KEY` or the `openai`/`weave` integration packages are missing, and `--live-demo` no longer requires a `DISPLAY` variable on macOS or Windows. New `scripts/setup_minecraft_server.py` downloads and SHA-1-verifies the official 1.21.1 server jar, writes `server.properties`, allowlists and grants operator permission to the offline-mode connector bot, installs every `scenarios/minecraft` data pack, and runs `npm ci` for the sidecar; a server it created started, enabled all three packs, and lit the redstone lamp after `noob_agent_redstone:reset` and a placed power block. The README's install line now includes the `integrations` group, and a new "Run the demo yourself" section covers Doom, the Minecraft redstone lamp, Weave traces, and replay.

2026-09-13 15:42 PDT | [ADDED] | Added the non-benchmark Minecraft redstone lamp demo: `scenarios/minecraft/redstone-lamp-v1` (room reset and lamp-lit success message), `scripts/run_minecraft_redstone_demo.py` (cold attempt, Builder, and same-task skill reuse under equal limits), its tests, and `docs/redstone-demo.md`. The Mineflayer sidecar now lists `redstone_lamp` and `redstone_block` as public blocks and reports each block's `lit` property, which the demo needs to see the lamp.

2026-09-13 15:22 PDT | [DOCUMENTED] | Rewrote the README's "What is working today" from the section 22–24 scorecards: Doom skills now accepted in 5 of 5 benchmark sequences with 0 of 597 unusable Action replies, held-out at 8 of 30 with practice matching the private grade on every episode, and refinement not yet improving held-out; Minecraft Action loop working live, the recorded redstone run lighting the lamp cold with an unusable Builder reply, and the 40,000-token training ceiling still exceeded. Corrected the redstone image caption to say the frame is a demo recording, not a graded model result.

2026-09-13 15:16 PDT | [DOCUMENTED] | Added a "What it looks like" section to the README with the Doom cold-versus-learned-skill held-out comparison image and a frame from the Minecraft redstone lamp recording (`assets/redstone-lamp-lit.png`), and a section on how runs use W&B Weave (nested episode, step, and model-call traces, the live reasoning view and demo log that read from Weave), trace-driven debugging, and skill isolation (static policy, validator, local subprocess worker, and the not-yet-implemented CoreWeave Sandbox backend).

2026-09-13 12:53 PDT | [DOCUMENTED] | Rewrote the README in plain language, defined project terminology, clarified verified prototype status and limitations, and replaced the compressed repository table with a readable source-tree map.

2026-09-13 11:46 PDT | [DOCUMENTED] | Rewrote the README to explain what noob-agent is for: the two learning loops, compared conditions, scorecard, games, repository map, and getting-started commands.

2026-09-13 10:15 PDT | [ADDED] | Added section 24 step 24b, multi-round tuning: the improvement loop stops after 2 consecutive rounds without a kept version (a refinement that fails validation counts as one) and each refinement starts from the incumbent; practice uses the four `scenarios/doom/basic-v3` seeds, two targets on each side; refinement calls are recorded with purpose `refine` and counted by the scorecard; and a practice batch starts only when its worst case fits the learning call and token budgets, with a validated challenger that cannot fit rejected as `learning_budget`, fixing a live run where 2 of 5 sequences passed the 140-call ceiling. In the 5-sequence rerun every sequence accepted a skill, none passed a ceiling, practice agreed with the private grade on every episode, and held-out goals were 8 of 30; the most rounds logged was 2, three sequences stopped on the learning budget, and the two kept refinements won only on fewer primitives without improving held-out.

2026-09-13 08:37 PDT | [ADDED] | Added section 24 step 24a: the Builder's skill API reference now states that `context.observe()`, `remaining_budget()`, and `log()` charge no primitive action, and that `primitive_actions_used` must equal the sum of `result.primitive_actions_charged` and never be counted by hand, with wording tightened so the worst-case repair still fits. On 5 live Doom sequences every sequence accepted a skill, against 1 of 5 in `loop-bench-22e`, with no accounting rejections.

2026-09-13 08:29 PDT | [DOCUMENTED] | Approved `hackathon_plan.md` section 24, loop follow-ups from the section 22 report: an accounting-clear Builder prompt (24a), multi-round patience of 2 rounds with four practice seeds in a new `basic-v3` manifest and a `refine` model-call purpose (24b), and a compact Action observation so Minecraft decisions fit the training token ceiling (24c).

2026-09-13 06:03 PDT | [DOCUMENTED] | Recorded the section 22 step 22.G Minecraft confirmation: one cold `resonator-training-v1` episode on the live server with the optimized Action agent made 10 decisions with no unusable or capped replies and a 690 ms median decision, and ended at `repeated_failure`; its roughly 2,300 input tokens per decision would exceed the 40,000-token training ceiling over 20 decisions, which needs a separate approved change before a Minecraft benchmark.

2026-09-13 06:00 PDT | [ADDED] | Added section 22 step 22.D, the multi-round improvement loop: `ImprovementLoop` plays the accepted skill on training-split practice seeds (`scenarios/doom/basic-v2`) under held-out budgets, asks the Builder for one refinement per round from public practice evidence (`BuilderAgent.refine` stops at validated), keeps a refinement only when its public practice score (terminal rate, fewer primitives on ties) beats the incumbent, stops on a perfect score, no improvement, 3 rounds, or the 300,000-token and 140-call learning budget, runs held-out only after learning, optionally evaluates every kept version afterwards (`--curve`), and records how often practice agrees with the private grade. The registry allows a refinement to parent the accepted version, `eval_protocol.md` and `skill_contract.md` define the condition, the scorecard counts practice spend and renders rounds, and a validator bug that rejected a successful no-input skill in the vacuous invalid-input case is fixed. In a live 5-sequence run the loop behaved as specified, but no refinement improved a skill; perfect practice came with 5 of 6 held-out goals, and practice agreed with the private grade on every episode.

2026-09-13 05:40 PDT | [ADDED] | Added section 22 step 22.E, parallel held-out cells and validation: `LearningSequence(heldout_concurrency=...)` plays cells concurrently with per-cell connectors, model calls attributed to their own episode, report order preserved, and persistence refused alongside concurrency; validation runs its replay, negative-case, and variation phases concurrently with unchanged verdicts; the Weave sink nests calls per episode and splits delivery-only `flush()` from end-of-run `close()`; and concurrent cells flush tracing once after all end, fixing a live deadlock where a Weave flush blocked the event loop on other episodes' in-flight model calls. `loop_bench.py run` defaults to 6 concurrent cells. The live bench's median sequence took 17.5 s, down from 42 s, with no protocol ceiling exceeded.

2026-09-13 05:25 PDT | [ADDED] | Added section 22 step 22.C, repairs that converge: repair prompts carry the primitive definitions, the bounded trace, the rejected source, the name to keep, and up to three failing checks, added by priority so even a 12 KiB skill's repair stays under 5,000 estimated tokens; repairs default to a 3,000-token cap; a capped reply stops with `truncated_reply`; the Builder stops before any call that could exceed the sequence's 60,000-token or 22-call learning budget, counting training spend (`learning_budget_exhausted`); and a repair that renames the skill is a public `renamed_repair` rejection instead of an `UnknownSkillVersionError` crash. On 5 live Doom sequences 4 accepted a skill, no Builder call hit its cap, no protocol ceiling was exceeded, and held-out goals rose to 9 of 24 from 0 of 5 cold training goals.

2026-09-13 05:13 PDT | [ADDED] | Added section 22 step 22.B, a Builder grounded in the real skill API: the Builder system prompt carries a `SkillContext`/`SkillResult`/`EvidenceRef` API reference kept in sync with `skills/contract.py` by tests, plus a worked example for a fictional `press_switch` primitive that passes package validation. The build prompt lists every primitive's description and argument schema, and each evidence step carries the bounded public state after it (status values and up to five visible objects with properties). Builder calls send `NOOB_AGENT_BUILDER_THINKING` (off by default) and default to a 6,000-token cap, and the worst-case build prompt stays under 5,000 estimated tokens. In a live bench the first build returned API-correct code in 2.1 s and 550 tokens, and its repair fixed the reported check; the bench then stopped on a pre-existing registry crash when the repair renamed the skill, which step 22.C fixes.

2026-09-13 05:06 PDT | [ADDED] | Added section 23 step 23b, the live Weave reasoning view: `scripts/live_reasoning_view.py` polls Weave, never SQLite, for a run's episode, Action decision, step, and Builder calls and serves a stdlib-only local page and event feed. The page shows each decision's subgoal, expected evidence, action, arguments, result, latency, and tokens, plus Builder code, episode outcomes, and a link to every call in Weave, and never serves prompt or system text. `--live-demo --live-view` starts it for the run, refuses when tracing is disabled, and stops it after the final trace flush. Root calls are read without a cursor and steps with their own cursor, fixing missed step results and episodes found against live Weave. In a live demo, decisions appeared a median 2.0 s after the model replied.

2026-09-13 04:40 PDT | [ADDED] | Added section 23 step 23a, one persistent game per learning sequence: `LearningSequence(persistent_connector=True)` resets a single connector for training, the Builder pause, and every held-out cell, grades each episode before the next reset, and closes it exactly once, including when the Builder fails or the sequence is cancelled. `--live-demo` always uses it, replacing the section 20 training-window continuity flag, so one Doom window stays open for the whole run. A real headless ViZDoom sequence with the scripted provider used 1 connector and 1 close for 7 episodes and completed all 6 held-out goals, and a reset after a finished episode matches a fresh connector.

2026-09-13 04:39 PDT | [DOCUMENTED] | Approved `hackathon_plan.md` section 23, a non-benchmark demo milestone: one persistent Doom game across a learning sequence (23a), and a stdlib-only local web view that reads the run's Action decisions, Builder calls, and episode outcomes from Weave in real time beside the game, started by `--live-demo --live-view` (23b).

2026-09-13 04:31 PDT | [ADDED] | Added section 22 step 22.A reliable, fast Action decisions: per-role `NOOB_AGENT_ACTION_THINKING` and `NOOB_AGENT_BUILDER_THINKING` settings (off by default) sent as `chat_template_kwargs.thinking`, a strict JSON-schema decision reply whose `action` is an enum of the offered primitive and skill names, fixed instructions moved to the system prompt, compact argument hints in place of JSON Schemas, a truncation mark on capped unusable replies, per-call client closing, and schema version 5 with `model_call.request_options_json`. On 5 live headless Doom sequences unusable replies fell from 46-52% to 0 of 100, the median decision from 5.5 s to 561 ms, learning tokens to about 32,000 per sequence, and sequence wall time to 45 s. Native tool calls were tried first and dropped because their template overhead exceeded the training token ceiling; the plan records the amendment.

2026-09-13 03:56 PDT | [ADDED] | Added the section 22 step 22.F loop scorecard and `scripts/loop_bench.py`: `run` executes fixed-seed headless Doom learning sequences into a new Git-ignored database and writes JSON and Markdown scorecards, and `score` reads any existing database through a read-only connection. The scorecard reports unusable and capped Action decisions, nearest-rank decision latency, Builder calls and caps, acceptance (labeled when inferred), per-phase wall time, and learning tokens and calls against the `eval_protocol.md` ceilings. `docs/loop-optimization.md` records the baseline of the recorded live runs: 46-65% unusable replies, 5.4-5.7 s median decisions, and every recent sequence over a protocol ceiling.

2026-09-13 03:46 PDT | [ADDED] | Added mandatory isolated skill validation before registry acceptance for section 22 step 22.0: real load and API-contract checks, public training-trace replay through a fake connector, missing-target, failed, unknown, invalid-input, and exhausted-budget cases, three-run repeatability, a generated object-ID variation, and public failure evidence for the Builder's bounded repair.

2026-09-13 03:43 PDT | [DOCUMENTED] | Approved `hackathon_plan.md` section 22, the loop-optimization milestone: land the isolated validation pipeline, add a loop scorecard and headless bench, make Action decisions reliable and fast with per-role thinking controls and native tool calls, give the Builder the real skill API and observation evidence, make repairs converge within protocol ceilings, parallelize held-out cells and validation, add a multi-round practice-based improvement loop with its learning budget, and confirm on Minecraft.

2026-09-13 00:14 PDT | [DOCUMENTED] | Proposed `hackathon_plan.md` section 21, a non-benchmark token-budget Doom demo loop: `--live-demo --token-budget 500000` runs fresh learning sequences until the budget is spent, with demo-only Builder caps up to 32,000 output tokens and 3 repairs, a 3,600-second safety deadline, a full-screen display setting for the live demo and replay viewer, a `doom-demo-log.md` written live from the Weave trace, and one paid demo run, split into four reversible pull requests.

2026-09-12 23:57 PDT | [ADDED] | Kept the visible live-demo training window open and paused on its final frame throughout Builder authoring, then closed it before any fresh held-out connector opens. `EpisodeRunner` retains its close-on-finish default, while explicit external ownership now has tested normal and Builder-error cleanup paths.

2026-09-12 23:56 PDT | [DOCUMENTED] | Approved live-demo window continuity during Builder authoring: keep the finished training ViZDoom instance open and paused on its final frame, close it before a fresh held-out episode, and preserve immediate close behavior for ordinary evaluation plus guaranteed cleanup on errors and deadlines.

2026-09-12 23:49 PDT | [DOCUMENTED] | Approved one paid, non-benchmark live Doom loop diagnostic using the existing maximum validated output caps of 2,000 Action tokens and 8,000 Build/Repair tokens, with the same model, prompts, game budgets, visible real-time connector, 600-second deadline, separate database, local sandbox label, and Weave tracing; no code or persistent environment setting changes.

2026-09-12 23:41 PDT | [ADDED] | Added an explicit `--live-demo` mode to the Doom learning-sequence runner: every game episode opens visibly and runs at native speed, the whole sequence has a configurable deadline capped at 600 seconds, storage and experiment records are labeled `non-benchmark-live-demo`, and timeout cleanup durably records cancelled model calls and finalizes an interrupted episode before closing Doom and flushing Weave. Normal evaluation defaults are unchanged. Manual run `doom-live-demo-20260912-234243-PDT` opened the WSLg game window, completed before its deadline, and remotely delivered the episode with all 20 Action calls and steps plus the separate Builder call; the model made 9 attacks without turning, did not kill the target, and produced no accepted skill.

2026-09-12 23:35 PDT | [DOCUMENTED] | Approved `hackathon_plan.md` section 20 for a visibly rendered, real-time, Weave-traced Doom learning sequence with a 600-second whole-run deadline, separate storage, cancellation-safe durable records, and explicit `non-benchmark-live-demo` labeling while leaving ordinary evaluation defaults unchanged.

2026-09-12 23:14 PDT | [ADDED] | Added `scripts/replay_doom_episode.py` for step 19b, which lists the Doom episodes in a database or replays one in a visible, real-time ViZDoom window from a temporary copy of the store, checks every step against the record, stops with exit 1 at the first mismatch, refuses non-Doom episodes and other connector versions, and never calls a model, the Builder, a grader, or a skill executor. A headless replay of the first live Doom run matched all 20 steps.

2026-09-12 23:09 PDT | [ADDED] | Added display-only `window_visible` and `realtime` settings to `DoomSettings` for step 19a: a visible ViZDoom window that renders every frame, and real-time pacing that advances accepted actions one tick at a time at 35 ticks per second and stops when the episode ends. Both default to off, the connector version stays `doom-vizdoom-v2`, and real-ViZDoom tests show paced step results identical to the default settings.

2026-09-12 23:02 PDT | [DOCUMENTED] | Proposed `hackathon_plan.md` section 19, a Doom episode replay viewer: display-only `window_visible` and `realtime` settings for the Doom connector that change no recorded value, and `scripts/replay_doom_episode.py`, which replays a recorded episode's exact requests from a copy of the store in a visible ViZDoom window, verifies every step against the record, stops at the first mismatch, and never calls a model or grader, split into two reversible pull requests.

2026-09-12 22:49 PDT | [ADDED] | Added validated per-role output settings with 1,024-token Action and 6,000-token Builder defaults, wired them through cold, build, repair, and held-out calls and the Doom run summary, and made run-exit trace delivery best effort by draining Weave after closing open calls and using a real W&B base URL default.

2026-09-12 22:36 PDT | [DOCUMENTED] | Amended `hackathon_plan.md` section 18 so step 18b also delivers step 18a traces: `WeaveTraceSink.flush()` drains the Weave client's upload queue, the Doom run script flushes the trace sink at exit even when no skill is accepted, and `.env.example` sets `WANDB_BASE_URL=https://api.wandb.ai` instead of exporting a blank host, with tests and rollback notes.

2026-09-12 22:32 PDT | [ADDED] | Added persisted and traced Model call records for step 18a: `LearningSequence` wraps each role's client in `RecordingModelClient`, which writes one `model_call` row per Action, Build, and Repair call (purpose, experiment, episode, and `action_id` links; provider and exact model; prompt, cap, and temperature; reply, provider reasoning, finish reason, token usage stored as unknown when unreported, latency, and error) and mirrors it to Weave under its episode. `WandbInferenceClient` now keeps `finish_reason`, reasoning text, and whether usage was reported. Schema version 3 adds the table and upgrades a version-2 database in place.

2026-09-12 22:14 PDT | [DOCUMENTED] | Proposed `hackathon_plan.md` section 18 after diagnosing the failed live Doom sequence, where reasoning tokens exhaust the output cap and leave empty or cut-off replies: persisted and traced Model call records with finish reason, reasoning text, and unknown-usage handling under schema version 3; per-role maximum output token settings of 1,024 for Action and 6,000 for Builder, validated against the protocol token ceilings; and a second recorded live run, split into three reversible pull requests.

2026-09-12 22:10 PDT | [DOCUMENTED] | Recorded the first live Doom learning sequence with W&B Inference `deepseek-ai/DeepSeek-V4-Flash-0731` and the labeled local-subprocess executor: cold training hit its decision limit with 13 of 20 unusable Action replies and no kill, the Builder's reply was unusable at its 2,048-token cap, and held-out episodes were skipped because no skill was accepted.

2026-09-12 22:10 PDT | [ADDED] | Added the game-neutral `LearningSequence` for build-order step 8c, which runs one cold training episode under the frozen training budgets, one Builder run on that episode's public evidence, and fresh-connector held-out episodes with the accepted skill, grading each after it ends. Added `scripts/run_doom_learning_sequence.py`, which refuses to run with a disabled model provider, model ID, or sandbox mode.

2026-09-12 21:54 PDT | [FIXED] | Doom episode IDs are now unique across connector instances (the scenario ID plus a random decimal token), so held-out episodes of one scenario from fresh connectors can be recorded in one store instead of colliding on `doom-doom-<scenario>-r001`.

2026-09-12 21:52 PDT | [ADDED] | Added the independent Doom grader for build-order step 8b: a private outcome pinned to its episode, a grade decided only after a finished Doom episode from kills and player death alone, refusal of mismatched or foreign-game episodes, and a scripted center-and-fire oracle that solves every precommitted held-out cell within the frozen 12-decision, 24-primitive budget.

2026-09-12 21:46 PDT | [FIXED] | A rejected Doom request now advances the public sequence like any other durable result, so the episode runner records it as a step instead of ending the episode on a sequence mismatch.

2026-09-12 21:46 PDT | [ADDED] | Added Doom connector `doom-vizdoom-v2` for build-order step 8a: declared training and held-out `basic.cfg` scenarios with repeatable reset-time starting offsets, precommitted seeds in `scenarios/doom/basic-v1/manifest.json`, the public target-elimination goal, a bounded `screen_offset` aiming property on visible objects without the player's own label, seed-free episode IDs, and a harness-only private outcome for kills, death, and timeout.

2026-09-12 21:36 PDT | [DOCUMENTED] | Proposed the core-loop milestone for build-order step 8 in `hackathon_plan.md` section 17: Doom connector `doom-vizdoom-v2` with declared training and held-out scenario variations, a public target-elimination goal, a screen-offset aiming property, and a harness-only private outcome; an independent Doom grader; and a game-neutral single learning sequence with a live Doom run, split into three reversible pull requests.

2026-09-12 21:29 PDT | [DOCUMENTED] | Proposed the core-loop milestone for live Minecraft clean/faulty grading in `hackathon_plan.md` section 16: an evaluation data pack with validation and matched held-out builds, private build selection and predicate reads over a localhost-only RCON channel that bypasses the unchanged public connector, a clean-twin replay, and a live end-to-end verification and reproduction check, split into three reversible pull requests.

2026-09-12 21:16 PDT | [ADDED] | Completed Minecraft connector version `minecraft-0.2.0` with the seven frozen primitives beyond `observe`, strict pre-delivery argument and latest-object-ID validation, public container and inventory objects, five-tick interaction settling, Mineflayer movement and interaction mappings, stable reset air observations, and a live end-to-end primitive acceptance flow.

2026-09-12 21:03 PDT | [DOCUMENTED] | Approved the Minecraft primitive-completion milestone for the seven frozen connector actions beyond `observe`, including strict public-target validation, sidecar mappings, deterministic live checks, and explicit exclusions for contracts, dependencies, prompts, budgets, grading, and held-out content.

2026-09-12 20:54 PDT | [DOCUMENTED] | Updated the implementation handoff after integrating held-out skill reuse, clean/faulty reproduction, and the concurrently merged Doom connector into `main`, including final test evidence and the remaining live Minecraft grading scope gap.

2026-09-12 20:51 PDT | [ADDED] | Added the headless ViZDoom Doom connector with the ten frozen BDP primitives, public HUD/visible-label observations, fixed-seed reset, one-tick signed turns, contract argument rejection, and live connector acceptance tests. Declared ViZDoom as the Doom game-layer runtime dependency.

2026-09-12 20:46 PDT | [DOCUMENTED] | Recorded the successful local ViZDoom 1.3.0 installation and 26-check headless Doom environment verification; no project dependency or Doom connector was added.

2026-09-12 20:43 PDT | [DOCUMENTED] | Documented the verified local Minecraft server and world locations, tmux lifecycle, WSL/TLauncher connection requirements, live connector check, troubleshooting findings, current implementation map, stacked pull-request state, and next build-order steps.

2026-09-12 19:20 PDT | [FIXED] | A rejected request now advances the Minecraft connector's public sequence like any other durable result, so the runner records the rejection as a step and continues instead of ending the episode as an unknown result.

2026-09-12 19:05 PDT | [ADDED] | Added the clean/faulty pair and independent reproduction for build-order step 6: public Finding records and private verdict/reproduction records, a private grader that verifies a reported defect only when its counts and public evidence resolve, the fault predicate holds, and the matched clean twin does not show the behavior, a reproduction runner that replays the minimal evidence sequence from a fresh reset with a different seed through ordinary primitives and records the first mismatch, schema version 2 (finding, finding_verdict, reproduction tables; a version-1 database is upgraded in place), and an optional finding object the Action agent can attach to a decision.

2026-09-12 18:45 PDT | [ADDED] | Added held-out reset and skill reuse for build-order step 5: an Action agent that chooses one primitive or one accepted skill per turn and states its subgoal and expected evidence, a skill runtime that records and charges every nested primitive to the same held-out budget and refuses calls to anything but manifest primitives, an interactive skill-executor seam whose default executes nothing and whose local-subprocess fallback is labeled as weaker isolation, and a held-out runner that starts every episode with a fresh conversation, offers only accepted versions, and never hands a held-out scenario to validation or repair.

2026-09-12 17:58 PDT | [ADDED] | Added the Builder agent that turns a bounded selection of public trace evidence into one skill candidate: the candidate is recorded in the registry as `proposed` before validation runs, a rejection produces a repair carrying its parent version, the repair budget is finite, and repairs receive only the public validation errors. Added the provider-neutral model client, disabled by default, with a lazily imported W&B Inference adapter.

2026-09-12 17:55 PDT | [DOCUMENTED] | Approved the core-loop milestone for build-order steps 4-6 in `hackathon_plan.md` section 14, covering the Builder-generated candidate, held-out reset and skill reuse, and the clean/faulty pair with independent reproduction, plus the narrow skill-runtime milestone that permits executing a generated candidate only through the sandbox seam.

2026-09-12 17:36 PDT | [ADDED] | Added the Minecraft reset/observe connector and pinned Mineflayer JSON Lines sidecar for the dedicated 1.21.1 training server.

2026-09-12 17:31 PDT | [ADDED] | Added the generated-skill capability/result contract and validated hand-written fixture bridge into the immutable registry.

2026-09-12 17:19 PDT | [ADDED] | Added ViZDoom environment groundwork for build-order step 7: a non-production `scaffolding/doom_env` check that maps all ten Doom BDP primitives to ViZDoom controls and verifies deterministic reset, tick-exact advancement, public-only observations, and the contract's Doom timeouts, plus `docs/doom-env.md` with the install, verification, and the three ViZDoom behaviors that fail silently. No connector, no new project dependency.

2026-09-12 17:13 PDT | [DOCUMENTED] | Approved the core-loop milestone for build-order steps 1-3 (Minecraft connector reset/action, cold episode recording, hand-written skill validation and registry), tracked as issues #13-#15.

2026-09-12 17:21 PDT | [ADDED] | Added the best-effort Weave trace mirror for recorded episodes: the runner mirrors an episode, each durable step, and the final outcome as one nested call tree, only after SQLite has committed the record, only when `TraceSettings.enabled` is true, and never in a way that a failing or mismatched trace backend can change or end an episode.

2026-09-13 00:12 UTC | [FIXED] | Static skill policy scanning now rejects aliased forbidden builtins like `open` and `__import__`.
2026-09-13 00:10 UTC | [FIXED] | Tightened static skill import policy to reject private member imports from allowlisted modules (for example, `from dataclasses import _create_fn`).

2026-09-12 17:02 PDT | [ADDED] | Added the resettable vanilla Minecraft Resonator training scenario, exact public feedback, and executable two-run oracle.

2026-09-12 16:53 PDT | [ADDED] | Added the immutable skill version registry: the registry assigns each candidate its version and content hash, enforces the `proposed` -> `validating` -> `accepted`/`rejected` and `accepted` -> `retired` transitions, keeps a repair's parent version, and exposes only the single accepted version as an available skill.

2026-09-12 16:47 PDT | [ADDED] | Added the non-executing validation boundary for generated skill packages: typed `noob-agent.skill.v1` metadata, package size/name/schema checks, and an AST-based static policy check rejecting forbidden imports and constructs.

2026-09-12 16:26 PDT | [DOCUMENTED] | Removed the isolated-scaffolding-only default permission from CLAUDE.md; core-loop work now proceeds under the Mandatory Change Gate and protected-area rules alone.

2026-09-12 16:14 PDT | [FIXED] | Closed the SQLite episode store's remaining validation gaps: `create_experiment` and `finalize_episode` now re-validate before writing, steps can no longer be appended after an episode is finalized, `read_episode` wraps a corrupted row as a `StorageError` instead of leaking `ValidationError`, and non-uniqueness integrity failures are no longer misclassified as duplicates.

2026-09-12 16:07 PDT | [ADDED] | Added the public GameConnector protocol and a deterministic bounded cold-episode runner that records every step through the SQLite store and always ends with a recorded stop reason, including when a connector returns a result the harness cannot durably record.

2026-09-12 15:55 PDT | [ADDED] | Added durable local records and a SQLite episode store as the local source of truth, with explicit transactions and duplicate-identity rejection.

2026-09-12 15:23 PDT | [ADDED] | Added isolated fake-connector scaffolding with a deterministic scripted transcript and a standalone contract validator.

2026-09-12 15:16 PDT | [ADDED] | Defined immutable public connector data models and contract validation tests.

2026-09-12 14:40 PDT | [ADDED] | Added offline W&B, Weave, and CoreWeave Sandbox configuration seams with disabled local adapters.

2026-09-12 14:35 PDT | [DOCUMENTED] | Added the TypeSafe headline model strategy, W&B Inference baseline and fallback, and operational go/no-go gate.

2026-09-12 14:33 PDT | [ADDED] | Created the approved pre-BDP Python package, dependency lock, CLI placeholder, test layout, and project tooling.

2026-09-12 14:20 PDT | [DOCUMENTED] | Set hackathon_plan.md as the authoritative project plan for all agents.

2026-09-12 14:13 PDT | [DOCUMENTED] | Renamed the project to noob-agent across repository documentation and contracts.

2026-09-12 14:12 PDT | [DOCUMENTED] | Added protected core-loop boundaries, isolated scaffolding rules, change gates, and repository-protection requirements.

2026-09-12 14:06 PDT | [DOCUMENTED] | Added repository-wide agent workflow, Claude Code branch rules, and changelog conventions.

### Milestone 3 connected-loop chunk

- Added a fresh-trial planner/Jev loop with durable pre-dispatch model-call charges,
  actual-result feedback, repair accounting and public checkpoint journaling.
- Intentions now support multiple Jev-selected offers with per-step observations,
  explicit early termination and action/time caps enforced inside Actions.
- Real providers, canonical trial selection and live repair proof remain pending;
  no model success or milestone acceptance is claimed.

2026-09-22 | [VERIFIED] | Milestone 4 interface-only checkpoint: 152 focused Python tests, 12 relevant Node tests, Ruff/format/mypy. Retained one live unknown-outcome read failure and a fresh dedicated-world inspection with eight charged air readings/eight detailed interface rejections. No controls or world blocks changed; behavioral module graders, real tick control and agent-built module success remain pending. Exact evidence and same-milestone continuation are in docs/redstone-trials.md; no milestone acceptance.
