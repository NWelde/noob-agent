"""ViZDoom implementation of the fixed public Doom connector surface.

Scenarios are declared variations of the included `basic.cfg`: the seed places
the target, and each scenario ID applies a fixed starting offset during reset,
before observation zero. The kill count, player death, and timeout are kept on
a harness-only outcome and never enter an observation, a step result, or a
message.
"""

from __future__ import annotations

import importlib
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from noob_agent.connectors.protocol import ConnectorError
from noob_agent.domain.model import (
    ConnectorManifest,
    Observation,
    PublicPlayerState,
    StepResult,
    ToolDefinition,
    ToolRequest,
    VisibleObject,
)

GAME_ID = "doom-vizdoom"
CONNECTOR_VERSION = "doom-vizdoom-v2"
PUBLIC_GOAL = (
    "Eliminate the hostile target in this area. Use visible results as evidence. "
    "Finish within the action budget."
)
MAX_TICKS = 35
MAX_DEGREES = 90
START_TICKS = 14

# Every reset spends the same ticks, so observation zero has the same logical
# time in every scenario; only the held-out scenarios strafe during them.
START_OFFSET_TICKS: Final = 12
# Doom's friction keeps a strafing player sliding; this is enough for the
# reset to be exactly repeatable, which is what the contract requires.
SETTLE_TICKS: Final = 35

StartOffset = Literal["left", "right"] | None
SCENARIOS: Final[dict[str, StartOffset]] = {
    "doom-basic-training": None,
    "doom-basic-heldout-a": "left",
    "doom-basic-heldout-b": "right",
}
SCENARIO_IDS: Final = tuple(SCENARIOS)

_PLAYER_LABEL: Final = "DoomPlayer"
_MAX_SCREEN_OFFSET: Final = 100


def _tool(name: str, description: str, schema: dict[str, Any], changing: bool) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        argument_schema=schema,
        max_duration=MAX_DEGREES if "turn" in name else MAX_TICKS,
        state_changing=changing,
    )


_TICKS = {
    "type": "object",
    "properties": {"ticks": {"type": "integer", "minimum": 1, "maximum": MAX_TICKS}},
    "additionalProperties": False,
}
_DEGREES = {
    "type": "object",
    "properties": {"degrees": {"type": "integer", "minimum": 1, "maximum": MAX_DEGREES}},
    "required": ["degrees"],
    "additionalProperties": False,
}
MANIFEST = ConnectorManifest(
    connector_version=CONNECTOR_VERSION,
    game_id=GAME_ID,
    observation_mode="structured_hud_and_visible_labels",
    timing_model="game_ticks",
    tools=(
        _tool(
            "observe",
            "Read the visible HUD and objects.",
            {"type": "object", "additionalProperties": False},
            False,
        ),
        _tool("move_forward", "Walk forward for a number of ticks.", _TICKS, True),
        _tool("move_backward", "Walk backward for a number of ticks.", _TICKS, True),
        _tool("strafe_left", "Move left for a number of ticks.", _TICKS, True),
        _tool("strafe_right", "Move right for a number of ticks.", _TICKS, True),
        _tool("turn_left", "Turn left by a degree amount.", _DEGREES, True),
        _tool("turn_right", "Turn right by a degree amount.", _DEGREES, True),
        _tool("attack", "Fire or attack for a number of ticks.", _TICKS, True),
        _tool("use", "Use the normal interact control for a number of ticks.", _TICKS, True),
        _tool("wait", "Wait for a number of ticks.", _TICKS, False),
    ),
)


@dataclass(frozen=True)
class DoomSettings:
    scenario: str = "basic.cfg"
    call_timeout_seconds: float = 3.0
    reset_timeout_seconds: float = 10.0


@dataclass(frozen=True)
class DoomEpisodeOutcome:
    """Private end-of-episode facts for the grader. Never shown to a policy."""

    kills: int
    player_dead: bool
    timed_out: bool
    finished: bool


class DoomConnector:
    """Headless ViZDoom connector; it exposes only HUD variables and labels."""

    def __init__(self, settings: DoomSettings | None = None) -> None:
        self._settings = settings or DoomSettings()
        self._vzd: Any | None = None
        self._game: Any | None = None
        self._buttons: list[Any] = []
        self._scenario_id: str | None = None
        self._episode_id: str | None = None
        self._latest: Observation | None = None
        self._outcome: DoomEpisodeOutcome | None = None
        self._sequence = 0

    async def manifest(self) -> ConnectorManifest:
        return MANIFEST

    async def reset(self, scenario_id: str, seed: int) -> Observation:
        if scenario_id not in SCENARIOS:
            raise ConnectorError(
                f"Scenario {scenario_id!r} is undeclared by connector {CONNECTOR_VERSION}."
            )
        game = self._ensure_game()
        started = time.monotonic()
        game.set_seed(seed)
        game.new_episode()
        self._outcome = None
        offset = [0.0] * len(self._buttons)
        direction = SCENARIOS[scenario_id]
        if direction is not None:
            offset[self._button_index("MOVE_LEFT" if direction == "left" else "MOVE_RIGHT")] = 1.0
        game.make_action(offset, START_OFFSET_TICKS)
        game.make_action([0.0] * len(self._buttons), SETTLE_TICKS)
        if time.monotonic() - started > self._settings.reset_timeout_seconds:
            raise ConnectorError("Doom reset exceeded its timeout.")
        self._scenario_id, self._sequence = scenario_id, 0
        # Unique across connector instances so episodes share one store, and
        # decimal so the token can never spell a word the leak checks forbid.
        self._episode_id = f"{scenario_id}-{secrets.randbelow(10**16):016d}"
        self._capture_outcome()
        self._latest = self._observation(None)
        return self._latest

    async def step(self, request: ToolRequest) -> StepResult:
        started = time.monotonic()
        if self._latest is None:
            raise ConnectorError("reset must succeed before an action is attempted.")
        action = self._action(request)
        if action is None:
            return self._rejected(request, started)
        values, ticks, changed = action
        game = self._game
        assert game is not None
        game.make_action(values, ticks)
        self._capture_outcome()
        elapsed = time.monotonic() - started
        self._sequence += 1
        if elapsed > self._settings.call_timeout_seconds:
            return self._result(
                request,
                "unknown",
                "TIMEOUT_UNKNOWN",
                "Action outcome could not be confirmed.",
                None,
                ticks,
                started,
            )
        return self._result(
            request, "succeeded", "OK", "Action completed.", changed, ticks, started
        )

    async def is_terminal(self) -> bool:
        return self._latest is not None and self._latest.terminal

    async def close(self) -> None:
        if self._game is not None and self._latest is not None:
            self._capture_outcome()
        game, self._game = self._game, None
        if game is not None:
            game.close()

    def private_outcome(self) -> DoomEpisodeOutcome:
        """Harness-only: the latest episode's private outcome, readable after close."""
        if self._outcome is None:
            raise ConnectorError("reset must succeed before a private outcome exists.")
        return self._outcome

    def _capture_outcome(self) -> None:
        if self._outcome is not None and self._outcome.finished:
            return
        game, vzd = self._game, self._vzd
        assert game is not None and vzd is not None
        self._outcome = DoomEpisodeOutcome(
            kills=int(game.get_game_variable(vzd.GameVariable.KILLCOUNT)),
            player_dead=bool(game.is_player_dead()),
            timed_out=bool(game.is_episode_timeout_reached()),
            finished=bool(game.is_episode_finished()),
        )

    def _button_index(self, name: str) -> int:
        return [button.name for button in self._buttons].index(name)

    def _ensure_game(self) -> Any:
        if self._game is not None:
            return self._game
        try:
            vzd = importlib.import_module("vizdoom")
        except ImportError as error:
            raise ConnectorError("ViZDoom is not installed; see docs/doom-env.md.") from error
        scenario = Path(vzd.scenarios_path) / self._settings.scenario
        if not scenario.is_file():
            raise ConnectorError(
                f"Included Doom scenario {self._settings.scenario!r} is unavailable."
            )
        game = vzd.DoomGame()
        game.load_config(str(scenario))
        game.set_window_visible(False)
        game.set_mode(vzd.Mode.PLAYER)
        self._buttons = [
            vzd.Button.MOVE_FORWARD,
            vzd.Button.MOVE_BACKWARD,
            vzd.Button.MOVE_LEFT,
            vzd.Button.MOVE_RIGHT,
            vzd.Button.TURN_LEFT_RIGHT_DELTA,
            vzd.Button.ATTACK,
            vzd.Button.USE,
        ]
        game.set_available_buttons(self._buttons)
        game.set_button_max_value(vzd.Button.TURN_LEFT_RIGHT_DELTA, MAX_DEGREES)
        game.set_available_game_variables(
            [vzd.GameVariable.HEALTH, vzd.GameVariable.AMMO2, vzd.GameVariable.ANGLE]
        )
        game.set_labels_buffer_enabled(True)
        game.set_episode_timeout(2100)
        game.set_episode_start_time(START_TICKS)
        game.init()
        self._vzd = vzd
        self._game = game
        return game

    def _action(self, request: ToolRequest) -> tuple[list[float], int, bool] | None:
        names = {tool.name for tool in MANIFEST.tools}
        if request.tool_name not in names:
            return None
        args = request.arguments
        if request.tool_name == "observe":
            if args:
                return None
            return [0.0] * len(self._buttons), 1, False
        key = "degrees" if request.tool_name.startswith("turn_") else "ticks"
        value = args.get(key, 1)
        maximum = MAX_DEGREES if key == "degrees" else MAX_TICKS
        if (
            set(args) != {key}
            or isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= maximum
        ):
            return None
        action = [0.0] * len(self._buttons)
        mapping = {
            "move_forward": "MOVE_FORWARD",
            "move_backward": "MOVE_BACKWARD",
            "strafe_left": "MOVE_LEFT",
            "strafe_right": "MOVE_RIGHT",
            "attack": "ATTACK",
            "use": "USE",
        }
        if request.tool_name in mapping:
            action[self._button_index(mapping[request.tool_name])] = 1.0
        elif request.tool_name in {"turn_left", "turn_right"}:
            action[self._button_index("TURN_LEFT_RIGHT_DELTA")] = (
                -1.0 if request.tool_name == "turn_left" else 1.0
            ) * value
            return action, 1, True
        return action, value, request.tool_name != "wait"

    def _visible_objects(self, state: Any) -> tuple[VisibleObject, ...]:
        game = self._game
        assert game is not None
        half_width = game.get_screen_width() / 2
        placed: list[tuple[str, int, int]] = []
        for label in state.labels:
            name = str(label.object_name)
            if name == _PLAYER_LABEL:
                continue
            center = label.x + label.width / 2
            offset = round((center - half_width) / half_width * _MAX_SCREEN_OFFSET)
            offset = max(-_MAX_SCREEN_OFFSET, min(_MAX_SCREEN_OFFSET, offset))
            placed.append((name, offset, int(label.x)))
        placed.sort()
        return tuple(
            VisibleObject(
                object_id=f"obj_{index:03d}",
                label=name,
                properties={"screen_offset": offset},
            )
            for index, (name, offset, _) in enumerate(placed)
        )

    def _observation(self, action_id: str | None) -> Observation:
        game = self._game
        assert game is not None
        state = game.get_state()
        terminal = game.is_episode_finished() or state is None
        variables = list(state.game_variables) if state is not None else [None, None, None]
        return Observation(
            episode_id=self._episode_id or "doom-unset",
            sequence=self._sequence,
            game_id=GAME_ID,
            scenario_id=self._scenario_id or SCENARIO_IDS[0],
            public_goal=PUBLIC_GOAL,
            status={"health": variables[0], "ammo": variables[1]},
            player=PublicPlayerState(orientation={"angle": variables[2]}),
            visible_objects=() if state is None else self._visible_objects(state),
            last_action_id=action_id,
            terminal=terminal,
            terminal_reason="episode_finished" if terminal else None,
            logical_time=game.get_episode_time(),
        )

    def _rejected(self, request: ToolRequest, started: float) -> StepResult:
        """Refuse a request that was never sent, charging no primitive call.

        The harness records every result as a step with its own sequence, so a
        rejection advances the public sequence exactly as a delivered action does.
        """
        latest = self._latest
        if latest is None:
            raise ConnectorError("reset must succeed before an action is attempted.")
        code = (
            "INVALID_TOOL"
            if request.tool_name not in {tool.name for tool in MANIFEST.tools}
            else "INVALID_ARGUMENT"
        )
        self._sequence += 1
        self._latest = latest.model_copy(
            update={"sequence": self._sequence, "last_action_id": request.action_id}
        )
        return StepResult(
            action_id=request.action_id,
            sequence=self._sequence,
            status="rejected",
            code=code,
            message="Tool name or arguments are invalid.",
            observation=self._latest,
            state_changed=False,
            primitive_actions_charged=0,
            logical_duration=0,
            wall_time_ms=int((time.monotonic() - started) * 1000),
        )

    def _result(
        self,
        request: ToolRequest,
        status: str,
        code: str,
        message: str,
        changed: bool | None,
        duration: int,
        started: float,
    ) -> StepResult:
        self._latest = self._observation(request.action_id)
        return StepResult.model_validate(
            {
                "action_id": request.action_id,
                "sequence": self._sequence,
                "status": status,
                "code": code,
                "message": message,
                "observation": self._latest,
                "state_changed": changed,
                "primitive_actions_charged": 1,
                "logical_duration": duration,
                "wall_time_ms": int((time.monotonic() - started) * 1000),
            }
        )
