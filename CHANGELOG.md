# Changelog

## Unreleased

2026-09-12 22:36 PDT | [DOCUMENTED] | Amended `hackathon_plan.md` section 18 so step 18b also delivers step 18a traces: `WeaveTraceSink.flush()` drains the Weave client's upload queue, the Doom run script flushes the trace sink at exit even when no skill is accepted, and `.env.example` sets `WANDB_BASE_URL=https://api.wandb.ai` instead of exporting a blank host, with tests and rollback notes.

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
