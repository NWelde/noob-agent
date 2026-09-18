# Demo video script (≤3 minutes)

Judges only see this video and the repo — there is no live demo in Part 2.
Every shot below cites the real recorded run it comes from. **Claude cannot
record video or run live model calls; the requester records this, following
the commands here exactly, from a freshly reset state with the listed
windows pre-opened.**

Flags below were verified against each script's own `--help` on this branch
(2026-09-18); re-check after any script changes.

## Before recording

- [ ] Confirm `docs/demo-trials.md` still lists `doom-demo-trial-20260918c` as
  the most recent completed, non-escalated Doom demo trial. If a newer trial
  exists, swap its run ID and numbers in everywhere below.
- [ ] Confirm whether [PR #76](https://github.com/NWelde/noob-agent/pull/76)
  (`scripts/demo_trial.py`) has merged to `main`. As of this writing it has
  not — `doom-demo-trial-20260918c` was produced on that PR's branch, and its
  SQLite/JSON records live outside this repo
  (`/home/nathan/noob-agent-trial/.noob-agent/`, Git-ignored, machine-local).
  Until the PR merges, replay that specific database from a checkout that
  still has it, or re-run `scripts/demo_trial.py` after merge to get a fresh,
  in-repo-reachable run and update the run ID here.
- [ ] Close unrelated windows and browser tabs; hide `.env`; enlarge the
  terminal font; have the repo open in an editor with the README visible.
- [ ] Pre-open: a terminal in the repo root, a browser tab for the Weave
  project, and (for the Minecraft shot) a Minecraft 1.21.1 client joined to
  `127.0.0.1:25566`.

## Shot list

| Time | Shot | Say | Command / on-screen |
| --- | --- | --- | --- |
| 0:00–0:15 | Title card, then a terminal showing the repo README top (CI badge visible) | "The question: can a model turn experience in an unfamiliar game into reliable software it can reuse — in a way you could run in CI?" | Scroll README.md from the top |
| 0:15–0:40 | Replay of the cold (primitive-only) training attempt from `doom-demo-trial-20260918c-a01` | "It starts as a noob: primitives only, no hints. Here's a recorded run — not live — replayed from its saved database." | **Label on screen: "Recorded run `doom-demo-trial-20260918c-a01`, replayed."** `uv run python scripts/replay_doom_episode.py --database <path-to-the-run's-sqlite3> --episode-id <training-episode-id>` (fill in from the JSON summary; add `--windowed` if not recording full screen) |
| 0:40–1:10 | The run's Weave trace (browser), then the accepted skill's source in the terminal | "From that trace, a second model call — the Builder — wrote a Python skill and it passed sandboxed validation on the first try, no repair needed." | Open the Weave URL from `docs/demo-trials.md` for this run: `https://wandb.ai/nathanweldegiorgis731-minerva-university/Noob-agent/weave/calls?filter=%7B%22traceRootsOnly%22%3Atrue%7D&peekPath=&query=doom-demo-trial-20260918c-a01`. Then show the skill source, e.g. via the SQLite `skill_version` table for that run. |
| 1:10–1:40 | Held-out replay: a new seed, skill available | "Transfer to a seed it never trained on, with a fresh conversation, graded by a grader the model never talks to." | **Label on screen: "Recorded run `doom-demo-trial-20260918c-a01` held-out cell `doom-basic-heldout-a` (seed 102), replayed."** Same `replay_doom_episode.py` command with the held-out episode ID. State on screen: "1 of 6 held-out goals completed in this run — see `docs/demo-trials.md`." |
| 1:40–2:05 | Minecraft: the recorded redstone-lamp frame, then a live join to the running local server | "The same loop runs on a second, different game — Minecraft — with only the connector swapped. The cold Action loop works live: 0 of 10 unusable replies, a 690 ms median decision. The full learn-a-skill loop is not proven here yet — say that on screen." | **Label: "assets/redstone-lamp-lit.png — a demo recording, not a graded model result."** Then: `uv run --env-file .env python scripts/run_minecraft_redstone_demo.py` shown starting (or a clip of a prior run) with the on-screen caption "Minecraft: Action loop proven live; full skill-learning loop not yet proven — see docs/demo-trials.md." <!-- MINECRAFT TRIAL 2 RESULT --> |
| 2:05–2:40 | "Production-ready" montage: CI badge on GitHub Actions, `uv run pytest -q` running, the sandbox/validator code, cost/latency numbers | "Why you could run this for real: a green CI workflow with no secrets, 597 of 598 tests passing on a fresh clone, sandboxed and versioned skills, and every number traced back to a run ID, not a highlight reel." | `uv run pytest -q` (terminal); open `.github/workflows/ci.yml` and the green Actions run; show `docs/demo-trials.md`'s token/call numbers for `doom-demo-trial-20260918c` (79,288 tokens, 61 calls, 0 truncated) |
| 2:40–3:00 | The Weave project's calls view (20+ seconds total screen time across the video), then the closing line | "The measured result: 5 of 5 benchmark sequences build a working skill, 8 of 30 held-out goals transfer, and Minecraft's Action loop already runs live. Full numbers and known limitations are in the repo." | Show the Weave UI once more; end card with repo URL |

## Exact replay commands (fill in run-specific IDs before recording)

```sh
# Cold training replay (label on screen as a replay)
uv run python scripts/replay_doom_episode.py \
  --database <path-to-demo-trial-database> \
  --episode-id <doom-demo-trial-20260918c-a01-training-episode-id>

# Held-out replay (label on screen as a replay)
uv run python scripts/replay_doom_episode.py \
  --database <path-to-demo-trial-database> \
  --episode-id <doom-basic-heldout-a-episode-id>

# Optional: live reasoning view read from Weave, if re-running live
uv run python scripts/live_reasoning_view.py --run-id doom-demo-trial-20260918c
```

`replay_doom_episode.py --help` confirms `--database`, `--episode-id`,
`--headless`, `--windowed`, and `--step-pause-seconds` as its only flags — no
flag exists to auto-label a replay on screen, so the on-screen "Recorded run
…, replayed" caption must be added as a title/overlay when editing, not by
the script.

## Honesty checklist (all must be yes before publishing)

- [ ] Every replay is labeled on screen with its run ID and the word
  "replayed"; no fixture skill (`scripted-fixture-not-model-generated`) is
  shown as model output.
- [ ] Every number on screen matches a cited run or scorecard
  (`docs/loop-optimization.md`, `docs/demo-trials.md`).
- [ ] The Doom demo-trial result is captioned "non-benchmark" on screen, not
  presented as the benchmark scorecard.
- [ ] The Minecraft segment says out loud that the full learning loop is not
  yet proven, not only the working Action loop.
- [ ] Known limitations are said out loud at least once (held-out is uneven;
  Minecraft skill-learning unproven; local-subprocess sandbox, not
  CoreWeave Sandbox).
- [ ] The W&B/Weave UI appears on screen for at least 20 seconds total.
- [ ] The video is ≤3 minutes, 1080p, with narration or captions.
