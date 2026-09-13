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
