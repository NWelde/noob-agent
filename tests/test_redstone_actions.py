"""Runtime failures and readbacks consume the same finite budget as successful work."""

from pathlib import Path

import pytest

from noob_agent.redstone.actions import ActionLimit, Actions, EffectMismatch, InvalidBlockState
from noob_agent.redstone.trial import TrialManifest


class Transport:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def command(self, command: str) -> str:
        self.commands.append(command)
        return "Changed the block"

    def close(self) -> None:
        pass


class Reader:
    def request(self, request: dict) -> dict:
        if request["op"] == "validate":
            return {"name": request["name"], "properties": request["properties"]}
        if request["op"] == "settle":
            return {"settled": True}
        return {"name": "minecraft:air", "properties": {}, "position": request["position"]}


def test_invalid_attempts_and_observations_are_charged(tmp_path: Path) -> None:
    manifest = TrialManifest(tmp_path)
    transport = Transport()
    actions = Actions(manifest, transport, Reader(), max_actions=2)
    with pytest.raises(ValueError):
        actions.apply("place", [-1, 64, 0], "minecraft:stone")
    assert actions.used == 1
    assert actions.observe([0, 64, 0])["name"] == "minecraft:air"
    with pytest.raises(ActionLimit):
        actions.observe([0, 64, 0])
    assert actions.used == 2
    assert transport.commands == []


def test_completed_registry_rejection_is_recorded_without_world_command(tmp_path: Path) -> None:
    class RejectingReader(Reader):
        def request(self, request: dict) -> dict:
            if request["op"] == "validate":
                return {"valid": False}
            return super().request(request)

    manifest = TrialManifest(tmp_path)
    transport = Transport()
    actions = Actions(manifest, transport, RejectingReader())
    with pytest.raises(InvalidBlockState):
        actions.apply("place", [48, 64, 90], "minecraft:lever", {"facing": "up"})
    assert transport.commands == []
    assert not actions.stopped
    action = next(event for event in manifest.data["events"] if event["kind"] == "bounded_action")
    assert action["outcome"] == "observed"
    assert action["result"] == {"rejected": "invalid_block_state"}


def test_actual_failed_effect_is_not_inferred_from_command_success(tmp_path: Path) -> None:
    actions = Actions(TrialManifest(tmp_path), Transport(), Reader())
    with pytest.raises(EffectMismatch):
        actions.apply("place", [0, 64, 0], "minecraft:stone")
    assert actions.used >= 3


def test_failed_wire_placement_records_missing_support_diagnostic(tmp_path: Path) -> None:
    manifest = TrialManifest(tmp_path)
    actions = Actions(manifest, Transport(), Reader())

    with pytest.raises(EffectMismatch):
        actions.apply("place", [12, 65, 34], "minecraft:redstone_wire")

    event = next(
        event
        for event in manifest.data["events"]
        if event["kind"] == "bounded_action"
    )
    diagnostic = event["result"]["placement_diagnostic"]
    assert diagnostic == {
        "type": "missing_support",
        "target": [12, 65, 34],
        "required_support": {
            "position": [12, 64, 34],
            "state": "solid block",
        },
        "actual_support": {"name": "minecraft:air", "properties": {}},
    }


def test_time_exhaustion_prevents_mutation(tmp_path: Path) -> None:
    now = [0.0]
    transport = Transport()
    actions = Actions(TrialManifest(tmp_path), transport, Reader(), clock=lambda: now[0])
    now[0] = 3600
    with pytest.raises(ActionLimit):
        actions.apply("place", [0, 64, 0], "minecraft:stone")
    assert transport.commands == []


def test_nonwhitelisted_or_injected_state_never_reaches_command(tmp_path: Path) -> None:
    transport = Transport()
    actions = Actions(TrialManifest(tmp_path), transport, Reader())
    for block, properties in [
        ("minecraft:command_block", {}),
        ("minecraft:stone", {"bad;stop": "x"}),
    ]:
        with pytest.raises(ValueError):
            actions.apply("place", [0, 64, 0], block, properties)
    assert not transport.commands


def test_unknown_mutation_stops_all_later_work_and_keeps_pending_event(tmp_path: Path) -> None:
    from noob_agent.redstone.rcon import UnknownOutcome

    class Lost(Transport):
        def command(self, command: str) -> str:
            raise UnknownOutcome("not safe to retry")

    manifest = TrialManifest(tmp_path)
    actions = Actions(manifest, Lost(), Reader())
    with pytest.raises(UnknownOutcome):
        actions.apply("place", [0, 64, 0], "minecraft:stone")
    assert actions.stopped
    assert manifest.data["action_budget"]["stopped"] is True
    assert manifest.data["events"][-1]["outcome"] == "unknown"
    with pytest.raises(ActionLimit):
        actions.observe([0, 64, 0])


def test_readback_crossing_wall_deadline_stops_runtime(tmp_path: Path) -> None:
    now = [0.0]

    class Slow(Reader):
        def request(self, request: dict) -> dict:
            now[0] = 3601
            return super().request(request)

    actions = Actions(TrialManifest(tmp_path), Transport(), Slow(), clock=lambda: now[0])
    with pytest.raises(ActionLimit):
        actions.observe([0, 64, 0])
    assert actions.stopped
