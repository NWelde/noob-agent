"""Acceptance tests for the Minecraft connector's reset and one primitive.

These cover build-order step 1 from `hackathon_plan.md` section 13: a real
Minecraft connector that resets a scenario and performs one hard-coded action.

Most tests drive the connector through a substituted sidecar transport, so
schema validity, tool rejection, private-state filtering, and timeout
classification are deterministic and need no game server. The two live tests
require the local server and the installed Node sidecar from
`docs/minecraft-server.md`; they skip with a clear reason when either is
missing, and never hang or silently pass.
"""

from __future__ import annotations

from typing import Any

import pytest

from noob_agent.connectors import ConnectorError
from noob_agent.connectors.minecraft import (
    DEFERRED_TOOLS,
    GAME_ID,
    MAX_OBSERVE_RADIUS,
    MinecraftConnector,
    MinecraftSettings,
    SidecarUnavailableError,
)
from noob_agent.domain.model import Observation, StepResult, ToolRequest

SCENARIO_ID = "resonator-training-v1"
SEED = 20260912

# One public snapshot shaped exactly as the Node sidecar reports it.
SIDECAR_SNAPSHOT: dict[str, Any] = {
    "logical_time": 1200,
    "terminal": False,
    "terminal_reason": None,
    "player": {
        "position": {"x": 0.5, "y": 100.0, "z": -5.5},
        "orientation": {"yaw": 0.0, "pitch": 0.0},
        "properties": {"game_mode": "adventure", "held_item": None},
    },
    "status": {"health": 20.0, "food": 20.0, "on_ground": True},
    "visible_objects": [
        {
            "label": "Dull Shard",
            "position": {"x": 3.0, "y": 100.0, "z": -2.0},
            "distance": 4.3,
            "properties": {"kind": "item", "count": 1},
        },
        {
            "label": "Resonator",
            "position": {"x": -2.0, "y": 100.0, "z": 2.0},
            "distance": 8.1,
            "properties": {"kind": "block"},
        },
    ],
    "messages": [{"kind": "game", "text": "The device does not respond."}],
}


class StubTransport:
    """A sidecar stand-in that replays prepared replies without a game server."""

    def __init__(
        self,
        *replies: dict[str, Any],
        fail_on_send: bool = False,
        never_replies: bool = False,
    ) -> None:
        self._replies = list(replies)
        self._fail_on_send = fail_on_send
        self._never_replies = never_replies
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    async def start(self) -> None:
        return None

    async def send(self, message: dict[str, Any]) -> None:
        if self._fail_on_send:
            # Nothing reached the sidecar, so delivery is confirmed not to have
            # happened.
            raise TimeoutError("the sidecar never accepted the request")
        self.sent.append(message)

    async def receive(self, *, timeout: float) -> dict[str, Any]:
        if self._never_replies:
            # Delivered, but the result can no longer be confirmed.
            raise TimeoutError("the sidecar did not answer in time")
        if not self._replies:
            raise AssertionError("the connector asked for more replies than were prepared")
        return self._replies.pop(0)

    async def close(self) -> None:
        self.closed = True


def observe_reply(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"ok": True, "observation": snapshot if snapshot is not None else SIDECAR_SNAPSHOT}


def stubbed(*replies: dict[str, Any], **kwargs: Any) -> tuple[MinecraftConnector, StubTransport]:
    transport = StubTransport(*replies, **kwargs)
    connector = MinecraftConnector(MinecraftSettings(), transport=transport)
    return connector, transport


def repeatable_public_state(observation: Observation) -> dict[str, Any]:
    """The public state a repeated reset on one seed must reproduce exactly.

    `episode_id` identifies the attempt rather than the restored world, and
    `logical_time` is the server's own tick counter, so neither is part of
    reset repeatability.
    """
    return observation.model_dump(exclude={"episode_id", "logical_time"})


# --- Manifest and frozen public surface ------------------------------------


async def test_manifest_declares_observe_and_defers_the_remaining_primitives() -> None:
    connector, _ = stubbed()

    manifest = await connector.manifest()

    assert manifest.game_id == GAME_ID
    assert manifest.observation_mode == "structured_nearby_state"
    assert manifest.timing_model == "game_ticks"
    assert [tool.name for tool in manifest.tools] == ["observe"]
    observe = manifest.tools[0]
    assert observe.state_changing is False
    # A tool name must never describe the unfamiliar mechanic's solution.
    assert "resonator" not in observe.description.lower()
    assert "move_to" in DEFERRED_TOOLS and "use_object" in DEFERRED_TOOLS


async def test_manifest_is_unchanged_by_a_reset() -> None:
    connector, _ = stubbed(observe_reply())
    before = await connector.manifest()

    await connector.reset(SCENARIO_ID, SEED)

    assert await connector.manifest() == before


# --- Reset ------------------------------------------------------------------


async def test_reset_returns_a_schema_valid_sequence_zero_observation() -> None:
    connector, transport = stubbed(observe_reply())

    observation = await connector.reset(SCENARIO_ID, SEED)

    assert Observation.model_validate(observation.model_dump()) == observation
    assert observation.sequence == 0
    assert observation.scenario_id == SCENARIO_ID
    assert observation.game_id == GAME_ID
    assert observation.last_action_id is None
    assert observation.terminal is False
    assert observation.public_goal
    assert await connector.is_terminal() is False
    # Reset must actually ask the sidecar to restore the declared start state.
    assert any(message.get("op") == "reset" for message in transport.sent)


async def test_reset_is_repeatable_for_the_same_seed_and_layout() -> None:
    connector, _ = stubbed(observe_reply(), observe_reply())

    first = await connector.reset(SCENARIO_ID, SEED)
    second = await connector.reset(SCENARIO_ID, SEED)

    assert repeatable_public_state(first) == repeatable_public_state(second)


async def test_reset_assigns_opaque_object_ids_that_do_not_encode_purpose() -> None:
    connector, _ = stubbed(observe_reply())

    observation = await connector.reset(SCENARIO_ID, SEED)

    labels = {item.label for item in observation.visible_objects}
    assert labels == {"Dull Shard", "Resonator"}
    for item in observation.visible_objects:
        assert item.object_id not in labels
        assert item.label.lower().split()[0] not in item.object_id.lower()


# --- One hard-coded primitive action ---------------------------------------


async def test_observe_round_trips_one_schema_valid_step_result() -> None:
    connector, transport = stubbed(observe_reply(), observe_reply())
    await connector.reset(SCENARIO_ID, SEED)
    request = ToolRequest(action_id="a_0001", tool_name="observe", arguments={"radius": 5})

    result = await connector.step(request)

    assert StepResult.model_validate(result.model_dump()) == result
    assert result.action_id == "a_0001"
    assert result.status == "succeeded"
    assert result.code == "OK"
    assert result.sequence == 1
    assert result.observation.sequence == 1
    assert result.observation.last_action_id == "a_0001"
    assert result.state_changed is False
    assert result.primitive_actions_charged == 1
    assert result.wall_time_ms >= 0
    observe_calls = [message for message in transport.sent if message.get("op") == "observe"]
    assert observe_calls[-1]["radius"] == 5


async def test_unknown_tool_is_rejected_before_anything_reaches_the_game() -> None:
    connector, transport = stubbed(observe_reply())
    await connector.reset(SCENARIO_ID, SEED)
    sent_during_reset = len(transport.sent)

    result = await connector.step(
        ToolRequest(action_id="a_0002", tool_name="charge_keystone", arguments={})
    )

    assert result.status == "rejected"
    assert result.code == "INVALID_TOOL"
    assert result.action_id == "a_0002"
    assert result.primitive_actions_charged == 0
    assert result.state_changed is False
    assert len(transport.sent) == sent_during_reset


async def test_a_deferred_contract_tool_is_rejected_as_an_undeclared_tool() -> None:
    connector, _ = stubbed(observe_reply())
    await connector.reset(SCENARIO_ID, SEED)

    result = await connector.step(
        ToolRequest(action_id="a_0003", tool_name="use_object", arguments={"object_id": "obj_0001"})
    )

    assert result.status == "rejected"
    assert result.code == "INVALID_TOOL"


@pytest.mark.parametrize("radius", [0, MAX_OBSERVE_RADIUS + 1, "five", None])
async def test_an_out_of_range_observe_radius_is_rejected(radius: object) -> None:
    connector, transport = stubbed(observe_reply())
    await connector.reset(SCENARIO_ID, SEED)
    sent_during_reset = len(transport.sent)

    result = await connector.step(
        ToolRequest(action_id="a_0004", tool_name="observe", arguments={"radius": radius})
    )

    assert result.status == "rejected"
    assert result.code == "INVALID_ARGUMENT"
    assert result.primitive_actions_charged == 0
    assert len(transport.sent) == sent_during_reset


async def test_a_rejected_request_does_not_advance_the_public_sequence() -> None:
    connector, _ = stubbed(observe_reply(), observe_reply())
    await connector.reset(SCENARIO_ID, SEED)
    accepted = await connector.step(ToolRequest(action_id="a_0005", tool_name="observe"))

    rejected = await connector.step(ToolRequest(action_id="a_0006", tool_name="no_such_tool"))

    assert rejected.sequence == accepted.sequence
    assert rejected.observation.sequence == accepted.observation.sequence


# --- Timeout classification -------------------------------------------------


async def test_a_timeout_after_possible_delivery_is_unknown() -> None:
    connector, _ = stubbed(observe_reply(), never_replies=True)

    # The request was written to the sidecar, so it may have changed the game.
    result = await connector.step(ToolRequest(action_id="a_0007", tool_name="observe"))

    assert result.status == "unknown"
    assert result.code == "TIMEOUT_UNKNOWN"
    assert result.state_changed is None
    assert result.primitive_actions_charged == 1
    assert result.action_id == "a_0007"


async def test_a_timeout_before_delivery_is_a_confirmed_failure() -> None:
    connector, _ = stubbed(observe_reply(), fail_on_send=True)

    result = await connector.step(ToolRequest(action_id="a_0008", tool_name="observe"))

    assert result.status == "failed"
    assert result.code == "TIMEOUT_CONFIRMED"
    assert result.state_changed is False
    assert result.primitive_actions_charged == 1


async def test_a_sidecar_error_reply_becomes_a_failed_result_not_an_exception() -> None:
    connector, _ = stubbed(observe_reply(), {"ok": False, "code": "GAME_REJECTED", "error": "no"})
    await connector.reset(SCENARIO_ID, SEED)

    result = await connector.step(ToolRequest(action_id="a_0009", tool_name="observe"))

    assert result.status == "failed"
    assert result.code == "GAME_REJECTED"
    assert result.observation.sequence == result.sequence


async def test_a_timeout_during_reset_is_raised_as_a_connector_error() -> None:
    connector, _ = stubbed(never_replies=True)

    with pytest.raises(ConnectorError):
        await connector.reset(SCENARIO_ID, SEED)


# --- Trust boundary ---------------------------------------------------------


async def test_private_scenario_state_is_filtered_out_of_every_observation() -> None:
    leaky = {
        **SIDECAR_SNAPSHOT,
        "status": {
            **SIDECAR_SNAPSHOT["status"],
            "scenario_variant": "faulty",
            "grader_score": 3,
            "noob_agent.state": 2,
        },
        "visible_objects": [
            {
                "label": "Dull Shard",
                "position": {"x": 3.0, "y": 100.0, "z": -2.0},
                "distance": 4.3,
                "properties": {"kind": "item", "required_count": 2, "is_solution_input": True},
            }
        ],
        "private_predicate_passed": True,
    }
    connector, _ = stubbed(observe_reply(leaky))

    observation = await connector.reset(SCENARIO_ID, SEED)

    serialized = observation.model_dump_json()
    for private in ("faulty", "grader_score", "noob_agent.state", "required_count", "solution"):
        assert private not in serialized
    assert observation.status["health"] == 20.0
    assert observation.visible_objects[0].properties["kind"] == "item"


async def test_close_releases_the_sidecar() -> None:
    connector, transport = stubbed(observe_reply())
    await connector.reset(SCENARIO_ID, SEED)

    await connector.close()

    assert transport.closed is True
    # Closing twice must stay safe for the harness.
    await connector.close()


# --- Live server and sidecar (skipped when unavailable) --------------------


async def live_connector() -> MinecraftConnector:
    """Return a started connector, or skip when the game side is unavailable."""
    connector = MinecraftConnector()
    try:
        await connector.start()
    except (SidecarUnavailableError, ConnectorError, OSError) as unavailable:
        await connector.close()
        pytest.skip(f"live Minecraft server or Node sidecar unavailable: {unavailable}")
    return connector


async def test_live_reset_is_repeatable_on_a_fixed_seed() -> None:
    connector = await live_connector()
    try:
        first = await connector.reset(SCENARIO_ID, SEED)
        second = await connector.reset(SCENARIO_ID, SEED)
    finally:
        await connector.close()

    assert first.sequence == 0 and second.sequence == 0
    assert repeatable_public_state(first) == repeatable_public_state(second)


async def test_live_observe_performs_one_hard_coded_action() -> None:
    connector = await live_connector()
    try:
        await connector.reset(SCENARIO_ID, SEED)
        result = await connector.step(
            ToolRequest(action_id="live_0001", tool_name="observe", arguments={"radius": 5})
        )
    finally:
        await connector.close()

    assert StepResult.model_validate(result.model_dump()) == result
    assert result.status == "succeeded"
    assert result.action_id == "live_0001"
    assert result.sequence == 1
    assert result.observation.player.position is not None
