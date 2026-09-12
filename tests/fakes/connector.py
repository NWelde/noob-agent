"""A deterministic scripted connector, policy, and clock for runner tests.

Nothing here reads a clock, a network, a game server, or the environment. The
same script always produces the same episode, so a failing assertion means the
runner's behavior changed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from noob_agent.connectors import ConnectorLostError
from noob_agent.domain.model import (
    ConnectorManifest,
    Observation,
    PublicMessage,
    PublicPlayerState,
    PublicPosition,
    StepResult,
    ToolDefinition,
    ToolRequest,
    VisibleObject,
)

GAME_ID = "minecraft"
SCENARIO_ID = "mc_signal_post_a"

MANIFEST = ConnectorManifest(
    connector_version="fake-minecraft-0.1.0",
    game_id=GAME_ID,
    observation_mode="structured_nearby_state",
    timing_model="game_ticks",
    tools=(
        ToolDefinition(
            name="observe",
            description="Refresh the visible nearby state within a radius.",
            argument_schema={"type": "object"},
            max_duration=10,
            state_changing=False,
        ),
        ToolDefinition(
            name="use_object",
            description="Perform the normal use control on a visible object.",
            argument_schema={"type": "object"},
            max_duration=30,
            state_changing=True,
        ),
    ),
)


@dataclass(frozen=True)
class ScriptedStep:
    """One prepared outcome for a request the connector actually accepts."""

    status: Literal["succeeded", "rejected", "failed", "unknown"] = "succeeded"
    code: str = "OK"
    state_changed: bool | None = False
    primitive_actions_charged: int = 1
    logical_duration: int = 0
    wall_time_ms: int = 10
    terminal: bool = False
    terminal_reason: str | None = None
    raises: bool = False


class ScriptedConnector:
    """Replays a fixed script, honouring the manifest's declared tool surface.

    A request naming a tool the manifest does not declare is rejected with
    `INVALID_TOOL` and consumes no scripted step, exactly as a real connector
    would refuse it before sending anything to the game.
    """

    def __init__(
        self,
        *script: ScriptedStep,
        episode_id: str = "ep_0001",
        manifest: ConnectorManifest = MANIFEST,
    ) -> None:
        self._script = list(script)
        self._episode_id = episode_id
        self._manifest = manifest
        self._sequence = 0
        self._logical_time = 0
        self._last_executed_action_id: str | None = None
        self._terminal = False
        self.reset_calls: list[tuple[str, int]] = []
        self.requests: list[ToolRequest] = []
        self.closed = False

    async def manifest(self) -> ConnectorManifest:
        return self._manifest

    async def reset(self, scenario_id: str, seed: int) -> Observation:
        """Restore the declared initial state. Same seed, same observation."""
        self.reset_calls.append((scenario_id, seed))
        self._sequence = 0
        self._logical_time = 0
        self._last_executed_action_id = None
        self._terminal = False
        return self._observation(scenario_id=scenario_id, seed=seed)

    async def step(self, request: ToolRequest) -> StepResult:
        self.requests.append(request)
        declared = {tool.name for tool in self._manifest.tools}
        self._sequence += 1

        if request.tool_name not in declared:
            return self._result(
                request,
                ScriptedStep(
                    status="rejected",
                    code="INVALID_TOOL",
                    primitive_actions_charged=0,
                    state_changed=False,
                ),
            )

        if not self._script:
            raise AssertionError("The scripted connector ran out of steps.")
        scripted = self._script.pop(0)

        if scripted.raises:
            raise ConnectorLostError("The scripted connector dropped the episode.")

        if scripted.status != "rejected":
            self._last_executed_action_id = request.action_id
        self._logical_time += scripted.logical_duration
        self._terminal = scripted.terminal
        return self._result(request, scripted)

    async def is_terminal(self) -> bool:
        return self._terminal

    async def close(self) -> None:
        self.closed = True

    def _result(self, request: ToolRequest, scripted: ScriptedStep) -> StepResult:
        return StepResult(
            action_id=request.action_id,
            sequence=self._sequence,
            status=scripted.status,
            code=scripted.code,
            message=f"{request.tool_name} -> {scripted.status}",
            observation=self._observation(
                last_action_id=(
                    request.action_id
                    if scripted.status != "rejected"
                    else self._last_executed_action_id
                ),
                terminal=scripted.terminal,
                terminal_reason=scripted.terminal_reason,
            ),
            state_changed=scripted.state_changed,
            primitive_actions_charged=scripted.primitive_actions_charged,
            logical_duration=scripted.logical_duration,
            wall_time_ms=scripted.wall_time_ms,
        )

    def _observation(
        self,
        *,
        scenario_id: str = SCENARIO_ID,
        seed: int = 0,
        last_action_id: str | None = None,
        terminal: bool = False,
        terminal_reason: str | None = None,
    ) -> Observation:
        # The seed changes the public layout, so a different seed is a visibly
        # different world while the same seed always rebuilds the same one.
        distance = 9.0 + (seed % 5)
        return Observation(
            episode_id=self._episode_id,
            sequence=self._sequence,
            game_id=GAME_ID,
            scenario_id=scenario_id,
            public_goal="Light the lantern on the post.",
            status={"health": 20, "on_ground": True},
            player=PublicPlayerState(
                position=PublicPosition(x=4.0, y=64.0, z=1.0),
                orientation={"pitch": 0.0, "yaw": 0.0},
                properties={"held_item_id": "item_1"},
            ),
            visible_objects=(
                VisibleObject(
                    object_id="obj_1",
                    label="lantern",
                    position=PublicPosition(x=12.0, y=65.0, z=-4.0),
                    distance=distance,
                    properties={"lit": terminal},
                ),
            ),
            messages=(PublicMessage(kind="observation", text="Nearby state refreshed."),),
            last_action_id=last_action_id,
            terminal=terminal,
            terminal_reason=terminal_reason,
            logical_time=self._logical_time,
        )


class ScriptedPolicy:
    """Chooses prepared calls in order, repeating the last one when exhausted.

    Each call gets a fresh action ID, so repeating the same tool and arguments
    produces genuinely distinct requests — which is what "three identical failed
    calls" means.
    """

    def __init__(self, *calls: tuple[str, dict[str, object]]) -> None:
        self._calls = list(calls) or [("observe", {"radius": 8})]
        self._issued = 0

    async def choose(self, observation: Observation) -> ToolRequest:
        index = min(self._issued, len(self._calls) - 1)
        tool_name, arguments = self._calls[index]
        self._issued += 1
        return ToolRequest(
            action_id=f"a_{self._issued:04d}",
            tool_name=tool_name,
            arguments=dict(arguments),
        )


@dataclass
class FakeClock:
    """A clock that only moves when the test says so."""

    wall: datetime
    elapsed: int = 0
    increments: list[int] = field(default_factory=list)

    def now(self) -> datetime:
        return self.wall

    def monotonic_ms(self) -> int:
        if self.increments:
            self.elapsed += self.increments.pop(0)
        return self.elapsed
