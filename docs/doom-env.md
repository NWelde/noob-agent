# Doom Environment Setup (ViZDoom)

Doom is the project's second game, connected through ViZDoom's Python API. This
page gets a ViZDoom environment running locally and verifies it can support the
minimum Doom BDP primitive tool set in
[`connector_contract.md`](../connector_contract.md).

This began as environment groundwork for build-order step 7
([`hackathon_plan.md`](../hackathon_plan.md) §10). The headless `DoomConnector`
is now implemented in `src/noob_agent/connectors/doom.py`.

## Prerequisites

- Python 3.9–3.14. ViZDoom 1.3.0 publishes binary wheels for CPython 3.9
  through 3.14 on `win_amd64`, `manylinux` (x86-64 and aarch64), and macOS arm64,
  so no compiler, CMake, or SDL toolchain is needed on those platforms.
- No Doom game data. ViZDoom bundles Freedoom and its included scenarios; the
  BDP uses an included scenario rather than a custom level
  ([`hackathon_plan.md`](../hackathon_plan.md) §8).
- No display for evaluation. The connector and everything below run headless by
  default. `DoomSettings(window_visible=True, realtime=True)` opens a visible
  window that renders every frame and paces accepted actions at Doom's native
  35 ticks per second, for watching a replay (`hackathon_plan.md` §19). Neither
  setting changes any observation or step result, and the visible window needs
  a display (on this WSL machine, WSLg).

## Install

ViZDoom is a project runtime dependency. Install the locked project environment:

```sh
uv sync --group dev
```

The manifest constrains it to `vizdoom>=1.3,<2`; `uv.lock` pins the resolved
environment alongside its transitive packages.

## Verify

Run the environment check:

```sh
uv run python scaffolding/doom_env/check_env.py
```

A healthy environment prints `PASS` and exits `0`:

```text
vizdoom 1.3.0
  reset observation: tick=14 variables=[100, 50, 0] labels=['Cacodemon', 'DoomPlayer']
  turn: 1 tick carries the whole amount; positive delta turns right
  init 0.59s, reset+5 actions 0.66s
  one 35-tick call 11ms
  scenario basic.cfg, seed 20260912
PASS: 26 checks, 10 primitives covered
```

Pass a different included scenario as an argument:

```sh
uv run python scaffolding/doom_env/check_env.py deadly_corridor.cfg
```

The check and everything it covers are described in
[`scaffolding/doom_env/README.md`](../scaffolding/doom_env/README.md).

## Local verification record

Verified on this WSL environment on 2026-09-12 (America/Los_Angeles):

- Installed `vizdoom 1.3.0` through `uv pip` using the supported CPython 3.14.6
  interpreter.
- `uv run python scaffolding/doom_env/check_env.py` passed all 26 checks for
  `basic.cfg` (seed `20260912`).
- The measured headless run initialized in 0.17 seconds, reset plus five
  actions took 0.29 seconds, and a 35-tick action took 2 ms.

This verification first established local environment readiness. PR #32 later
declared ViZDoom as a project dependency and added the `DoomConnector`.

## What the check confirms

- **All ten Doom BDP primitives are reachable.** `observe` and `wait` need no
  button; the other eight map to `MOVE_FORWARD`, `MOVE_BACKWARD`, `MOVE_LEFT`,
  `MOVE_RIGHT`, `TURN_LEFT_RIGHT_DELTA` (both turns), `ATTACK`, and `USE`.
- **Reset is deterministic.** The same scripted session on a fixed seed twice
  produces an identical public trace.
- **The game advances only by the ticks a tool asks for**, so evaluation does not
  depend on frame rate — as `connector_contract.md` requires.
- **Observations are public.** Status values (health, ammunition) plus ViZDoom's
  labels for objects currently in view. This is the declared
  structured-observation condition, not a pixels-only result.
- **Both Doom timeouts have headroom**: 10s reset and 3s per call.

## Three behaviors the connector handles

Measured locally, not taken from documentation. Each one fails quietly rather
than loudly, so they are important implementation constraints for the connector.

1. **A turn is one tick carrying the whole amount.** `TURN_LEFT_RIGHT_DELTA`
   applies once per tick, so holding it multiplies the turn — 4 ticks of 15
   degrees turns 60 degrees. Send `turn_left(45)` as a single tick of 45.
2. **A positive delta turns right.** Doom's `ANGLE` rises anticlockwise; the
   named `TURN_LEFT` button raises it and a positive delta lowers it. So
   `turn_left` is the negative delta, despite the button's name.
3. **Early actions are silently swallowed.** While the map is still starting,
   ViZDoom accepts an action and does nothing with it — no error, no rejection.
   `deadly_corridor.cfg` ships with `episode_start_time = 1` and loses its first
   actions; from tick 10 they register. Pin the episode start to tick 14, as the
   check does, or the connector will record no-op actions that look successful.

## Scenario choice

Any included scenario works. Three were verified:

| Scenario | Objects in view at reset | Fit |
| --- | --- | --- |
| `basic.cfg` | `Cacodemon`, `DoomPlayer` | Smallest; one visible target |
| `deadly_corridor.cfg` | `DoomPlayer`, `GreenArmor`, `ShotgunGuy`, `Zombieman` | Closest to the §6.2 training skill: locate, approach, engage |
| `defend_the_center.cfg` | `DoomPlayer`, `MarineChainsawVzd` | Stationary; turning and firing |

`deadly_corridor.cfg` gives the richest label set to summarize into an
observation. The connector freezes `basic.cfg` as the base of its declared
scenarios; see Connector below.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `FAIL: ViZDoom is not installed` | Run the install command above in the environment `uv run` uses. |
| Actions run but nothing moves | The episode started before input registers — see behavior 3. |
| A turn overshoots by a multiple | The delta was held for several ticks — see behavior 1. |
| First `init()` takes ~12s | Cold start only, typically the first scan of a new binary. Later inits are under a second. |
| `pip` tries to build from source | The interpreter is outside 3.9–3.14, or the platform has no wheel. Use a supported Python. |

## Connector

`src/noob_agent/connectors/doom.py` implements the ten declared Doom BDP
primitives against the included `basic.cfg` scenario. It uses the one-tick,
signed delta behavior above for turns and emits only HUD health/ammunition,
player angle, and visible object labels. ViZDoom is a declared runtime
dependency because the connector imports it when a game starts.

Connector version `doom-vizdoom-v2` implements `hackathon_plan.md` section 17,
step 8a:

- **Declared scenarios.** `doom-basic-training`, `doom-basic-heldout-a`, and
  `doom-basic-heldout-b`. Any other scenario ID fails reset. The seed places the
  target. During reset, before observation zero, every scenario spends 12 ticks
  on its starting offset (no movement for training, a strafe left for
  `heldout-a`, a strafe right for `heldout-b`) and then 35 idle ticks. Doom's
  friction keeps a strafing player sliding for a long time. The idle ticks don't
  stop that, but they make each reset exactly repeatable for a fixed scenario
  and seed.
- **Seeds.** Committed in `scenarios/doom/basic-v1/manifest.json`: one
  training seed and three for each held-out scenario, all distinct, and every
  one shows the target at observation zero. Agent-facing code never reads this
  file.
- **Aiming signal.** Each visible object has `properties.screen_offset`, an
  integer from -100 (left edge of the screen) to 100 (right edge), where 0 is
  the center. Turning right lowers a target's offset. The player's own
  `DoomPlayer` label is not listed.
- **Public goal.** "Eliminate the hostile target in this area. Use visible
  results as evidence. Finish within the action budget."
- **Private outcome.** `DoomConnector.private_outcome()` returns the kill
  count, player death, timeout, and whether the episode finished. The values
  freeze when the episode ends and stay readable after `close()`. It is not
  part of `GameConnector`, and none of these values, nor the seed, appears in an
  observation, a step result, a message, or `episode_id`. `terminal_reason` is
  `episode_finished` for every ending.
- **Episode IDs.** An episode ID is the scenario ID followed by 16 random
  decimal digits, such as `doom-basic-heldout-a-0123456789012345`. It is unique
  across connector instances, so episodes from fresh connectors can be recorded
  in one store. The digits carry no seed.
- **Rejected requests.** A rejected request advances the public sequence, like
  any other durable result, and charges no primitive.

Verify with `uv run pytest tests/test_connectors_doom.py -v`.

## Watching a recorded episode

`scripts/replay_doom_episode.py` (`hackathon_plan.md` §19, step 19b) replays a
recorded Doom episode in a visible ViZDoom window. It resets the recorded
scenario with the recorded seed, sends the recorded requests, and checks every
result against the record, ignoring only the per-reset episode ID and host wall
time. It stops at the first step that differs and exits 1, so a diverged replay
is never shown as the agent's play. It is labeled as a replay, reads only a
temporary copy of the database, and never calls a model, the Builder, a grader,
or a skill executor.

List the Doom episodes in a database:

```sh
uv run python scripts/replay_doom_episode.py --database .noob-agent/doom-learning-live.sqlite3
```

Watch one. The window paces actions at 35 ticks per second and pauses
`--step-pause-seconds` (default 0.5) after each step, including rejected ones:

```sh
uv run python scripts/replay_doom_episode.py \
  --database .noob-agent/doom-learning-live.sqlite3 \
  --episode-id doom-basic-training-2716044773193143
```

Add `--headless` to check a replay without a display. The script refuses (exit
2) a missing database or episode, a non-Doom episode, and an episode recorded
with a connector version other than the current one. A replay also depends on
the ViZDoom build and bundled scenario files matching the recording, which the
record does not store; a difference shows up as a mismatch.
## Non-benchmark token-budget demo

After the live-demo token-budget implementation is installed, run the visible,
full-screen diagnostic with Weave tracing enabled:

```sh
NOOB_AGENT_SANDBOX_MODE=local uv run --env-file .env python \
  scripts/run_doom_learning_sequence.py --live-demo --token-budget 500000
```

This is not benchmark evidence. It uses a 32,000-token Builder cap, up to three
repairs, a 3,600-second safety deadline, and writes the Weave-derived transcript
to `.noob-agent/doom-demo-log.md` after trace delivery.
