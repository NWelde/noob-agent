# Current project status

Updated 2026-09-25. The Minecraft redstone computer is the only active demo path. The approved goal and milestone sequence are in
[`hackathon_plan.md`](../hackathon_plan.md).

## Working foundation

- Vanilla Minecraft Java 1.21.1 trial world with repeatable, hashed resets.
- Bounded placement and trusted server-timed redstone checks.
- Planner and Jev adapters connected through a recorded trial loop.
- Independent behavioral graders for register, arithmetic, instruction
  storage/readout, and visible output.
- Trial manifests retain planner offers, Jev actions, observations, grades,
  errors, budgets, and reset evidence.
- Provider pacing, duplicate-placement detection, and final-reset recovery are
  implemented with offline test coverage.

## Demo status

Milestone 4, model-designed modules, remains incomplete. Recent V4-Pro/Jev runs
placed redstone components but did not pass a public module grade. The recorded
blockers include provider timeouts and rate limits, invalid or repeated
placements, and incomplete circuit declarations. A successful reset or a
fixture-driven test does not count as a model-built module pass.

Milestones 5 and 6 remain ahead: execute two programs on the same completed
machine, then prepare the HUD, rehearse the run and recovery path, and record an
actual successful trial. The immediate technical target is the first
model-designed module pass. Detailed per-trial evidence is in
[`redstone-trials.md`](redstone-trials.md).

## Validation

Run the local Python suite with `uv run pytest`. Tests that require the local
Minecraft server or its installed Mineflayer dependencies may skip when that
environment is unavailable. A scripted fixture trial validates the connected
trial plumbing but does not establish model success. Live provider and in-game
acceptance evidence must be recorded separately from unit tests.
