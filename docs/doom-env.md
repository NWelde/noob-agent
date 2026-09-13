# Doom Environment Setup (ViZDoom)

Doom is the project's second game, connected through ViZDoom's Python API. This
page gets a ViZDoom environment running locally and verifies it can support the
minimum Doom BDP primitive tool set in
[`connector_contract.md`](../connector_contract.md).

This is **environment groundwork for build-order step 7**
([`hackathon_plan.md`](../hackathon_plan.md) §10). The `DoomConnector` itself is
a separate, later issue.

## Prerequisites

- Python 3.9–3.14. ViZDoom 1.3.0 publishes binary wheels for CPython 3.9
  through 3.14 on `win_amd64`, `manylinux` (x86-64 and aarch64), and macOS arm64,
  so no compiler, CMake, or SDL toolchain is needed on those platforms.
- No Doom game data. ViZDoom bundles Freedoom and its included scenarios; the
  BDP uses an included scenario rather than a custom level
  ([`hackathon_plan.md`](../hackathon_plan.md) §8).
- No display. Everything below runs headless with `set_window_visible(False)`.

## Install

ViZDoom is **not** a project dependency. It is deliberately absent from
`pyproject.toml`: nothing in `src/` imports it yet, and adding it to the manifest
belongs with the connector milestone that actually needs it. Install it into your
local environment only:

```sh
uv pip install "vizdoom>=1.3,<2"
```

That pulls `gymnasium`, `numpy`, `pygame-ce`, `cloudpickle`, and
`farama-notifications` alongside it. To undo it:

```sh
uv pip uninstall vizdoom
```

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

## Three behaviors the connector must handle

Measured locally, not taken from documentation. Each one fails quietly rather
than loudly, so they are worth knowing before the connector is written.

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
observation. The BDP scenario is the connector issue's decision.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `FAIL: ViZDoom is not installed` | Run the install command above in the environment `uv run` uses. |
| Actions run but nothing moves | The episode started before input registers — see behavior 3. |
| A turn overshoots by a multiple | The delta was held for several ticks — see behavior 1. |
| First `init()` takes ~12s | Cold start only, typically the first scan of a new binary. Later inits are under a second. |
| `pip` tries to build from source | The interpreter is outside 3.9–3.14, or the platform has no wheel. Use a supported Python. |

## Next

The `DoomConnector` implementation (`src/noob_agent/connectors/doom.py`) is out
of scope here and is gated by the core-loop rules in
[`CLAUDE.md`](../CLAUDE.md) and [`AGENTS.md`](../AGENTS.md). When it is taken up,
it will need the three behaviors above, a decision on the BDP scenario, and
ViZDoom added to `pyproject.toml` as a dependency of the game layer.
