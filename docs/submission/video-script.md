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
  the most recent completed, non-escalated Doom demo trial, and
  `minecraft-redstone-20260918T122657Z` (attempt a02) as the most recent
  Minecraft trial with a skill accepted and reused. If newer trials exist,
  swap their run IDs and numbers in everywhere below.
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
| 1:40–2:05 | Minecraft: the recorded redstone-lamp frame, then the Weave trace for the trial-4 run | "The same loop runs on a second, different game — Minecraft — with only the connector swapped. In run `minecraft-redstone-20260918T122657Z`, a cold attempt lit the lamp, the Builder's skill was accepted after one repair, and reusing that skill lit the lamp again. That's the loop working end to end on a simple task — not yet on an unseen variation, so say that on screen too." | **Label: "assets/redstone-lamp-lit.png — a demo recording, not a graded model result."** No recorded-episode replay script exists for Minecraft (unlike Doom's `replay_doom_episode.py`), so show the run's Weave trace in the browser instead — open the project (`nathanweldegiorgis731-minerva-university/Noob-agent`) and search calls for `minecraft-redstone-20260918T122657Z-a02`. On-screen caption: "Minecraft: skill built, accepted, and reused on a same-task check (run `…T122657Z`, attempt a02); held-out transfer not yet proven — see docs/demo-trials.md." |
| 2:05–2:40 | "Production-ready" montage: CI badge on GitHub Actions, `uv run pytest -q` running, the sandbox/validator code, cost/latency numbers | "Why you could run this for real: a green CI workflow with no secrets, 597 of 598 tests passing on a fresh clone, sandboxed and versioned skills, and every number traced back to a run ID, not a highlight reel." | `uv run pytest -q` (terminal); open `.github/workflows/ci.yml` and the green Actions run; show `docs/demo-trials.md`'s token/call numbers for `doom-demo-trial-20260918c` (79,288 tokens, 61 calls, 0 truncated) and for the Minecraft trial-4 attempt (about 21,000 tokens) |
| 2:40–3:00 | The Weave project's calls view (20+ seconds total screen time across the video), then the closing line | "The measured result: 5 of 5 benchmark sequences build a working skill, 8 of 30 held-out goals transfer, and Minecraft's loop now runs end to end on a simple task. Full numbers and known limitations are in the repo." | Show the Weave UI once more; end card with repo URL |

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

**Minecraft has no equivalent replay script.** `scripts/` has no
frame-recording or episode-replay entry point for Minecraft (only
`run_minecraft_redstone_demo.py`, `run_minecraft_live_smoke.py`, and
`run_minecraft_easy_live_smoke.py`, none of which replay a saved episode).
For the Minecraft segment, show the trial-4 run's Weave trace in the
browser instead of a replay:

```
https://wandb.ai/nathanweldegiorgis731-minerva-university/Noob-agent/weave/calls?filter=%7B%22traceRootsOnly%22%3Atrue%7D&peekPath=&query=minecraft-redstone-20260918T122657Z-a02
```

Caption it "Weave trace, run `minecraft-redstone-20260918T122657Z`, attempt
a02" — not a replay, since none exists for this connector.

## Honesty checklist (all must be yes before publishing)

- [ ] Every replay is labeled on screen with its run ID and the word
  "replayed"; no fixture skill (`scripted-fixture-not-model-generated`) is
  shown as model output.
- [ ] Every number on screen matches a cited run or scorecard
  (`docs/loop-optimization.md`, `docs/demo-trials.md`).
- [ ] The Doom demo-trial result is captioned "non-benchmark" on screen, not
  presented as the benchmark scorecard.
- [ ] The Minecraft segment says out loud that the run shown is a same-task
  cold-vs-skill-reuse check, not held-out transfer, and that held-out
  transfer on Minecraft is still not proven.
- [ ] Known limitations are said out loud at least once (Doom held-out is
  uneven; Minecraft held-out transfer unproven and skill use succeeded in
  only 1 of 2 attempts in the cited round; local-subprocess sandbox, not
  CoreWeave Sandbox).
- [ ] The W&B/Weave UI appears on screen for at least 20 seconds total.
- [ ] The video is ≤3 minutes, 1080p, with narration or captions.
