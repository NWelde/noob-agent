# Doom Environment Check (non-production scaffolding)

**This is disposable scaffolding, not production code.** Nothing in the main
application imports, calls, builds, or deploys it. It starts no service, reads no
credential, touches no shared runtime configuration, and changes no CI. It is
safe to delete at any time — see [Removal](#removal).

It answers one question for build-order step 7: **can the minimum Doom BDP
primitive tool set in [`connector_contract.md`](../../connector_contract.md) be
built on ViZDoom as installed on this machine?** It maps each required primitive
to the ViZDoom control it would use, exercises them in one short scripted
session, and checks the properties the harness depends on.

It does **not** implement `DoomConnector`. That is a separate, later issue.

## Run

Install ViZDoom first — see [`docs/doom-env.md`](../../docs/doom-env.md). Then:

```sh
uv run python scaffolding/doom_env/check_env.py
```

Exit code `0` prints the check count and the measured session facts; exit code
`1` prints every failure. An optional argument names a different included
scenario config:

```sh
uv run python scaffolding/doom_env/check_env.py deadly_corridor.cfg
```

Apart from `vizdoom` itself the script imports only the standard library, and it
never imports `noob_agent`.

## What it checks

| Group | Check |
| --- | --- |
| Primitive coverage | Every one of the ten Doom BDP primitives maps to a `vizdoom.Button` this build exposes, including the `TURN_LEFT_RIGHT_DELTA` delta button a degree-valued turn needs |
| Scenario | An **included** ViZDoom scenario loads — no custom level, per `hackathon_plan.md` §8 |
| Deterministic reset | The same scripted session on a fixed seed twice produces an identical public trace (ticks, status values, visible-object labels) |
| Public observation | The reset observation carries public status values and labels for objects in view — not pixels |
| Tick-exact advance | A 35-tick action advances the game by exactly 35 ticks |
| Turn amounts | `turn_left`/`turn_right` land on 1, 45, and 90 degrees, and the per-tick delta rule below still holds |
| Live input | The episode starts at a tick where player input actually registers |
| Timing headroom | Reset and a 35-tick call stay inside the contract's 10s and 3s Doom timeouts |

## Measured environment facts

These were measured on this machine, not taken from documentation. They are the
reason the script is shaped the way it is, and the future connector needs all
three.

- **A turn is one tick, carrying the whole amount.** `TURN_LEFT_RIGHT_DELTA` is
  applied once *per tick*, so holding it multiplies the turn: 4 ticks of 15
  degrees turns 60 degrees, not 15. A `turn_left(amount)` primitive must send a
  single tick with the full amount.
- **A positive delta turns right.** Doom's `ANGLE` rises anticlockwise, and the
  named `TURN_LEFT` button raises it while a positive `TURN_LEFT_RIGHT_DELTA`
  lowers it. So `turn_left` is the *negative* delta, despite the button's name.
- **Actions before the map finishes starting are silently swallowed.**
  `deadly_corridor.cfg` ships with `episode_start_time = 1`; actions sent then
  are accepted and do nothing — no error, no rejection. From tick 10 they
  register, and `basic.cfg` already starts at 14. The script pins every episode
  to tick 14. A connector that skips this would record a full episode of
  no-op actions that look successful.

## Verified scenarios

All three pass 26 checks with `vizdoom 1.3.0` on CPython 3.14.7, Windows 11
(`win_amd64` wheel), headless:

| Scenario | Reset labels in view |
| --- | --- |
| `basic.cfg` | `Cacodemon`, `DoomPlayer` |
| `deadly_corridor.cfg` | `DoomPlayer`, `GreenArmor`, `ShotgunGuy`, `Zombieman` |
| `defend_the_center.cfg` | `DoomPlayer`, `MarineChainsawVzd` |

`deadly_corridor.cfg` is the closest fit to the training skill named in
`hackathon_plan.md` §6.2 — locating, approaching, and engaging a visible target —
and its labels give the most to summarize. Choosing the BDP scenario is the
connector issue's call, not this scaffold's.

## Known limitations

- **Feasibility, not a connector.** Passing means the controls, determinism, and
  timing are available. It does not mean the primitives behave correctly under
  the contract's preconditions, budgets, rejection codes, or `unknown` result
  handling.
- **Determinism is checked over one fixed action sequence**, on one machine, in
  one process. It is not a cross-machine or cross-version reproducibility claim,
  and ViZDoom's own behavior across builds is not pinned here.
- **The trace compares public values only** — ticks, status values, and the set
  of visible-object labels. Two sessions that differ in some other respect would
  still compare equal.
- **No observation summarizer.** Turning labels and status values into the
  contract's `Observation` is connector work and is deliberately absent.
- **`use` is mapped but not exercised** against a door or switch; `basic.cfg`
  has nothing to use.
- **Windows-only measurement.** The wheels exist for Linux and macOS, but the
  timings and the tick behavior above were only measured here.

## Removal

The scaffold is self-contained in this directory. To remove it:

```sh
rm -rf scaffolding/doom_env
```

`docs/doom-env.md` references it, so remove that reference too. Nothing else
points at it and no test breaks. Add a `[FIXED]` or `[DOCUMENTED]` entry to
`CHANGELOG.md` noting the removal.

Promoting any of this into the main application requires an explicitly approved
`hackathon_plan.md` section, a failing test written first, and a separate pull
request.
