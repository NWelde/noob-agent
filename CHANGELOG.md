# Changelog

## Unreleased

2026-09-12 16:53 PDT | [ADDED] | Added the immutable skill version registry: the registry assigns each candidate its version and content hash, enforces the `proposed` -> `validating` -> `accepted`/`rejected` and `accepted` -> `retired` transitions, keeps a repair's parent version, and exposes only the single accepted version as an available skill.

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
