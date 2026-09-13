"""ViZDoom implementation of the fixed public Doom connector surface."""

from __future__ import annotations

import importlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
CONNECTOR_VERSION = "doom-vizdoom-v1"
PUBLIC_GOAL = "Explore the visible area and survive the scenario."
MAX_TICKS = 35
MAX_DEGREES = 90
START_TICKS = 14


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


class DoomConnector:
    """Headless ViZDoom connector; it exposes only HUD variables and labels."""

    def __init__(self, settings: DoomSettings | None = None) -> None:
        self._settings = settings or DoomSettings()
        self._game: Any | None = None
        self._buttons: list[Any] = []
        self._scenario_id: str | None = None
        self._episode_id: str | None = None
        self._latest: Observation | None = None
        self._sequence = 0
        self._resets = 0

    async def manifest(self) -> ConnectorManifest:
        return MANIFEST

    async def reset(self, scenario_id: str, seed: int) -> Observation:
        game = self._ensure_game(scenario_id)
        started = time.monotonic()
        game.set_seed(seed)
        game.new_episode()
        if time.monotonic() - started > self._settings.reset_timeout_seconds:
            raise ConnectorError("Doom reset exceeded its timeout.")
        self._resets += 1
        self._scenario_id, self._sequence = scenario_id, 0
        self._episode_id = f"doom-{scenario_id}-s{seed}-r{self._resets:03d}"
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
        game, self._game = self._game, None
        if game is not None:
            game.close()

    def _ensure_game(self, scenario_id: str) -> Any:
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
            action[[button.name for button in self._buttons].index(mapping[request.tool_name])] = (
                1.0
            )
        elif request.tool_name in {"turn_left", "turn_right"}:
            action[[button.name for button in self._buttons].index("TURN_LEFT_RIGHT_DELTA")] = (
                -1.0 if request.tool_name == "turn_left" else 1.0
            ) * value
            return action, 1, True
        return action, value, request.tool_name != "wait"

    def _observation(self, action_id: str | None) -> Observation:
        game = self._game
        assert game is not None
        state = game.get_state()
        terminal = game.is_episode_finished() or state is None
        variables = list(state.game_variables) if state is not None else [None, None, None]
        labels = (
            ()
            if state is None
            else tuple(
                VisibleObject(object_id=f"obj_{index:03d}", label=str(label.object_name))
                for index, label in enumerate(
                    sorted(state.labels, key=lambda item: item.object_name)
                )
            )
        )
        return Observation(
            episode_id=self._episode_id or "doom-unset",
            sequence=self._sequence,
            game_id=GAME_ID,
            scenario_id=self._scenario_id or self._settings.scenario,
            public_goal=PUBLIC_GOAL,
            status={"health": variables[0], "ammo": variables[1]},
            player=PublicPlayerState(orientation={"angle": variables[2]}),
            visible_objects=labels,
            last_action_id=action_id,
            terminal=terminal,
            terminal_reason="episode_finished" if terminal else None,
            logical_time=game.get_episode_time(),
        )

    def _rejected(self, request: ToolRequest, started: float) -> StepResult:
        latest = self._latest
        if latest is None:
            raise ConnectorError("reset must succeed before an action is attempted.")
        code = (
            "INVALID_TOOL"
            if request.tool_name not in {tool.name for tool in MANIFEST.tools}
            else "INVALID_ARGUMENT"
        )
        return StepResult(
            action_id=request.action_id,
            sequence=self._sequence,
            status="rejected",
            code=code,
            message="Tool name or arguments are invalid.",
            observation=latest.model_copy(update={"last_action_id": request.action_id}),
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
