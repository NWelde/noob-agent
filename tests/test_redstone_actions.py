"""Runtime failures and readbacks consume the same finite budget as successful work."""

from pathlib import Path

import pytest

from noob_agent.redstone.actions import (
    ActionAdmissionDenied,
    ActionLimit,
    Actions,
    EffectMismatch,
    InvalidBlockState,
)
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

    event = next(event for event in manifest.data["events"] if event["kind"] == "bounded_action")
    diagnostic = event["result"]["placement_diagnostic"]
    assert diagnostic == {
        "type": "unsupported_support",
        "target": [12, 65, 34],
        "required_support": {
            "position": [12, 64, 34],
            "state": "full_top_solid_block",
        },
        "actual_support": {"name": "minecraft:air", "properties": {}},
    }
    assert actions.transport.commands == []


def test_wire_preflight_allows_verified_full_block_support(tmp_path: Path) -> None:
    class SupportedReader(Reader):
        def request(self, request: dict) -> dict:
            if request["op"] == "block" and request["position"] == [12, 64, 34]:
                return {"name": "minecraft:stone", "properties": {}, "position": [12, 64, 34]}
            if request["op"] == "block" and request["position"] == [12, 65, 34]:
                return {
                    "name": "minecraft:redstone_wire",
                    "properties": {"power": 0},
                    "position": [12, 65, 34],
                }
            return super().request(request)

    transport = Transport()
    actions = Actions(TrialManifest(tmp_path), transport, SupportedReader())
    result = actions.apply("place", [12, 65, 34], "minecraft:redstone_wire")
    assert result["effect_verified"] is True
    assert len(transport.commands) == 1
    estimate = actions.estimate("place", [12, 65, 34], "minecraft:redstone_wire")
    assert actions.used == estimate.primitives


def test_admission_denial_has_feedback_hook_and_never_starts_action(tmp_path: Path) -> None:
    manifest = TrialManifest(tmp_path)
    transport = Transport()
    actions = Actions(manifest, transport, Reader())
    seen = []

    def deny(estimate):
        seen.append(estimate)
        raise ActionAdmissionDenied("intention has no room for complete bounded action")

    actions.admit = deny
    with pytest.raises(ActionAdmissionDenied):
        actions.apply("place", [0, 64, 0], "minecraft:stone")

    assert seen[0].primitives == 6
    assert seen[0].seconds == 53
    assert actions.used == 0
    assert transport.commands == []
    assert not any(event["kind"] == "bounded_action" for event in manifest.data["events"])


@pytest.mark.parametrize("bad_timeout", [float("nan"), float("inf"), True, 31])
def test_estimate_rejects_unsafe_transport_timeout_metadata(
    tmp_path: Path, bad_timeout: object
) -> None:
    class TimedReader(Reader):
        timeout = bad_timeout

    actions = Actions(TrialManifest(tmp_path), Transport(), TimedReader())
    with pytest.raises(ValueError, match="Transport timeout metadata"):
        actions.estimate("observe", [0, 64, 0])


def test_persistent_interaction_estimate_includes_reconnect_handshake(tmp_path: Path) -> None:
    class PersistentReader(Reader):
        keep_connected = True
        timeout = 12

    actions = Actions(TrialManifest(tmp_path), Transport(), PersistentReader())
    estimate = actions.estimate("interact", [0, 64, 0])

    assert estimate.primitives == 13
    assert estimate.seconds == 190.2


def test_lever_activation_positions_bot_and_restores_observed_pose(tmp_path: Path) -> None:
    transport = Transport()

    class LeverReader(Reader):
        def __init__(self) -> None:
            self.powered = False

        def request(self, request: dict) -> dict:
            if request["op"] == "player":
                return {
                    "position": [48.5, 64, 98.5],
                    "yaw": 3.141592653589793,
                    "pitch": 0,
                    "orientationUnits": "radians",
                }
            if request["op"] == "interact":
                assert transport.commands == ["tp noobagentbot 10.5 66 10.5"]
                self.powered = not self.powered
                return {}
            if request["op"] == "block":
                return {
                    "name": "minecraft:lever",
                    "properties": {"powered": self.powered},
                    "position": request["position"],
                }
            return super().request(request)

    actions = Actions(TrialManifest(tmp_path), transport, LeverReader())
    assert actions.apply("interact", [10, 65, 10])["effect_verified"] is True
    assert transport.commands[-1] == "tp noobagentbot 48.5 64 98.5 180.0 0.0"
    assert actions.used == actions.estimate("interact", [10, 65, 10]).primitives


def test_initial_primitive_capacity_must_fit_whole_action(tmp_path: Path) -> None:
    manifest = TrialManifest(tmp_path)
    transport = Transport()
    actions = Actions(manifest, transport, Reader(), max_actions=5)

    with pytest.raises(ActionAdmissionDenied):
        actions.apply("place", [0, 64, 0], "minecraft:stone")

    assert actions.used == 0
    assert transport.commands == []


def test_admitted_action_can_finish_when_intention_charge_cap_is_reached(tmp_path: Path) -> None:
    class MutableReader(Reader):
        placed = False

        def request(self, request: dict) -> dict:
            if request["op"] == "block" and self.placed:
                return {
                    "name": "minecraft:stone",
                    "properties": {},
                    "position": request["position"],
                }
            return super().request(request)

    class PlacingTransport(Transport):
        def __init__(self, reader):
            super().__init__()
            self.reader = reader

        def command(self, command: str) -> str:
            self.commands.append(command)
            self.reader.placed = True
            return "Changed the block"

    reader = MutableReader()
    actions = Actions(TrialManifest(tmp_path), PlacingTransport(reader), reader)

    def intention_cap_guard(charging: bool) -> None:
        # Models the loop's intention policy: once an admitted action starts,
        # its reserved charges and readback are allowed to finish.
        if charging and actions.used >= 1 and not actions.action_active:
            raise ActionLimit("intention action limit exhausted")

    actions.guard = intention_cap_guard
    result = actions.apply("place", [0, 64, 0], "minecraft:stone")

    assert result["effect_verified"] is True
    assert actions.used == 6
    assert actions.action_active is False


@pytest.mark.parametrize(
    "support_name", ["minecraft:torch", "minecraft:oak_slab", "minecraft:oak_stairs"]
)
def test_wire_preflight_rejects_non_full_top_support_before_mutation(
    tmp_path: Path, support_name: str
) -> None:
    class SupportReader(Reader):
        def request(self, request: dict) -> dict:
            if request["op"] == "block" and request["position"] == [12, 64, 34]:
                return {"name": support_name, "properties": {"type": "bottom"}}
            return super().request(request)

    manifest = TrialManifest(tmp_path)
    transport = Transport()
    actions = Actions(manifest, transport, SupportReader())
    with pytest.raises(EffectMismatch):
        actions.apply("place", [12, 65, 34], "minecraft:redstone_wire")
    assert transport.commands == []
    event = next(event for event in manifest.data["events"] if event["kind"] == "bounded_action")
    assert event["result"]["placement_diagnostic"]["type"] == "unsupported_support"


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
    assert actions.action_active is False
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
