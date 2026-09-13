# Changelog

## Unreleased

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
