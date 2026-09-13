#!/usr/bin/env python3
"""Verify a local ViZDoom environment against the Doom BDP connector requirements.

Non-production scaffolding. This script is standalone: apart from ``vizdoom``
itself it imports only the standard library, and it never imports, calls, or
configures ``noob_agent``. It answers one question — can the minimum Doom BDP
primitive tool set in ``connector_contract.md`` be built on this machine's
ViZDoom — and exits non-zero when it cannot.

It does not implement the connector. It maps each required primitive to the
ViZDoom control it would use, exercises them in one short scripted session, and
checks the properties the harness depends on: deterministic reset, tick-exact
advancement, and public-only observations.

Run:
    uv run python scaffolding/doom_env/check_env.py

An optional argument names a different included scenario config:

    uv run python scaffolding/doom_env/check_env.py deadly_corridor.cfg
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

DEFAULT_SCENARIO = "basic.cfg"
SEED = 20260912

# connector_contract.md: movement and attack durations are 1 to 35 game ticks;
# turn amount is 1 to 90 degrees.
MAX_DURATION_TICKS = 35
MAX_TURN_DEGREES = 90

# connector_contract.md: 3 seconds per Doom connector call, 10 seconds to reset.
CALL_TIMEOUT_SECONDS = 3.0
RESET_TIMEOUT_SECONDS = 10.0

# Measured: while the map is still starting, ViZDoom accepts an action and
# silently does nothing with it. `deadly_corridor.cfg` ships with
# `episode_start_time = 1` and swallows the first actions; from tick 10 they
# register, and `basic.cfg` already starts at 14. Every episode here starts at
# the same tick so a reset never hands back a player who cannot yet move.
MIN_EPISODE_START_TICKS = 14

# The minimum Doom BDP tool set, mapped to the ViZDoom button each one would
# drive. `observe` and `wait` need no button: one reads the current state, the
# other advances ticks with every button released.
PRIMITIVE_BUTTONS: dict[str, str | None] = {
    "observe": None,
    "move_forward": "MOVE_FORWARD",
    "move_backward": "MOVE_BACKWARD",
    "strafe_left": "MOVE_LEFT",
    "strafe_right": "MOVE_RIGHT",
    "turn_left": "TURN_LEFT_RIGHT_DELTA",
    "turn_right": "TURN_LEFT_RIGHT_DELTA",
    "attack": "ATTACK",
    "use": "USE",
    "wait": None,
}

# Measured, not assumed — see the README. ViZDoom applies a delta button once
# per tick, and a positive TURN_LEFT_RIGHT_DELTA lowers ANGLE, which is the
# direction the named TURN_RIGHT button turns. So a turn primitive is one tick
# carrying the whole amount, and `turn_left` is the negative delta.
TURN_DELTA_SIGN = {"turn_left": -1.0, "turn_right": 1.0}
TURN_TICKS = 1

# Public status values only: what an ordinary player can see on the HUD.
REQUIRED_GAME_VARIABLES = ("HEALTH", "AMMO2", "ANGLE")


class Report:
    """Counts checks and collects every failure instead of stopping at the first."""

    def __init__(self) -> None:
        self.checks = 0
        self.failures: list[str] = []
        self.notes: list[str] = []

    def check(self, condition: bool, message: str) -> bool:
        self.checks += 1
        if not condition:
            self.failures.append(message)
        return condition

    def note(self, message: str) -> None:
        self.notes.append(message)


def _scripted_trace(game: Any, vzd: Any, buttons: list[Any]) -> list[tuple[int, ...]]:
    """Run one fixed action sequence and return a comparable trace of the result.

    The sequence is deliberately ordinary: look around, walk, strafe, and fire —
    the Doom equivalents of a new player's first moves.
    """
    game.set_seed(SEED)
    game.new_episode()

    index = {button.name: position for position, button in enumerate(buttons)}
    trace: list[tuple[int, ...]] = []

    def act(pressed: dict[str, float], tics: int) -> None:
        action = [0.0] * len(buttons)
        for name, value in pressed.items():
            action[index[name]] = value
        game.make_action(action, tics)
        state = game.get_state()
        if state is None:
            trace.append((game.get_episode_time(), -1))
            return
        variables = tuple(int(value) for value in state.game_variables)
        labels = tuple(sorted(label.object_name for label in state.labels))
        trace.append((game.get_episode_time(), len(labels), *variables, hash(labels)))

    # turn_left(15): one tick carrying the whole amount, negative for left.
    act({"TURN_LEFT_RIGHT_DELTA": TURN_DELTA_SIGN["turn_left"] * 15.0}, TURN_TICKS)
    act({"MOVE_FORWARD": 1.0}, 8)
    act({"MOVE_LEFT": 1.0}, 4)
    act({}, 2)
    act({"ATTACK": 1.0}, 4)
    return trace


def main(argv: list[str]) -> int:
    report = Report()
    scenario_name = argv[0] if argv else DEFAULT_SCENARIO

    try:
        import vizdoom as vzd
    except ImportError as error:
        print("FAIL: ViZDoom is not installed in this environment")
        print(f"  - {error}")
        print("  - see docs/doom-env.md for the install command")
        return 1

    version = getattr(vzd, "__version__", "unknown")
    print(f"vizdoom {version}")

    # 1. Every required primitive maps to a control this build actually exposes.
    # A pybind11 enum is not iterable; its members are exposed as a mapping.
    available = set(vzd.Button.__members__)
    for primitive, button in PRIMITIVE_BUTTONS.items():
        if button is None:
            continue
        report.check(
            button in available,
            f"primitive {primitive!r} needs vizdoom.Button.{button}, which this build lacks",
        )
    report.check(
        "TURN_LEFT_RIGHT_DELTA" in available,
        "turning by a degree amount needs the TURN_LEFT_RIGHT_DELTA delta button",
    )

    # 2. An included scenario loads, with no custom level required.
    scenario = Path(vzd.scenarios_path) / scenario_name
    if not report.check(scenario.is_file(), f"included scenario {scenario} not found"):
        print(f"FAIL: {len(report.failures)} of {report.checks} checks failed")
        for failure in report.failures:
            print(f"  - {failure}")
        return 1

    buttons = [
        vzd.Button.MOVE_FORWARD,
        vzd.Button.MOVE_BACKWARD,
        vzd.Button.MOVE_LEFT,
        vzd.Button.MOVE_RIGHT,
        vzd.Button.TURN_LEFT_RIGHT_DELTA,
        vzd.Button.ATTACK,
        vzd.Button.USE,
    ]

    game = vzd.DoomGame()
    game.load_config(str(scenario))
    game.set_window_visible(False)
    game.set_mode(vzd.Mode.PLAYER)
    game.set_available_buttons(buttons)
    game.set_button_max_value(vzd.Button.TURN_LEFT_RIGHT_DELTA, MAX_TURN_DEGREES)
    game.set_available_game_variables(
        [getattr(vzd.GameVariable, name) for name in REQUIRED_GAME_VARIABLES]
    )
    game.set_labels_buffer_enabled(True)
    game.set_episode_timeout(2100)
    game.set_episode_start_time(MIN_EPISODE_START_TICKS)

    started = time.monotonic()
    game.init()
    init_seconds = time.monotonic() - started

    try:
        # 3. Reset is deterministic on a fixed seed: the same scripted session
        #    twice must produce the same public trace.
        reset_started = time.monotonic()
        first = _scripted_trace(game, vzd, buttons)
        reset_seconds = time.monotonic() - reset_started
        second = _scripted_trace(game, vzd, buttons)
        report.check(first == second, "a fixed seed did not reproduce the same session trace")
        report.check(len(first) == 5, "the scripted session did not record five steps")

        # 4. Observations are public status values plus visible-object labels.
        game.set_seed(SEED)
        game.new_episode()
        state = game.get_state()
        if report.check(state is not None, "no state available after reset"):
            report.check(
                len(state.game_variables) == len(REQUIRED_GAME_VARIABLES),
                "the requested public game variables were not delivered",
            )
            report.check(
                hasattr(state, "labels"),
                "this build exposes no labels, so there is no structured observation",
            )
            report.check(
                state.tic >= MIN_EPISODE_START_TICKS,
                f"the episode starts at tick {state.tic}, before player input registers",
            )
            report.note(
                f"reset observation: tick={state.tic} "
                f"variables={[int(v) for v in state.game_variables]} "
                f"labels={sorted({label.object_name for label in state.labels})}"
            )

        # 5. The game advances only by the ticks a tool asks for.
        before = game.get_episode_time()
        idle = [0.0] * len(buttons)
        call_started = time.monotonic()
        game.make_action(idle, MAX_DURATION_TICKS)
        call_seconds = time.monotonic() - call_started
        advanced = game.get_episode_time() - before
        report.check(
            advanced == MAX_DURATION_TICKS,
            f"a {MAX_DURATION_TICKS}-tick action advanced the game by {advanced} ticks",
        )

        # 6. A turn of 1 to 90 degrees lands on the amount actually requested.
        turn_index = buttons.index(vzd.Button.TURN_LEFT_RIGHT_DELTA)

        def turn(primitive: str, degrees: float, ticks: int = TURN_TICKS) -> float:
            game.set_seed(SEED)
            game.new_episode()
            before_angle = float(game.get_state().game_variables[2])
            action = [0.0] * len(buttons)
            action[turn_index] = TURN_DELTA_SIGN[primitive] * degrees
            game.make_action(action, ticks)
            after_angle = float(game.get_state().game_variables[2])
            # Doom's ANGLE rises anticlockwise, so a left turn is the positive change.
            return (after_angle - before_angle + 180.0) % 360.0 - 180.0

        for degrees in (1.0, 45.0, float(MAX_TURN_DEGREES)):
            left = turn("turn_left", degrees)
            right = turn("turn_right", degrees)
            report.check(
                abs(left - degrees) < 1.0,
                f"turn_left({degrees:g}) moved the view {left:.2f} degrees, not {degrees:g}",
            )
            report.check(
                abs(right + degrees) < 1.0,
                f"turn_right({degrees:g}) moved the view {right:.2f} degrees, not -{degrees:g}",
            )

        # A delta button is applied once per tick, so holding one multiplies the
        # turn. The connector must send a turn as a single tick; this check fails
        # loudly if a future ViZDoom stops behaving that way.
        held = turn("turn_right", 30.0, ticks=2)
        report.check(
            abs(held + 60.0) < 1.0,
            f"a 2-tick 30-degree delta turned {held:.2f} degrees, so the per-tick "
            "rule this scaffold documents no longer holds",
        )
        report.note("turn: 1 tick carries the whole amount; positive delta turns right")

        # 7. Both contract timeouts have headroom on this machine.
        report.check(
            reset_seconds < RESET_TIMEOUT_SECONDS,
            f"reset plus five actions took {reset_seconds:.2f}s, "
            f"over the {RESET_TIMEOUT_SECONDS}s reset timeout",
        )
        report.check(
            call_seconds < CALL_TIMEOUT_SECONDS,
            f"one {MAX_DURATION_TICKS}-tick call took {call_seconds:.2f}s, "
            f"over the {CALL_TIMEOUT_SECONDS}s call timeout",
        )
        report.note(f"init {init_seconds:.2f}s, reset+5 actions {reset_seconds:.2f}s")
        report.note(f"one {MAX_DURATION_TICKS}-tick call {call_seconds * 1000:.0f}ms")
        report.note(f"scenario {scenario.name}, seed {SEED}")
    finally:
        game.close()

    for note in report.notes:
        print(f"  {note}")

    if report.failures:
        print(f"FAIL: {len(report.failures)} of {report.checks} checks failed")
        for failure in report.failures:
            print(f"  - {failure}")
        return 1

    print(f"PASS: {report.checks} checks, {len(PRIMITIVE_BUTTONS)} primitives covered")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
