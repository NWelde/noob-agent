"""Runtime failures and readbacks consume the same finite budget as successful work."""

from pathlib import Path

import pytest

from noob_agent.redstone.actions import ActionLimit, Actions, EffectMismatch
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


def test_actual_failed_effect_is_not_inferred_from_command_success(tmp_path: Path) -> None:
    actions = Actions(TrialManifest(tmp_path), Transport(), Reader())
    with pytest.raises(EffectMismatch):
        actions.apply("place", [0, 64, 0], "minecraft:stone")
    assert actions.used >= 3


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
