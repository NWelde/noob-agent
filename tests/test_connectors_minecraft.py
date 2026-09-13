"""Acceptance tests for the complete Minecraft primitive connector.

These cover build-order step 1 and the primitive-completion milestone from
`hackathon_plan.md` sections 13 and 15.

Most tests drive the connector through a substituted sidecar transport, so
schema validity, tool rejection, private-state filtering, and timeout
classification are deterministic and need no game server. The two live tests
require the local server and the installed Node sidecar from
`docs/minecraft-server.md`; they skip with a clear reason when either is
missing, and never hang or silently pass.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fakes.connector import ScriptedPolicy

from noob_agent.connectors import ConnectorError
from noob_agent.connectors.minecraft import (
    CONNECTOR_VERSION,
    GAME_ID,
    MAX_OBSERVE_RADIUS,
    MinecraftConnector,
    MinecraftSettings,
    SidecarUnavailableError,
)
from noob_agent.domain.model import Observation, StepResult, ToolRequest
from noob_agent.domain.records import ExperimentRecord
from noob_agent.runtime import EpisodeRunner
from noob_agent.storage import EpisodeStore

SCENARIO_ID = "resonator-training-v1"
SEED = 20260912
SIDECAR = Path("src/noob_agent/connectors/minecraft_sidecar")

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


async def test_manifest_declares_the_exact_frozen_minecraft_primitives() -> None:
    connector, _ = stubbed()

    manifest = await connector.manifest()

    assert manifest.game_id == GAME_ID
    assert manifest.observation_mode == "structured_nearby_state"
    assert manifest.timing_model == "game_ticks"
    assert manifest.connector_version == CONNECTOR_VERSION == "minecraft-0.2.0"
    assert [tool.name for tool in manifest.tools] == [
        "observe",
        "move_to",
        "look_at",
        "inspect_object",
        "collect_object",
        "use_object",
        "place_object",
        "wait",
    ]
    assert {tool.name: tool.state_changing for tool in manifest.tools} == {
        "observe": False,
        "move_to": True,
        "look_at": True,
        "inspect_object": False,
        "collect_object": True,
        "use_object": True,
        "place_object": True,
        "wait": False,
    }
    serialized = manifest.model_dump_json().lower()
    assert "resonator" not in serialized
    assert "gateway" not in serialized


async def test_manifest_freezes_each_primitive_argument_schema() -> None:
    connector, _ = stubbed()
    manifest = await connector.manifest()

    schemas = {tool.name: tool.argument_schema for tool in manifest.tools}
    assert schemas == {
        "observe": {
            "type": "object",
            "properties": {"radius": {"type": "integer", "minimum": 1, "maximum": 8, "default": 5}},
            "additionalProperties": False,
        },
        "move_to": {
            "type": "object",
            "properties": {
                "x": {"type": "number"},
                "y": {"type": "number"},
                "z": {"type": "number"},
                "tolerance": {"type": "integer", "enum": [1, 2]},
            },
            "required": ["x", "y", "z", "tolerance"],
            "additionalProperties": False,
        },
        "look_at": _object_id_schema(),
        "inspect_object": _object_id_schema(),
        "collect_object": {
            "type": "object",
            "properties": {
                "object_id": {"type": "string", "minLength": 1},
                "count": {"type": "integer", "minimum": 1, "maximum": 8},
            },
            "required": ["object_id", "count"],
            "additionalProperties": False,
        },
        "use_object": {
            "type": "object",
            "properties": {
                "object_id": {"type": "string", "minLength": 1},
                "held_item_id": {"type": "string", "minLength": 1},
            },
            "required": ["object_id"],
            "additionalProperties": False,
        },
        "place_object": {
            "type": "object",
            "properties": {
                "held_item_id": {"type": "string", "minLength": 1},
                "x": {"type": "number"},
                "y": {"type": "number"},
                "z": {"type": "number"},
            },
            "required": ["held_item_id", "x", "y", "z"],
            "additionalProperties": False,
        },
        "wait": {
            "type": "object",
            "properties": {"ticks": {"type": "integer", "minimum": 1, "maximum": 100}},
            "required": ["ticks"],
            "additionalProperties": False,
        },
    }


def _object_id_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"object_id": {"type": "string", "minLength": 1}},
        "required": ["object_id"],
        "additionalProperties": False,
    }


def test_default_player_name_matches_the_offline_server_whitelist() -> None:
    assert MinecraftSettings().username == "noobagentbot"


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


# --- Primitive actions ------------------------------------------------------


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


@pytest.mark.parametrize(
    ("tool_name", "arguments", "expected"),
    [
        (
            "move_to",
            {"x": 1.5, "y": 100, "z": -2.5, "tolerance": 1},
            {"x": 1.5, "y": 100.0, "z": -2.5, "tolerance": 1},
        ),
        ("look_at", {"object_id": "obj_0002"}, {"target": "Resonator"}),
        ("inspect_object", {"object_id": "obj_0002"}, {"target": "Resonator"}),
        (
            "collect_object",
            {"object_id": "obj_0001", "count": 1},
            {"target": "Dull Shard", "count": 1},
        ),
        ("use_object", {"object_id": "obj_0002"}, {"target": "Resonator"}),
        ("wait", {"ticks": 20}, {"ticks": 20}),
    ],
)
async def test_each_primitive_maps_to_one_sidecar_operation(
    tool_name: str, arguments: dict[str, Any], expected: dict[str, Any]
) -> None:
    connector, transport = stubbed(observe_reply(), observe_reply())
    await connector.reset(SCENARIO_ID, SEED)

    result = await connector.step(
        ToolRequest(action_id=f"a_{tool_name}", tool_name=tool_name, arguments=arguments)
    )

    assert result.status == "succeeded"
    assert result.code == "OK"
    assert result.primitive_actions_charged == 1
    assert (
        result.state_changed
        is {
            "move_to": True,
            "look_at": True,
            "inspect_object": False,
            "collect_object": True,
            "use_object": True,
            "wait": False,
        }[tool_name]
    )
    sent = transport.sent[-1]
    assert sent["op"] == tool_name
    assert sent["radius"] == 5
    if tool_name not in {"observe", "wait", "look_at"}:
        assert sent["settle_ticks"] == 5
    for key, value in expected.items():
        if key == "target":
            assert sent[key]["label"] == value
        else:
            assert sent[key] == value


async def test_place_object_resolves_a_public_inventory_item_id() -> None:
    inventory_snapshot = {
        **SIDECAR_SNAPSHOT,
        "visible_objects": [
            *SIDECAR_SNAPSHOT["visible_objects"],
            {
                "label": "Slate Chip",
                "position": None,
                "distance": 0,
                "properties": {"kind": "inventory_item", "count": 1},
            },
        ],
    }
    connector, transport = stubbed(observe_reply(inventory_snapshot), observe_reply())
    observation = await connector.reset(SCENARIO_ID, SEED)
    held_id = next(
        item.object_id
        for item in observation.visible_objects
        if item.properties.get("kind") == "inventory_item"
    )

    result = await connector.step(
        ToolRequest(
            action_id="a_place",
            tool_name="place_object",
            arguments={"held_item_id": held_id, "x": 0.5, "y": 101, "z": 0.5},
        )
    )

    assert result.status == "succeeded"
    sent = transport.sent[-1]
    assert sent["op"] == "place_object"
    assert sent["held_item"]["label"] == "Slate Chip"
    assert sent["position"] == {"x": 0.5, "y": 101.0, "z": 0.5}


async def test_use_object_can_select_an_optional_public_inventory_item() -> None:
    inventory_snapshot = {
        **SIDECAR_SNAPSHOT,
        "visible_objects": [
            *SIDECAR_SNAPSHOT["visible_objects"],
            {
                "label": "Slate Chip",
                "position": None,
                "distance": 0,
                "properties": {"kind": "inventory_item", "count": 1},
            },
        ],
    }
    connector, transport = stubbed(observe_reply(inventory_snapshot), observe_reply())
    observation = await connector.reset(SCENARIO_ID, SEED)
    held = next(
        item
        for item in observation.visible_objects
        if item.properties.get("kind") == "inventory_item"
    )
    target = next(item for item in observation.visible_objects if item.label == "Resonator")

    result = await connector.step(
        ToolRequest(
            action_id="a_use_held",
            tool_name="use_object",
            arguments={"object_id": target.object_id, "held_item_id": held.object_id},
        )
    )

    assert result.status == "succeeded"
    sent = transport.sent[-1]
    assert sent["target"] == {
        "label": "Resonator",
        "kind": "block",
        "position": {"x": -2.0, "y": 100.0, "z": 2.0},
    }
    assert sent["held_item"] == {"label": "Slate Chip", "kind": "inventory_item"}


async def test_success_uses_the_sidecars_confirmed_state_change_value() -> None:
    reply = {**observe_reply(), "state_changed": False}
    connector, _ = stubbed(observe_reply(), reply)
    await connector.reset(SCENARIO_ID, SEED)

    result = await connector.step(
        ToolRequest(
            action_id="a_already_there",
            tool_name="move_to",
            arguments={"x": 0.5, "y": 100, "z": -5.5, "tolerance": 1},
        )
    )

    assert result.status == "succeeded"
    assert result.state_changed is False


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("move_to", {"x": True, "y": 100, "z": 0, "tolerance": 1}),
        ("move_to", {"x": 0, "y": 100, "z": 0, "tolerance": 3}),
        ("look_at", {"object_id": "obj_0001", "extra": 1}),
        ("inspect_object", {}),
        ("collect_object", {"object_id": "obj_0001", "count": 0}),
        ("use_object", {"object_id": 1}),
        (
            "place_object",
            {"held_item_id": "obj_0001", "x": float("inf"), "y": 100, "z": 0},
        ),
        ("wait", {"ticks": 101}),
    ],
)
async def test_invalid_primitive_arguments_are_rejected_without_delivery(
    tool_name: str, arguments: dict[str, Any]
) -> None:
    connector, transport = stubbed(observe_reply())
    await connector.reset(SCENARIO_ID, SEED)
    sent_during_reset = len(transport.sent)

    result = await connector.step(
        ToolRequest(action_id=f"bad_{tool_name}", tool_name=tool_name, arguments=arguments)
    )

    assert result.status == "rejected"
    assert result.code == "INVALID_ARGUMENT"
    assert result.primitive_actions_charged == 0
    assert len(transport.sent) == sent_during_reset


@pytest.mark.parametrize("tool_name", ["look_at", "inspect_object", "collect_object", "use_object"])
async def test_unknown_object_ids_are_rejected_without_delivery(tool_name: str) -> None:
    connector, transport = stubbed(observe_reply())
    await connector.reset(SCENARIO_ID, SEED)
    sent_during_reset = len(transport.sent)
    arguments: dict[str, Any] = {"object_id": "obj_missing"}
    if tool_name == "collect_object":
        arguments["count"] = 1

    result = await connector.step(
        ToolRequest(action_id=f"missing_{tool_name}", tool_name=tool_name, arguments=arguments)
    )

    assert result.status == "rejected"
    assert result.code == "NO_VISIBLE_TARGET"
    assert result.primitive_actions_charged == 0
    assert len(transport.sent) == sent_during_reset


async def test_collect_and_place_enforce_public_object_preconditions() -> None:
    connector, transport = stubbed(observe_reply())
    await connector.reset(SCENARIO_ID, SEED)
    sent_during_reset = len(transport.sent)

    collect = await connector.step(
        ToolRequest(
            action_id="bad_collect",
            tool_name="collect_object",
            arguments={"object_id": "obj_0002", "count": 1},
        )
    )
    place = await connector.step(
        ToolRequest(
            action_id="bad_place",
            tool_name="place_object",
            arguments={"held_item_id": "obj_0001", "x": 0, "y": 101, "z": 0},
        )
    )

    assert collect.code == "PRECONDITION_FAILED"
    assert place.code == "PRECONDITION_FAILED"
    assert len(transport.sent) == sent_during_reset


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


async def test_a_rejected_request_is_a_durable_result_with_its_own_sequence() -> None:
    """Every request receives one durable result, and each recorded result needs
    its own sequence: the runner records a rejection as a step, so a rejection
    must advance the public sequence exactly like a delivered primitive."""
    connector, _ = stubbed(observe_reply(), observe_reply(), observe_reply())
    await connector.reset(SCENARIO_ID, SEED)
    accepted = await connector.step(ToolRequest(action_id="a_0005", tool_name="observe"))

    rejected = await connector.step(ToolRequest(action_id="a_0006", tool_name="no_such_tool"))
    after = await connector.step(ToolRequest(action_id="a_0007", tool_name="observe"))

    assert accepted.sequence == 1
    assert rejected.sequence == 2
    assert rejected.observation.sequence == 2
    assert rejected.observation.last_action_id == "a_0006"
    assert rejected.primitive_actions_charged == 0
    assert after.sequence == 3
    assert after.observation.sequence == 3


async def test_the_runner_keeps_going_after_a_live_rejection(store: EpisodeStore) -> None:
    """A rejected call costs one decision and no primitive; it must not end the
    episode as an unknown result because of a stale sequence number."""
    connector, _ = stubbed(observe_reply(), observe_reply())
    experiment = ExperimentRecord(
        experiment_id="exp_live_reject",
        model_id="scripted-policy",
        condition="cold",
        connector_version="test",
        decision_budget=2,
        primitive_budget=40,
        wall_time_budget_ms=180_000,
        created_at=datetime(2026, 9, 12, 16, 0, tzinfo=UTC),
    )
    store.create_experiment(experiment)
    policy = ScriptedPolicy(("charge_keystone", {}), ("observe", {"radius": 4}))
    runner = EpisodeRunner(connector=connector, store=store, policy=policy)

    result = await runner.run(experiment=experiment, scenario_id=SCENARIO_ID, seed=SEED)

    assert result.stop_reason == "decision_limit"
    assert result.decisions_used == 2
    assert result.primitives_used == 1
    stored = store.read_episode(result.episode_id)
    assert [step.result.status for step in stored.steps] == ["rejected", "succeeded"]
    assert [step.sequence for step in stored.steps] == [1, 2]


# --- Timeout classification -------------------------------------------------


async def test_a_timeout_after_possible_delivery_is_unknown() -> None:
    connector, transport = stubbed(observe_reply())
    await connector.reset(SCENARIO_ID, SEED)
    transport._never_replies = True

    # The request was written to the sidecar, so it may have changed the game.
    result = await connector.step(ToolRequest(action_id="a_0007", tool_name="observe"))

    assert result.status == "unknown"
    assert result.code == "TIMEOUT_UNKNOWN"
    assert result.state_changed is None
    assert result.primitive_actions_charged == 1
    assert result.action_id == "a_0007"


async def test_a_timeout_before_delivery_is_a_confirmed_failure() -> None:
    connector, transport = stubbed(observe_reply())
    await connector.reset(SCENARIO_ID, SEED)
    transport._fail_on_send = True

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
    except (SidecarUnavailableError, OSError) as unavailable:
        await connector.close()
        pytest.skip(f"live Minecraft server or Node sidecar unavailable: {unavailable}")
    return connector


def test_sidecar_is_a_pinned_json_lines_mineflayer_package() -> None:
    package = json.loads((SIDECAR / "package.json").read_text(encoding="utf-8"))
    source = (SIDECAR / "index.js").read_text(encoding="utf-8")

    assert package["private"] is True
    assert package["dependencies"] == {"mineflayer": "4.39.0"}
    assert package["scripts"]["start"] == "node index.js"
    for operation in (
        '"connect"',
        '"reset"',
        '"observe"',
        '"move_to"',
        '"look_at"',
        '"inspect_object"',
        '"collect_object"',
        '"use_object"',
        '"place_object"',
        '"wait"',
        '"close"',
    ):
        assert operation in source
    assert "process.stdout.write" in source


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


async def test_live_complete_primitive_surface() -> None:
    connector = await live_connector()
    try:
        observation = await connector.reset(SCENARIO_ID, SEED)
        move = await connector.step(
            ToolRequest(
                action_id="live_move",
                tool_name="move_to",
                arguments={"x": -3.5, "y": 100, "z": -0.5, "tolerance": 1},
            )
        )
        assert move.status == "succeeded"

        observed = await connector.step(
            ToolRequest(action_id="live_wide", tool_name="observe", arguments={"radius": 8})
        )
        supply = next(
            item
            for item in observed.observation.visible_objects
            if item.label == "Barrel"
            and item.position is not None
            and item.position.x == -4
            and item.position.z == 0
        )
        looked = await connector.step(
            ToolRequest(
                action_id="live_look",
                tool_name="look_at",
                arguments={"object_id": supply.object_id},
            )
        )
        assert looked.status == "succeeded"

        inspected = await connector.step(
            ToolRequest(
                action_id="live_inspect",
                tool_name="inspect_object",
                arguments={"object_id": supply.object_id},
            )
        )
        assert inspected.status == "succeeded"
        shard = next(
            item
            for item in inspected.observation.visible_objects
            if item.label == "Dull Shard" and item.properties.get("kind") == "container_item"
        )

        collected = await connector.step(
            ToolRequest(
                action_id="live_collect",
                tool_name="collect_object",
                arguments={"object_id": shard.object_id, "count": 2},
            )
        )
        assert collected.status == "succeeded"
        held = next(
            item
            for item in collected.observation.visible_objects
            if item.label == "Dull Shard" and item.properties.get("kind") == "inventory_item"
        )

        moved_back = await connector.step(
            ToolRequest(
                action_id="live_move_back",
                tool_name="move_to",
                arguments={"x": 0.5, "y": 100, "z": -0.5, "tolerance": 1},
            )
        )
        assert moved_back.status == "succeeded"
        held = next(
            item
            for item in moved_back.observation.visible_objects
            if item.label == "Dull Shard" and item.properties.get("kind") == "inventory_item"
        )
        placed = await connector.step(
            ToolRequest(
                action_id="live_place",
                tool_name="place_object",
                arguments={"held_item_id": held.object_id, "x": 0.5, "y": 101.25, "z": 0.5},
            )
        )
        assert placed.status == "succeeded"

        button = next(
            item
            for item in placed.observation.visible_objects
            if item.label == "Stone Button"
            and item.position is not None
            and item.position.x == 0
            and item.position.z == 2
        )
        used = await connector.step(
            ToolRequest(
                action_id="live_use",
                tool_name="use_object",
                arguments={"object_id": button.object_id},
            )
        )
        assert used.status == "succeeded"

        waited = await connector.step(
            ToolRequest(action_id="live_wait", tool_name="wait", arguments={"ticks": 5})
        )
        assert waited.status == "succeeded"
        assert waited.sequence == 9
        assert observation.sequence == 0
    finally:
        await connector.close()
