# Submission text (draft — paste into the AGI House Part 2 page)

This is a draft for the requester to review, fill in placeholders, and paste.
Every number below traces to a run ID or scorecard cited next to it; see
[`docs/loop-optimization.md`](../loop-optimization.md) and
[`docs/demo-trials.md`](../demo-trials.md) for the underlying records.

## Title

noob-agent: a production-ready harness for measuring skill-learning in games

## One-liner

noob-agent is a drop-in harness that measures whether a model can turn
experience in an unfamiliar game into tested, reusable code, and it does
this in a way you could run in CI.

## Problem

Most "can an agent learn" demos show one lucky run. There is no shared,
reproducible way to ask: given the same new-player controls, an unfamiliar
game, and a fixed learning budget, how effectively does a model turn
experience into a *reliable, reusable* skill — one that still works on
variations it never saw while learning?

## Approach

A model starts as a "noob" with primitive controls only (observe, move,
inspect, pick up, interact). It explores under a frozen budget, and a
separate Builder model call turns the public trace of that attempt into a
versioned Python skill. The skill goes through static policy checks and a
sandboxed validator (replay, negative-case, and variation phases) before it
is ever saved. Only an accepted skill is carried into a fresh reset and
conversation, where it is tried on held-out variations and graded by an
independent grader the model never talks to. Every step is recorded in
SQLite as the source of truth and mirrored to W&B Weave for inspection. The
same Action agent, Builder, skill registry, and grader run unmodified on two
different connectors — Doom (ViZDoom) and Minecraft (Mineflayer) — so only
the connector changes between games.

## Result (with numbers)

- **Doom benchmark** (`docs/loop-optimization.md`, `loop-bench-24b-r2`, 5
  fixed-seed sequences): the model built and validated a skill in **5 of 5**
  sequences, with **0 of 597** Action replies unusable. Held-out transfer to
  unseen seeds completed **8 of 30** goals; the best sequence solved 4 of 4
  practice seeds and 5 of 6 held-out cells. Practice scores agreed with the
  private grader on every episode.
- **Doom demo trial, non-benchmark** (`docs/demo-trials.md`, run
  `doom-demo-trial-20260918c`, raised non-benchmark budgets): the run built
  and accepted a skill on its first attempt with no repair needed, and
  completed 1 of 6 held-out goals, spending 79,288 tokens across 61 model
  calls with 0 truncated replies. This is one labeled demo-trial run, not a
  benchmark statistic.
- **Minecraft**: the shared Action loop runs live on the second game with no
  connector-specific tuning — a cold training episode had 0 of 10 unusable
  replies and a 690 ms median decision (`mc-confirm-22g-20260913T125747Z`).
  The full learning loop (Builder, validation, held-out transfer) is **not
  yet proven** on Minecraft: the redstone-lamp demo trial has not gotten a
  skill accepted in any attempt recorded so far (`docs/demo-trials.md`).
  <!-- MINECRAFT TRIAL 2 RESULT -->

## Why it is production-ready

- Deterministic, fixed seeds for every scenario and reset.
- Frozen benchmark budgets as constants in code (`runtime/sequence.py`,
  `runtime/heldout.py`), never touched by the separately labeled,
  non-benchmark demo-trial profile (`run_kind: non-benchmark-demo-trial`,
  its own Git-ignored database).
- SQLite as the durable source of truth; Weave tracing is best-effort and
  never changes a recorded attempt.
- Independent private grading the model conversation never sees.
- Generated skills are versioned, immutable once accepted, statically
  policy-checked, and run in a separate worker process with a narrow API
  (CoreWeave Sandbox is the intended backend; every recorded run so far used
  the labeled, weaker local-subprocess fallback).
- An honest scorecard: `docs/loop-optimization.md` and `docs/demo-trials.md`
  report failures, rejections, and truncations alongside successes, and a
  CI workflow (ruff, mypy, pytest, no credentials) runs on every push and
  pull request.

## Part 1 → Part 2 delta

Part 1 shipped the two-game learning loop, connectors, SQLite records, and
Weave tracing as a working prototype with an unreliable Action loop (about
half of Doom Action replies were truncated) and no live evidence the loop
actually produced held-out transfer. Since then (see the README's "What
changed since Part 1" list for links): the Action and Builder prompts were
reworked so skills are accepted reliably (5 of 5 vs. 1 of 5) with zero
truncated replies across 597 calls; a multi-round practice loop and an
accounting-clear Builder prompt were added; the two failing tests from the
Part 1 audit were fixed and CI was added; a labeled non-benchmark demo-trial
mode with automatic budget escalation was built and used for monitored,
documented Doom and Minecraft demo trials; and this judge-facing submission
package was written.

## Links

- Repo: <https://github.com/NWelde/noob-agent>
- Demo video: _link coming_
- Weave project: `nathanweldegiorgis731-minerva-university/Noob-agent`
  (_public link coming_ — not yet confirmed public)
- W&B Report: _public link coming_

## W&B usage

- **W&B Inference**: every Action, Builder, and repair model call goes
  through W&B Inference with the configured `WANDB_API_KEY`
  (`deepseek-ai/DeepSeek-V4-Flash-0731` in every recorded run to date).
- **Weave traces**: `WeaveTraceSink` mirrors every episode, step, and model
  call as a nested call tree (`noob_agent.episode` → `noob_agent.step`,
  `noob_agent.model_call`) after each event is durably written to SQLite.
  Look under the Weave tab of the project above, or open the run-specific
  links in `docs/demo-trials.md`.
- **Reports**: a public W&B Report comparing cold vs. learned-skill results
  with cost, latency, and token numbers is planned but not yet published —
  see the go/no-go checklist (`docs/submission/go-no-go.md`).

## Known limitations

- Minecraft's full learning loop (Builder → validated skill → held-out
  transfer) has not been proven live: every recorded redstone-lamp demo
  trial attempt failed to get a skill accepted, most recently traced to a
  repair-cap bug fixed in PR #82 but not yet re-confirmed by a clean
  escalating run. <!-- MINECRAFT TRIAL 2 RESULT -->
- Doom held-out transfer is real but uneven: the benchmark's best sequence
  solved 5 of 6 held-out cells, but held-out totals across 5 sequences are
  8 of 30, and multi-round refinement has not yet improved held-out results
  over the incumbent skill.
- Generated skills run in a labeled local-subprocess fallback, not the
  intended CoreWeave Sandbox backend, which is not implemented yet.
- Demo-trial numbers (the non-benchmark run above) are single labeled runs
  under raised budgets, explicitly excluded from the benchmark scorecard —
  they show the loop can complete a sample task, not a statistical result.

## Ceremony

_Attendance to be confirmed by the requester: who is attending 2026-10-01,
4:50 PM, Moscone South Expo Stage (arrive 4:40 PM), in person or by Zoom._
