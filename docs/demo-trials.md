# Demo trials (non-benchmark)

**Status: in progress.** This document covers the first, pre-escalation-fix
attempt of each game under `hackathon_plan.md` step 26.4. Both attempts
stopped short of the sample task for reasons the section 26.3 escalation
logic does not yet cover; the author of 26.3 is fixing escalation (per-episode
wall-clock stops and per-call output caps). This document will be updated
with further attempts once those fixes land and new run IDs are sent.

Every run here is explicitly **non-benchmark**: it uses the demo-trial
profile's raised limits (`RUN_KIND = "non-benchmark-demo-trial"` /
`CONDITION = "non-benchmark-minecraft-redstone-comparison"`), a separate
Git-ignored SQLite database, and never enters a scorecard or comparison. It
also reflects a single run of each game, on a local subprocess sandbox
(`NOOB_AGENT_SANDBOX_MODE=local`), not a hardened isolation boundary.

## Doom trial

- Run id: `doom-demo-trial-20260918a`
- Command: `cd /home/nathan/noob-agent-trial && NOOB_AGENT_SANDBOX_MODE=local uv run --env-file .env python scripts/demo_trial.py --run-id doom-demo-trial-20260918a`
- Records: `.noob-agent/demo-trial.sqlite3` (SQLite), `.noob-agent/demo-trials/doom-demo-trial-20260918a.json` (JSON summary)
- Weave: https://wandb.ai/nathanweldegiorgis731-minerva-university/Noob-agent/weave/calls?filter=%7B%22traceRootsOnly%22%3Atrue%7D&peekPath=&query=doom-demo-trial-20260918a-a01

### Attempt 1 (pre-fix)

Source: `doom-demo-trial-20260918a.json`, cross-checked against
`demo-trial.sqlite3` (`experiment`, `episode`, `episode_outcome`, `model_call`
tables; query used: `select experiment_id, purpose, action_id, finish_reason,
input_tokens, output_tokens, max_output_tokens, error, started_at from
model_call order by started_at`).

| Field | Value |
|---|---|
| Sequence id | `doom-demo-trial-20260918a-a01` |
| Limits offered | learning_token_budget=1,000,000; learning_call_budget=66; training_decision_budget=60; training_primitive_budget=120; heldout_decision_budget=36; heldout_primitive_budget=72; deadline_seconds=3,600 |
| Training episode stop reason | `wall_time_limit` (per-episode wall clock, 180,000 ms, from the `experiment.wall_time_budget_ms` row — **not** the 3,600 s whole-run deadline) |
| Training decisions / primitives used | 14 / 6 (well under the 60 / 120 budget — the clock ran out first) |
| Action calls | 14, each `max_output_tokens=1024`; **7 of 14 (50%) hit `finish_reason=length`** (truncated): `a_0002, a_0005, a_0006, a_0008, a_0009, a_0011, a_0012, a_0013` |
| Builder call | 1 `build` call, `max_output_tokens=6000`, `finish_reason=length` (the Builder's own reply was truncated) |
| Overall reported stop reason | `truncated_reply` (from `heldout_skipped_reason`, since the Builder reply was truncated) |
| Skill accepted | No — Builder never returned an unclipped, validated skill |
| Held-out | Skipped entirely (`heldout_grades: []`) |
| Tokens spent | 37,978 (per summary) |
| Model calls | 15 |
| Errors | None recorded (`error: null`) |
| Escalation | **Did not trigger.** `classify_stop()` in `scripts/demo_trial.py` only escalates `decision_limit`, `primitive_limit`, learning-token/-call exhaustion, held-out decision/primitive limits, or the whole-run deadline. Neither the per-episode `wall_time_limit` nor a truncated Builder reply is an escalatable `LimitKey`, so `run_demo_trial()` returned after this single attempt with `completed: false` and no limit raised. |

**Training outcome:** did not complete (`goal_completed`/`terminal_state` not
reached; episode ended on its per-episode wall clock).
**Accepted skill:** none.
**Held-out:** not run (0 cells graded).

## Minecraft redstone trial

- Run id: `minecraft-redstone-20260918T081617Z`
- Command: `cd /home/nathan/noob-agent && NOOB_AGENT_SANDBOX_MODE=local uv run --env-file .env python scripts/run_minecraft_redstone_demo.py`
- Records: `.noob-agent/minecraft-redstone-20260918T081617Z.sqlite3` (SQLite), `.noob-agent/minecraft-redstone-20260918T081617Z.json` (JSON summary)

### Attempt 1 (pre-escalation)

Source: `minecraft-redstone-20260918T081617Z.json`, cross-checked against
`minecraft-redstone-20260918T081617Z.sqlite3` (`experiment`, `episode_outcome`,
`model_call` tables; query used: `select experiment_id, purpose, action_id,
finish_reason, input_tokens, output_tokens, max_output_tokens, latency_ms,
error, started_at from model_call order by started_at`).

| Field | Value |
|---|---|
| Sequence id | `minecraft-redstone-20260918T081617Z-s01` |
| Limits (`docs/redstone-demo.md`) | 12 decisions, 24 primitives, 120 s per attempt, 4,000 max output tokens per Action call; Builder 1,000,000 output tokens, 1 repair; whole run 600 s deadline, 1,100,000-token call-start ceiling |
| Overall run status | `TimeoutError` (whole-run 600 s deadline) |
| Cold-attempt stop reason | `wall_time_limit` (the attempt's own 120 s clock, not the whole-run deadline) |
| Cold-attempt decisions / primitives used | 6 / 12 decisions, 4 / 24 primitives |
| Cold-attempt lamp lit | **No.** The cold episode never reached a terminal/success state (`episode_outcome.terminal = 0`); the run stopped on the 120 s clock before any success message. |
| Cold-attempt Action calls | 6, `max_output_tokens=4000`; 2 of 6 hit `finish_reason=length` (`a_0005` at 38,456 ms latency, `a_0006` at 59,360 ms latency) |
| Builder call | 1 `build` call, `max_output_tokens=1,000,000`, `latency_ms=463,746` (~7.7 min), `error="CancelledError: "`, `finish_reason=None`, `output_tokens=None` — the call was still in flight when the whole-run 600 s `asyncio.timeout` fired and cancelled it |
| Builder accepted | **No** (`builder: null` in the JSON summary — the Builder call never returned) |
| Reuse/skill attempt | **Did not run** (`reuse: null` — the script only starts a reuse episode after Builder acceptance) |
| Skill actually used | N/A — no skill was ever accepted |
| Lamp lit with skill | N/A — no reuse attempt ran |
| Tokens by phase | cold: 20,422; builder: 1,003,230 (per `tokens_by_phase` in the JSON summary). This is a **charged upper bound, not actual output**: `_call_tokens()` in `scripts/run_minecraft_live_smoke.py` (reused via `run_minecraft_easy_live_smoke.py`'s `_call_tokens = _live_smoke._call_tokens`, and by `run_minecraft_redstone_demo.py`'s `m._call_tokens`) falls back to `record.max_output_tokens + (len(system) + len(prompt)) // 4` whenever `input_tokens`/`output_tokens` are `None` — exactly the case here, since the Builder call was cancelled before it reported usage. 1,000,000 (max_output_tokens) + 3,230 ≈ the reported 1,003,230, i.e. ≈12,920 prompt+system characters charged at the conservative 4-chars-per-token estimate, not tokens the model actually produced. |
| Errors | 1: the Builder call's `CancelledError` |
| Which limit stopped it | The whole-run 600 s deadline cancelled the in-flight Builder call, which had been given no cap other than the requested 1,000,000-token budget; the cold attempt itself was separately stopped by its own 120 s per-attempt clock, well before its 12-decision/24-primitive budget |

**Cold attempt:** lamp not lit, stopped on the 120 s per-attempt wall clock.
**Builder/skill:** no skill accepted — the Builder call was cancelled by the
600 s whole-run deadline after running ~7.7 minutes on a 1,000,000-token
output budget.
**Skill (learned) attempt:** did not run.

## Tokens and calls per phase

| Run | Phase | Tokens | Calls |
|---|---|---|---|
| Doom `doom-demo-trial-20260918a-a01` | training (action) | sum of 14 calls' input+output, ≈ within the 37,978 total | 14 |
| Doom `doom-demo-trial-20260918a-a01` | builder | included in the 37,978 total | 1 |
| Minecraft `…-s01-training` | cold | 20,422 | 6 |
| Minecraft `…-s01-training` | builder | 1,003,230 (cancelled before completion) | 1 |
| Minecraft `…-s01-reuse` | — | did not run | 0 |

## Wall time

- Doom attempt 1: training episode ran 08:15:36–08:18:46 UTC (~190 s to its
  outcome record), the whole script exited shortly after (summary present by
  08:20:59 UTC per the monitor). Well under the 3,600 s whole-run deadline.
- Minecraft redstone attempt 1: cold episode ran 08:16:19–08:18:33 UTC (~134 s
  including its 120 s clock), the Builder call ran ~08:18:35–08:26:19 UTC
  before cancellation (~464 s), and the whole script exited at the 600 s
  deadline (~08:26:19 UTC).

## Cost estimate

Not derived. No per-token pricing for `deepseek-ai/DeepSeek-V4-Flash-0731` via
`wandb-inference` was available to me in either checkout's configuration or
committed docs, so a dollar figure would be a guess. If pricing is added to
the repo or provided, this section should be filled in from the token totals
above.

## Weave cross-check

A read-only query against the configured W&B project
(`nathanweldegiorgis731-minerva-university/Noob-agent`) via
`weave.init(project).get_calls(limit=5)` returned nested
`noob_agent.episode` and `noob_agent.step` ops, confirming episodes and steps
are traced in Weave as expected. I did not exhaustively match every DB row to
a Weave call id in this pass; the spot check confirms the trace pipeline is
live and nesting is present.

## Anomalies flagged

1. **Escalation gap (Doom):** the demo-trial escalation logic
   (`scripts/demo_trial.py::classify_stop`) does not recognize a per-episode
   `wall_time_limit` or a truncated Builder reply (`truncated_reply`) as
   escalatable limits, so attempt 1 ended after a single try with no limit
   raised, even though the underlying cause (majority of Action calls
   truncated at `finish_reason=length`, `max_output_tokens=1024`) is squarely
   a capacity problem an escalation should address.
2. **Escalation gap (Minecraft):** the redstone runner (outside the 26.3
   escalation machinery entirely — it is a single-shot comparison script, not
   a `LearningSequence`) has no cap on the Builder call itself beyond the
   whole-run 600 s deadline; requesting 1,000,000 output tokens produced a
   ~7.7-minute in-flight call that was cancelled by the deadline rather than
   completing or failing cleanly.
3. **Truncation is the dominant failure mode, not budget exhaustion.** In
   both trials, decision/primitive/token budgets were nowhere near exhausted
   (Doom: 14/60 decisions; Minecraft cold: 6/12 decisions) — the real
   constraint was per-call output-token caps (Doom Action calls at 1,024)
   and per-episode/whole-run wall clocks, which is why the coordinator is
   having the 26.3 author add wall-time and per-call-cap escalation.
4. **Minecraft Builder token total is a charged upper bound, not actual
   output.** `tokens_by_phase.builder = 1,003,230` comes from `_call_tokens()`
   (`scripts/run_minecraft_live_smoke.py`, shared with the easy-smoke and
   redstone runners), which falls back to `max_output_tokens + (len(system) +
   len(prompt)) // 4` whenever a call reports no `input_tokens`/`output_tokens`
   — the case here, since the Builder call was cancelled before reporting
   usage. This is the section 21 "Budget accounting" conservative-charge rule
   working as designed, not a data-integrity bug; it should not be read as
   1,003,230 tokens the model actually produced.

## Known limitations

- Non-benchmark: neither run enters a scorecard or comparison; both are
  labeled (`RUN_KIND` / `CONDITION`) and stored in separate, Git-ignored
  databases.
- Local subprocess sandbox only (`NOOB_AGENT_SANDBOX_MODE=local`); this is
  not a hardened isolation claim.
- One run per game so far. No statistical conclusions should be drawn from a
  single attempt of each.
- Neither trial reached its sample-task completion criterion in this pass;
  both are pending re-runs after the escalation fix.

## Minecraft redstone trial — next attempt

Pending the escalation fixes and a new run id from the coordinator. This
section will be updated with attempt 2's lamp-lit status (cold and with
skill), skill acceptance, and actual skill-use count once available.
