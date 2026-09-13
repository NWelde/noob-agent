# Loop Optimization Scorecards

This is the running record for `hackathon_plan.md` section 22. Every step that
changes the loop adds a row measured with `scripts/loop_bench.py`, so each
optimization is judged against the same numbers.

```sh
# Run fixed-seed headless Doom sequences and score them.
NOOB_AGENT_SANDBOX_MODE=local uv run --env-file .env python scripts/loop_bench.py run --sequences 5

# Score an existing database without modifying it.
uv run python scripts/loop_bench.py score --database .noob-agent/doom-live-demo.sqlite3
```

Outputs go to the Git-ignored `.noob-agent/loop-bench/` as `<name>.json` and
`<name>.md`.

## How the metrics are counted

- **Unusable Action replies:** decisions recorded as `unusable_reply`, divided
  by all decisions in training and held-out episodes.
- **Capped calls:** calls whose provider `finish_reason` is `length`.
- **Latency:** nearest-rank p50 and p90 of Action model-call latency, so every
  reported value was actually observed.
- **Accepted skill:** from the Builder result when the bench ran the sequence.
  A score read only from a database infers acceptance from whether held-out
  episodes ran, and labels it `inferred`.
- **Learning tokens and calls:** training Action calls plus every Build and
  Repair call, checked against the `eval_protocol.md` ceilings of 40,000
  training Action tokens, 12,000 per build, 8,000 per repair, 60,000 learning
  tokens, and 22 learning calls. A call whose provider reported no usage is
  flagged `usage_unknown` rather than counted as free.
- **Wall time:** first recorded start to last recorded finish in the sequence.
- **Goal completion:** only known for bench runs, because a database holds no
  private grade.

## Baseline (before section 22)

Scored read-only on 2026-09-13 from the Git-ignored live databases. All used
`deepseek-ai/DeepSeek-V4-Flash-0731` on W&B Inference with thinking on, the
labeled local subprocess sandbox, and the section 17 single-pass sequence. The
database hashes were unchanged after scoring.

| Database | Sequences | Accepted | Unusable replies | Capped Action | Action p50 / p90 ms | Builder calls capped | Median wall s | Max learning tokens | Over a ceiling |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `doom-learning-live` (schema 2) | 1 | 0 | 13/20 (65%) | not recorded | not recorded | not recorded | 64 | not recorded | not recorded |
| `doom-live-demo` (sections 20-21, 6k-32k Builder cap) | 4 | 3 (inferred) | 103/223 (46%) | 103/224 | 5,399 / 7,214 | 1 of 4 | 413 | 59,476 | 3 |
| `doom-hypothesis-500k-20260913` (32k Builder cap) | 2 | 0 (inferred) | 20/40 (50%) | 20/40 | 5,498 / 7,166 | 1 of 3 | 225 | 78,306 | 2 |
| `doom-hypothesis-1m-headed-20260913` (100k Builder cap) | 6 | 0 (inferred) | 52/100 (52%) | 52/100 | 5,749 / 7,192 | 5 of 8 | 473 | 161,490 | 5 |

Observations:

- Every capped Action call was also an unusable reply, and every unusable reply
  with a recorded call was capped. Truncation, not format, causes the waste.
- The 1M run's sixth sequence was cancelled by the token budget before its
  training episode started, so it has no records.
- The `doom-live-demo` sequences that accepted a skill used a 32,000-token
  Builder cap. Their builds exceeded the 12,000-token protocol ceiling, so they
  do not meet the protocol.

## After each step

| Step | Bench run | Unusable replies | Action p50 ms | Accepted | Builder capped | Median wall s | Max learning tokens |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 22.F (no loop change) | baseline above | | | | | | |
| 22.A native tool calls (superseded) | `loop-bench-22a` | 0/100 | 634 | 0/5 | 5/5 | 50 | 71,133 (training Action tokens over ceiling) |
| 22.A trimmed tool calls (superseded) | `loop-bench-22a-r2` | 0/100 | 620 | 0/5 | 5/5 | 47 | 53,360 (training Action tokens over ceiling) |
| 22.A JSON-schema decisions | `loop-bench-22a-r3` | 0/100 | 561 | 0/5 | 5/5 | 45 | 32,251 (no ceiling exceeded) |
| 22.B grounded Builder, thinking off | `loop-bench-22b` (stopped after 1 of 5 sequences) | 0/20 | 528 | 0/1 | 0/2 | 23 | 31,169 (no ceiling exceeded) |
| 22.C converging repairs | `loop-bench-22c` | 0/190 | 517 | **4/5** | 0/9 | 42 | 32,623 (no ceiling exceeded) |
| 22.E parallel held-out and validation | `loop-bench-22e` | 0/110 | 507 | 1/5 | 0/10 | **17.5** | 32,616 (no ceiling exceeded) |
| 22.D multi-round loop (`--rounds 3 --curve`, basic-v2) | `loop-bench-22d` | 0/311 | 570 | 3/5 | 0/11 | 34.7 | 90,214 (multi-round ceiling 300,000, not exceeded) |

22.A runs use Action thinking off and Builder thinking at the provider default
(on), so every build still ends at its 6,000-token cap and no skill is accepted.
Step 22.B turns Builder thinking off. Median Action input was 3,156 tokens with
native tools, 2,273 after trimming, and 1,188 with the JSON schema. In the last
run the model chose `attack` 61 times, `turn_right` 23, `turn_left` 7, `observe`
7, and `move_forward` 2; the baseline almost never turned.

In `loop-bench-22b` the first build used the real API, took 2.1 s and 550 output
tokens, and was rejected because it claimed success in the missing-target
negative case. The repair fixed exactly that check in 2.2 s and 546 tokens, but
renamed the skill in its metadata. The registry refused a repair whose parent
has a different name and raised `UnknownSkillVersionError`, which ended the
bench. Step 22.C makes a renamed repair a public rejection instead of a crash.

`loop-bench-22c` is the first run where learning shows up in held-out results.
Cold training completed its goal in 0 of 5 sequences. With the accepted skill,
held-out episodes completed 9 of 24: 6 of 6 for sequence 1's skill, 1 of 6 for
each of sequences 2, 4, and 5, and none run for sequence 3, whose repairs ran out
(`repair_budget_exhausted`). Most held-out failures stopped at the primitive
limit after three skill uses, so the accepted skills vary widely in quality. That
is what step 22.D's practice rounds are meant to improve.

`loop-bench-22e` cut the median sequence to 17.5 s by playing 6 held-out cells at
once and running validation in concurrent phases. Only 1 of 5 sequences accepted
a skill, against 4 of 5 in 22.C. The rejections were genuine skill defects of the
same kinds 22.C saw: 4 `contract/incorrect_action_accounting` (the result reported
one more primitive than the replay charged) and 1
`negative_case/missing_target_claimed_success`. They were not timeouts, and the
concurrency-1 and concurrency-8 validation tests reach identical verdicts. Five
sequences are too few to separate this variance from a real change. Wrong
primitive accounting is now the most common rejection and a clear target for 22.D.

## Multi-round loop (22.D)

`loop-bench-22d` ran 5 sequences with up to 3 refinement rounds over the practice
seeds 20260913 and 20260915, then evaluated the kept versions on held-out:

| Sequence | Rounds | Stop | Practice (public) | Held-out goals | Learning tokens / calls |
| --- | --- | --- | --- | --- | --- |
| s01 | incumbent only | `perfect_practice` | 2 of 2 terminal | **5 of 6** | 35,598 / 26 |
| s02 | none | `no_skill` | none | none run | 32,245 / 22 |
| s03 | incumbent, refinement failed validation | `no_improvement` | 0 of 2 | 1 of 6 | 66,017 / 47 |
| s04 | incumbent, refinement did not improve (rejected) | `no_improvement` | 0 of 2, then 0 of 2 | 2 of 6 | 90,214 / 70 |
| s05 | none | `no_skill` | none | none run | 30,162 / 22 |

- **The loop works as a mechanism.** It kept an incumbent, stopped without a
  refinement when practice was perfect, rejected a refinement that failed
  validation, rejected a validated refinement that did not score better, and
  evaluated held-out only after learning ended.
- **No refinement improved a skill yet.** With the stop-after-one-round-without-
  improvement rule, no sequence logged 3 rounds, so the "at least 3 rounds"
  target was not met.
- **Practice predicts held-out.** A perfect practice score came with 5 of 6
  held-out goals, and a zero practice score with 1 and 2 of 6. The public practice
  score agreed with the private grade on every practice episode (agreement 1.0).

## Minecraft confirmation (22.G)

On 2026-09-13 one cold Minecraft training episode (`resonator-training-v1`, seed
20260912) ran through the unchanged runner and live Mineflayer connector with the
22.A Action agent (JSON-schema decisions, thinking off), on the local server.
Recorded as `mc-confirm-22g-20260913T125747Z`:

| Metric | Value |
| --- | --- |
| Unusable Action replies | 0 of 10 |
| Action calls ending at their cap | 0 of 10 |
| Decision latency p50 / p90 | 690 / 1,477 ms |
| Median Action input tokens | about 2,317 |
| Episode | `repeated_failure` after 10 decisions and 10 primitives, 11.4 s |

- **The agent used real controls.** It called `observe` three times with a growing
  radius, moved to reach an unreachable object, inspected it successfully, then
  inspected a vanished object three times, which triggered the repeated-failure
  stop.
- **Decisions are reliable and fast on the second game**, with no Minecraft code
  change.
- **Token risk.** Minecraft observations are larger. At about 2,300 input tokens a
  decision, a full 20-decision training episode would use about 46,000 tokens,
  above the 40,000-token training ceiling. The observation or history size needs
  a separately approved change before a Minecraft benchmark run.
- **Not confirmed on Minecraft:** the Builder, practice rounds, and held-out. Only
  the cold Action loop was run.

## Accounting-clear Builder prompt (24a)

`loop-bench-24a` (5 single-pass sequences, basic-v1) after the API reference began
stating that `context.observe()` is free and that `primitive_actions_used` must
equal the sum of `result.primitive_actions_charged`:

| Metric | `loop-bench-22e` | `loop-bench-24a` |
| --- | --- | --- |
| Sequences with an accepted skill | 1 of 5 | **5 of 5** |
| `incorrect_action_accounting` rejections | 4 | **0** |
| Repairs needed | 5 | 2 (both `missing_target_claimed_success`, both fixed) |
| Builder calls ending at their cap | 0 of 10 | 0 of 7 |
| Unusable Action replies | 0 of 110 | 0 of 370 |
| Held-out goals completed | 1 of 6 | 8 of 30 |
| Median sequence wall time | 17.5 s | 37.7 s (more held-out cells ran) |

Held-out goals by sequence: 1, 1, 1, 1, and 4 of 6. Skills are now accepted
reliably, but most still do not solve held-out, which is what step 24b's longer
practice loop targets.

## Multi-round tuning (24b)

Step 24b changed the multi-round loop to stop after 2 consecutive rounds without
a kept version, moved practice to the four `basic-v3` seeds, and recorded
refinement calls with purpose `refine`. Both runs used `--rounds 3 --curve` on
`basic-v3`.

The first run, `loop-bench-24b`, took 2 of 5 sequences over the 140-call learning
ceiling (156 and 168 calls): the loop started a practice batch that the remaining
budget could not cover. The loop now starts a practice batch only when its worst
case, every practice episode using its whole 12-decision budget at the largest
average cost per call so far, fits both learning budgets. A validated challenger
whose practice cannot fit is rejected with stop `learning_budget`. The rerun,
`loop-bench-24b-r2`, is the result below.

| Sequence | Refinement rounds | Kept | Stop | Practice (private goals) | Held-out goals | Learning tokens / calls |
| --- | --- | --- | --- | --- | --- | --- |
| s01 | 0 | none | `perfect_practice` | 4 of 4 | **5 of 6** | 38,017 / 28 |
| s02 | 2 | none | `no_improvement` | 0, 0, 0 of 4 | 1 of 6 | 89,024 / 63 |
| s03 | 1 | none | `learning_budget` | 2, then 0 of 4 | 0 of 6 | 145,700 / 111 |
| s04 | 1 | v3 (fewer primitives, same 0 terminal rate) | `learning_budget` | 0, 0 of 4 | 1 of 6 | 149,322 / 106 |
| s05 | 1 | v2 (fewer primitives, same 0 terminal rate) | `learning_budget` | 0, 0 of 4 | 1 of 6 | 131,768 / 95 |

| Metric | `loop-bench-22d` | `loop-bench-24b-r2` |
| --- | --- | --- |
| Sequences with an accepted skill | 3 of 5 | 5 of 5 (includes the 24a prompt) |
| Most refinement rounds in a sequence | 1 | 2 |
| Refinements kept | 0 | 2, both on the primitive tie-break |
| Practice agreement with the private grade | 1.0 | 1.0 in every sequence |
| Held-out goals completed | 8 of 18 (3 sequences ran held-out) | 8 of 30 |
| Sequences over a protocol ceiling | 0 | 0 |
| Unusable Action replies | 0 of 311 | 0 of 597 |
| Median sequence wall time | 34.7 s | 65.4 s |

- **Patience 2 works but rarely binds.** Only s02 logged two rounds without a
  kept version. Three sequences stopped on the learning budget first.
- **The call budget allows one challenger.** A four-seed practice batch can take
  48 Action calls. Training, the build, and the incumbent's practice leave room
  for at most one full challenger practice inside 140 calls, so no sequence can
  log 3 rounds.
- **Kept refinements did not help held-out.** Both kept versions scored 0 on
  practice and won only by using fewer primitives. Their `--curve` held-out
  results equalled their incumbents' (1 of 6 each).
- **Practice still predicts held-out.** Perfect practice again came with 5 of 6
  held-out goals, and practice agreed with the private grade on every episode.

Reaching 3 rounds within the protocol needs a separately approved change, such
as a smaller practice decision budget, two practice seeds per round, or a larger
learning call budget.

## Compact Action observation (24c)

Step 24c renders the Action prompt's observation JSON without null values or
empty collections, with compact separators and floats rounded to two decimals,
and cuts history subgoals to 100 characters. No field with a value and no object
is removed.

**Minecraft acceptance not met.** One cold `resonator-training-v1` episode (seed
20260912) on the local server, recorded as `mc-confirm-24c-20260913T154513Z`:

| Metric | `mc-confirm-22g` (before) | `mc-confirm-24c` |
| --- | --- | --- |
| Decisions | 10 (`repeated_failure`) | 20 (`decision_limit`) |
| Unusable Action replies | 0 of 10 | 0 of 20 |
| Median Action input tokens | about 2,317 (1,212 to 3,003) | **2,371** (1,140 to 2,408) |
| Median over the first 10 decisions | 2,612 | 2,146 |
| Input tokens over the first 10 decisions | 23,168 | 19,959 (14% fewer) |
| Observation text at decision 10 | 4,288 characters | 2,992 characters (30% fewer) |
| Input tokens over the episode | not reached | 43,752 |

- The observation shrank by about 30%, but the system prompt, primitive list,
  and history did not. Once the history window is full, each decision takes
  about 2,370 tokens.
- The target was a median of at most 2,000 tokens, and a full 20-decision
  episode still uses 43,752 tokens, above the 40,000-token training ceiling.
  A Minecraft benchmark run still needs a further approved change, such as a
  shorter history window, shorter primitive descriptions, fewer visible-object
  properties, or a per-game token ceiling.

**Doom acceptance met.** `loop-bench-24c` (5 single-pass sequences, basic-v1):

| Metric | `loop-bench-24a` | `loop-bench-24c` |
| --- | --- | --- |
| Unusable Action replies | 0 of 370 | **0 of 268** |
| Action calls ending at their cap | 0 of 370 | 0 of 268 |
| Median Action input tokens | 1,190 | 1,138 |
| Sequences with an accepted skill | 5 of 5 | 5 of 5 |
| Held-out goals completed | 8 of 30 | 6 of 30 |
| Median sequence wall time | 37.7 s | 35.7 s |
