# Demo trials (non-benchmark)

**Status: in progress.** This document covers `hackathon_plan.md` step 26.4
non-benchmark demo trials across three rounds: the pre-fix attempt of each
game (Doom `…a`, Minecraft `…081617Z`), a post-escalation-fix Doom re-run and
a 4-attempt escalating Minecraft run (Doom `…b`, Minecraft `…084449Z`), and a
further Doom re-run after the escalation logic was corrected again plus a
second escalating Minecraft run after a repair-cap fix (Doom `…c`, PR #76;
Minecraft `…113854Z`, PR #82). Each round is documented as it completed; the
"Anomalies flagged" and "Known limitations" sections below span all rounds.

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

### Attempt 2 (post-escalation-fix — `doom-demo-trial-20260918b`)

- Command: `cd /home/nathan/noob-agent-trial && NOOB_AGENT_SANDBOX_MODE=local uv run --env-file .env python scripts/demo_trial.py --run-id doom-demo-trial-20260918b`
- Records: `.noob-agent/demo-trial.sqlite3` (SQLite, shared file — filtered by `experiment_id like 'doom-demo-trial-20260918b%'`), `.noob-agent/demo-trials/doom-demo-trial-20260918b.json`
- Source: read-only via `sqlite3.connect("file:...?mode=ro", uri=True)`. Queries used: `select experiment_id, count(*) from model_call where experiment_id like 'doom-demo-trial-20260918b%' group by experiment_id`; `select purpose, count(*), sum(finish_reason='length') from model_call where experiment_id like 'doom-demo-trial-20260918b%' group by purpose`; `select purpose, action_id, finish_reason from model_call where experiment_id='doom-demo-trial-20260918b-a01' and purpose in ('build','repair')`.

This run escalated across 5 attempts as `training_decision_budget` /
`training_primitive_budget` / `learning_call_budget` were raised (per the
26.3 escalation fix; each attempt's `limits` block in the JSON summary
confirms the raised values).

| Attempt | Limits raised vs. prior | `stop_reason` | `calls_spent` | `tokens_spent` | Held-out |
|---|---|---|---|---|---|
| a01 | training_decision_budget=60 (baseline) | `decision_limit` | 62 | 82,717 | **Ran: 6 cells graded, 1 `goal_completed=true`** (`doom-basic-heldout-a`, seed 102, `terminal_state`) |
| a02 | training_decision_budget 60→120 | `learning_budget_exhausted` | 120 | 150,801 | none (`heldout_grades: []`) |
| a03 | training_decision_budget 120→240 | `learning_budget_exhausted` | 120 | 156,337 | none |
| a04 | training_primitive_budget 120→240 | `learning_budget_exhausted` | 173 | 214,413 | none |
| a05 | learning_call_budget 66→132 | `learning_budget_exhausted` | 229 | 293,795 | none |

Key finding: **attempt a01 already accepted a skill and ran held-out
grading**, even though its own `stop_reason` is `decision_limit` (the
training episode hit its decision cap after the skill had already been built
and accepted — `stop_reason` reflects the training episode's own stop, not
whether a skill was produced). Verified directly against the DB: a01's
`build` call has `finish_reason='stop'` (not truncated) and is followed by
one `repair` call, also `finish_reason='stop'` — i.e. the Builder's first
draft needed one clean repair pass, not a truncation-forced retry. a02
onward never got a skill to build (`stop_reason=learning_budget_exhausted`
before the training episode reached a terminal state to hand off to the
Builder).

Across all 5 attempts: 704 training-purpose `model_call` rows
(`action`: 883 total training decisions across attempts, `build`: 1, `repair`:
1, summed per-attempt as 62+120+120+173+229=704) plus 181 held-out calls in
a01 (885 rows total for the run). **0 of the 704 training calls, and 0 of the
885 total calls, have `finish_reason='length'`** — no truncation this round,
confirming the `action_max_output_tokens=4000` / `builder_max_output_tokens=
32000` caps (raised from attempt-a's 1,024 / 6,000) were sufficient. The
run never reached `completed: true` overall — a02–a05 kept re-attempting
training under progressively raised budgets but never got another skill
built (the JSON's top-level `completed` is `false`), stopping only because
this coordinator ended the round at 5 attempts, not because a limit
resolved it.

**Training outcome (a01):** skill built, accepted (1 repair pass, no
truncation).
**Held-out (a01):** 1/6 goals completed (`doom-basic-heldout-a` seed 102);
the other 5 cells stopped on `decision_limit` (4) or hit no completion.
**a02–a05:** stopped on `learning_budget_exhausted` before another skill was
built; each round's calls were consumed entirely by more training Action
calls under the raised decision/primitive budgets, not by Builder/repair
activity.

### Attempt 3 (post-fix, PR #76 — `doom-demo-trial-20260918c`)

- Checkout: PR #76 head (`fix/26-4-...` escalation fixes: call budget
  coupled to decisions, completion defined as skill accepted + ≥1 held-out
  goal, repairs get the build cap, up to 6 escalations).
- Command: `cd /home/nathan/noob-agent-trial && NOOB_AGENT_SANDBOX_MODE=local uv run --env-file .env python scripts/demo_trial.py --run-id doom-demo-trial-20260918c`
- Records: `.noob-agent/demo-trial.sqlite3` (filtered `experiment_id like 'doom-demo-trial-20260918c%'`), `.noob-agent/demo-trials/doom-demo-trial-20260918c.json`
- Weave: https://wandb.ai/nathanweldegiorgis731-minerva-university/Noob-agent/weave/calls?filter=%7B%22traceRootsOnly%22%3Atrue%7D&peekPath=&query=doom-demo-trial-20260918c-a01

| Field | Value |
|---|---|
| Sequence id | `doom-demo-trial-20260918c-a01` |
| Overall `completed` | **`true`** — the run finished in a single attempt |
| Limits | training_decision_budget=60; training_primitive_budget=120; learning_call_budget=66; learning_token_budget=1,000,000; action_max_output_tokens=4,000; builder_max_output_tokens=32,000; `max_repairs=1` (new field vs. attempt b) |
| Training stop reason | `decision_limit` (training episode hit its decision cap after the skill was built and accepted) |
| Overall `stop_reason` | `completed` |
| Skill accepted | **Yes** — 1 `build` call, `finish_reason='stop'`, no repair needed this time (only 1 `build` row in `model_call` for a01-training, no `repair` row) |
| Held-out | 6 cells graded, **1 `goal_completed=true`** (`doom-basic-heldout-a`, seed 102, `terminal_state`); the other 5 stopped on `decision_limit` (1) or `primitive_limit` (4) without completing |
| Calls spent | 61 (matches `model_call` count for `doom-demo-trial-20260918c-a01-training`: 60 `action` + 1 `build`) |
| Tokens spent | 79,288 (matches `sum(input_tokens+output_tokens)` for `experiment_id='doom-demo-trial-20260918c-a01-training'` exactly) |
| Truncated calls | **0** — `action_truncated_calls=0`, `builder_truncated_calls=0` in the summary; confirmed via SQL: 0 of 61 training calls and 0 of 122 held-out calls have `finish_reason='length'` (183 total calls for the run) |
| Total tokens (training + held-out) | 242,688 (79,288 training + 163,400 held-out, summed from `model_call`) |
| Escalation | Did not need to trigger — attempt a01 satisfied the new completion definition (skill accepted + ≥1 held-out goal) on the first try |
| Errors | None (`error: null`) |

**Training outcome:** skill built and accepted on the first attempt, no
repair pass needed, no truncation.
**Held-out:** 1/6 goals completed.
**Result:** `completed: true` in a single attempt — the escalation-fix run
did what attempt a01 of round b already showed was possible (skill
acceptance without truncation), and this time the run's own `completed`
flag reflects it correctly.

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

### Attempt 2 — 4-attempt escalating run (`minecraft-redstone-20260918T084449Z`)

- Command: `cd /home/nathan/noob-agent-mctrial && NOOB_AGENT_SANDBOX_MODE=local uv run --env-file .env python scripts/run_minecraft_redstone_demo.py --escalate`
- Records: `.noob-agent/minecraft-redstone-20260918T084449Z-index.json` (round summary) plus per-attempt `-a01.json`..`-a04.json` / `-a01.sqlite3`..`-a04.sqlite3`
- Source: read-only, per-attempt JSON plus `select purpose, max_output_tokens, finish_reason, output_tokens from model_call where purpose in ('build','repair') order by started_at` against each attempt's own SQLite file.

The Builder cap was escalated per attempt (32k → 64k → 128k → 128k, with
`wall_time_ms` also doubled for a04); the repair cap stayed **fixed at
3,000 tokens** in this checkout regardless of the Builder cap escalation —
this is the bug PR #82 (see the next round) later fixes.

| Attempt | builder_cap | Cold lamp lit | Cold decisions/primitives | Build call | Repair call | Builder accepted |
|---|---|---|---|---|---|---|
| a01 | 32,000 | **Yes** | 11 / 8 | `finish_reason=length`, `output_tokens=32000` (**truncated at the build cap**) | none attempted | No — `truncated_reply` |
| a02 | 64,000 | **Yes** | 7 / 5 | `finish_reason=stop`, `output_tokens=22729` (clean) | `max_output_tokens=3000`, `finish_reason=length`, `output_tokens=3000` (**truncated at the fixed repair cap**) | No — `truncated_reply` |
| a03 | 128,000 | **No** (`wall_time_limit`) | 14 / 12 | `finish_reason=stop`, `output_tokens=28848` (clean) | `max_output_tokens=3000`, `finish_reason=length`, `output_tokens=3000` (**truncated at the fixed repair cap**) | No — `truncated_reply` |
| a04 | 128,000 (wall_time_ms doubled to 720,000) | **Yes** | 12 / 8 | `finish_reason=stop`, `output_tokens=39375` (clean) | none attempted | No — `unusable_reply` (rejected on validation, not truncation; no repair invoked) |

Tokens by phase (from each attempt's JSON `tokens_by_phase`): a01 cold
60,124 / builder 36,203; a02 cold 25,681 / builder 33,748; a03 cold 74,262 /
builder 40,106; a04 cold 72,245 / builder 43,587. `reuse_lamp_lit=false` and
`skill=null` in all 4 attempts — **the Builder was never accepted across
the whole round**, so no skill-reuse episode ever ran.

**What actually blocked acceptance, once the Builder cap itself was raised
high enough:** a02 and a03 both produced a clean (`finish_reason=stop`)
first Builder draft, well under their raised build caps, but the repair
pass — needed because the first draft failed validation — was capped at a
**fixed 3,000 tokens regardless of the escalated build cap**, and repairing
a skill this size needs more than 3,000 tokens, so the repair reply itself
was truncated and the Builder was never accepted. a01's first draft was
truncated outright (build cap too low at 32k). a04's first draft was clean
and within cap but rejected as `unusable_reply` — a validation failure, not
a truncation — and no repair was attempted for it in this checkout.

**Cold lamp lit:** 3 of 4 attempts (a01, a02, a04); a03 ran out of its
per-attempt wall clock before lighting it.
**Builder/skill accepted:** 0 of 4 attempts.
**Skill-reuse attempt:** did not run in any attempt (no accepted skill to
reuse).

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
- Doom: 1 successful full round (`…c`, 1 attempt) after two escalation-logic
  fixes; 1 escalating round (`…b`, 5 attempts) that produced a skill and
  held-out grading in its first attempt but never re-triggered Builder
  activity in later attempts; 1 pre-fix failed round (`…a`). No statistical
  conclusions should be drawn from this handful of runs.
- Minecraft: 1 pre-escalation failed attempt (`…081617Z`) plus 1
  4-attempt escalating round (`…084449Z`) that never got a Builder skill
  accepted, root-caused (PR #82) to a fixed 3,000-token repair cap that
  didn't scale with the escalated build cap, plus separate validation
  failures (a02: `TypeError: 'PublicPosition' object is not subscriptable`;
  a03: forbidden `getattr` call; a04: a zero-width space U+200B embedded in
  the metadata JSON plus mismatched brackets in the generated source). A
  second escalating round under the PR #82 fix (repair cap escalating
  32,000→128,000 separately from the build cap; unusable replies retried at
  the same limits) is covered below.

## Minecraft redstone trial — escalating round 2 (PR #82 fix)

See the dedicated section below once run `minecraft-redstone-20260918T113854Z`
completes.
