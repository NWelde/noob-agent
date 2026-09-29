# Register checkpoint implementation — 2026-09-29

## Changes

- Construction can continue past three intentions. Readiness feedback arrives every twelve; existing action, call and wall-time limits still apply.
- Invalid recipes identify undeclared controls and invalid control roles. Validation exhaustion records `planner_validation_exhausted` with the last validation reason.
- Support failures identify the required support coordinate. Later support-only intentions are allowed, so the agent can build supports before attached components.
- `--stop-after-module register` targets the agent-designed four-bit register. It stops successfully only after the existing independent behavioral grader passes. Full computer success remains unevaluated.
- Manual recovery accepts failed or interrupted model inference, or a completed action-feedback boundary after interruption, only when the journal has no unknown world mutation and the timeline is clean. Every resume verifies the player and all touched world cells, saves that verification, and rebuilds placement evidence from actual readbacks.
- Previous failed checks stay in planner feedback alongside the current verified layout. Already repaired hardware should trigger reinspection rather than repeated replacement. Register feedback also asks the planner to trace the input, storage, STEP and output before repeating an ineffective repair.
- Replacement placements are not skipped when the same intention can break that cell. A regression covers removing and restoring an existing lever.
- Register feedback clarifies that load grading supplies a two-tick STEP pulse and samples after settling; STEP-low in the final readback is expected.
- A selection timeout at the intention deadline returns completed results to the planner without dispatching the unresolved action. Genuine provider timeouts and the trial wall deadline still stop the trial. Three regressions cover these distinctions.
- Jev selection timeouts now receive the diagnostic `SelectionTimeout`.

## Validation

The focused planner, loop, module, trial and Jev suites passed: **132 tests**. The final focused run includes the current-layout feedback, clean-boundary recovery and timeout diagnostic regressions. The unchanged behavioral grader suite also passed: **35 tests** (167 total across these suites). Ruff and `git diff --check` passed. Pytest reported Python/pytest-asyncio deprecation warnings. An earlier type-check comparison reproduced six existing errors on unchanged HEAD; type checking was not green.

## Live experiment

Main journal: `.noob-agent/redstone-trials/20260929T192522-1bc31bdf2bbe4266807e55fd7cc23748/manifest.json`.

The fresh reset checks passed twice. The build exceeded three intentions and reached interface inspection. The first inspection found torches at the declared reset/STEP lever locations. A later resume independently observed both locations as levers. The next session includes those current readbacks so the planner can re-inspect the interface.

The second inspection passed the interface check and entered behavioral grading. Initial reset, zero load, zero hold and zero reset passed. Loading `1` failed: expected `a=1`, observed `a=0`. The planner is repairing its own bit-0 circuit.

**Final status: incomplete.** Seven sessions ended at 20:53:34 UTC with `LoopLimit`; no register behavioral pass or full computer grade was recorded. The current build is preserved. No recording is available because the Windows Minecraft client and OBS were not running.

Final report and complete manifest: [register-checkpoint-run-192522-2026-09-29.md](register-checkpoint-run-192522-2026-09-29.md). Earlier attempt and reset exports: [register-checkpoint-prior-attempts-2026-09-29.md](register-checkpoint-prior-attempts-2026-09-29.md).
