"""Budgeted single-target runtime. No reset, raw commands or bulk operations exposed."""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable
from typing import Any, Protocol

from noob_agent.redstone.contract import load_contract, validate_build_action
from noob_agent.redstone.rcon import UnknownOutcome
from noob_agent.redstone.sidecar import SidecarError
from noob_agent.redstone.trial import CONTRACT_PATH, CommandTransport, TrialManifest


class Reader(Protocol):
    def request(self, request: dict[str, Any]) -> dict[str, Any]: ...


class ActionLimit(RuntimeError):
    pass


class EffectMismatch(RuntimeError):
    pass


class InvalidBlockState(ValueError):
    """Completed registry validation rejected a proposed placement before mutation."""


class ActionAdmissionDenied(RuntimeError):
    """A proposed mutation did not fit in the current intention's reserved budget."""


class ActionEstimate:
    """A conservative upper bound for one bounded action and its readback."""

    def __init__(self, primitives: int, seconds: float) -> None:
        self.primitives = primitives
        self.seconds = seconds


# Deliberately conservative: these are common opaque full cubes with a flat top.
# Slabs, stairs, fences, glass, and other partial/transparent shapes are omitted.
_WIRE_SUPPORT_BLOCKS = frozenset(
    f"minecraft:{name}"
    for name in (
        "stone",
        "granite",
        "polished_granite",
        "diorite",
        "polished_diorite",
        "andesite",
        "polished_andesite",
        "deepslate",
        "cobbled_deepslate",
        "polished_deepslate",
        "cobblestone",
        "mossy_cobblestone",
        "dirt",
        "coarse_dirt",
        "rooted_dirt",
        "grass_block",
        "podzol",
        "mycelium",
        "clay",
        "gravel",
        "sand",
        "red_sand",
        "sandstone",
        "red_sandstone",
        "bricks",
        "stone_bricks",
        "mossy_stone_bricks",
        "obsidian",
        "bedrock",
        "netherrack",
        "end_stone",
        "nether_bricks",
        "blackstone",
        "oak_planks",
        "spruce_planks",
        "birch_planks",
        "jungle_planks",
        "acacia_planks",
        "dark_oak_planks",
        "mangrove_planks",
        "cherry_planks",
        "bamboo_planks",
        "crimson_planks",
        "warped_planks",
    )
)


def _safe_actual_support(support: dict[str, Any]) -> dict[str, Any]:
    name = support.get("name")
    safe_name = (
        name if isinstance(name, str) and re.fullmatch(r"minecraft:[a-z0-9_]+", name) else "unknown"
    )
    raw_properties = support.get("properties", {})
    safe_properties: dict[str, str | bool | int] = {}
    if isinstance(raw_properties, dict):
        for key, value in raw_properties.items():
            if not isinstance(key, str) or not re.fullmatch(r"[a-z_]+", key):
                continue
            if type(value) is bool or type(value) is int:
                safe_properties[key] = value
            elif isinstance(value, str) and re.fullmatch(r"[a-z0-9_]+", value.lower()):
                safe_properties[key] = value.lower()
    return {"name": safe_name, "properties": safe_properties}


class Actions:
    def __init__(
        self,
        manifest: TrialManifest,
        transport: CommandTransport,
        reader: Reader,
        *,
        max_actions: int | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.contract = load_contract(CONTRACT_PATH)
        self.manifest, self.transport, self.reader = manifest, transport, reader
        limit = self.contract.budgets.primitive_actions
        if max_actions is not None and (
            type(max_actions) is not int or not 0 < max_actions <= limit
        ):
            raise ValueError("Invalid action limit")
        self.maximum = limit if max_actions is None else max_actions
        self.clock, self.started = clock, clock()
        self.used = 0
        self.stopped = False
        self.guard: Callable[[bool], None] | None = None
        self.admit: Callable[[ActionEstimate], None] | None = None
        # The loop can scope its intention guard to action admission while still
        # enforcing global wall and primitive deadlines throughout the action.
        self.action_active = False

    def estimate(
        self,
        action: str,
        position: list[int],
        block: str | None = None,
        properties: dict[str, Any] | None = None,
    ) -> ActionEstimate:
        """Return the maximum primitive and bounded I/O cost for ``apply``.

        Counts include the outer action record, all preflight reads, the world
        command/interact, settling, and final readback. Persistent sidecar
        interaction reconciliation can add three charged recovery operations.
        """
        if action == "observe":
            primitive_count, reader_calls = 1, 1
        elif action == "interact":
            primitive_count, reader_calls = 10, 7
            if getattr(self.reader, "keep_connected", False):
                primitive_count += 3
                reader_calls += 3
        elif action == "break":
            primitive_count, reader_calls = 5, 3
        elif action == "place":
            is_wire = block == "minecraft:redstone_wire"
            primitive_count = 6 + int(is_wire)
            reader_calls = 4 + int(is_wire)
        else:
            # Invalid proposals still consume their bounded outer attempt.
            primitive_count, reader_calls = 1, 0
        reader_timeout = self._timeout(self.reader, "timeout", 12.0)
        command_timeout = self._timeout(self.transport, "timeout", 5.0)
        command_calls = 2 if action == "interact" else int(action in {"place", "break"})
        # Reconnect may consume the 35s socket-discovery window, then the
        # persistent-agent 25s ready handshake. Include both in this envelope.
        reconnect_seconds = (
            60.2 if action == "interact" and getattr(self.reader, "keep_connected", False) else 0.0
        )
        return ActionEstimate(
            primitive_count,
            reader_calls * reader_timeout + command_calls * command_timeout + reconnect_seconds,
        )

    @staticmethod
    def _timeout(adapter: object, attribute: str, default: float) -> float:
        """Read a concrete transport timeout without accepting unsafe metadata."""
        value = getattr(adapter, attribute, default)
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 30:
            raise ValueError("Transport timeout metadata must be finite and in (0, 30]")
        return float(value)

    @staticmethod
    def position(position: list[int]) -> tuple[int, int, int]:
        if not isinstance(position, list) or len(position) != 3:
            raise ValueError("Expected one target")
        return position[0], position[1], position[2]

    def stop_unknown(self) -> None:
        self.stopped = True
        self.manifest.data["action_budget"] = {
            "used": self.used,
            "limit": self.maximum,
            "stopped": True,
            "reason": "unknown_transport_outcome",
        }
        self.manifest.save()

    def check(self) -> None:
        if self.guard is not None:
            self.guard(False)
        if self.stopped or self.clock() - self.started >= self.contract.budgets.wall_seconds:
            self.stopped = True
            self.manifest.data["action_budget"] = {
                "used": self.used,
                "limit": self.maximum,
                "stopped": True,
                "reason": "time_or_stopped",
            }
            self.manifest.save()
            raise ActionLimit("Runtime stopped or time exhausted")

    def charge(self, kind: str, request: dict[str, Any]) -> int:
        self.check()
        if self.guard is not None:
            self.guard(True)
        if self.used >= self.maximum:
            self.stopped = True
            self.manifest.data["action_budget"] = {
                "used": self.used,
                "limit": self.maximum,
                "stopped": True,
            }
            self.manifest.save()
            raise ActionLimit("Action budget exhausted")
        self.used += 1
        self.manifest.data["action_budget"] = {
            "used": self.used,
            "limit": self.maximum,
            "stopped": False,
        }
        return self.manifest.attempt(kind, request)

    def read(self, request: dict[str, Any]) -> dict[str, Any]:
        sequence = self.charge("charged_observation", request)
        try:
            result = self.reader.request(request)
            self.manifest.observed(sequence, result)
            self.check()
            return result
        except (SidecarError, UnknownOutcome):
            self.stop_unknown()
            raise

    def observe(self, position: list[int]) -> dict[str, Any]:
        if self.admit is None or self.action_active:
            return self._observe(position)
        estimate = self.estimate("observe", position)
        if estimate.primitives > self.maximum - self.used:
            raise ActionAdmissionDenied("Observation cannot fit in remaining primitive budget")
        self.admit(estimate)
        self.action_active = True
        try:
            return self._observe(position)
        finally:
            self.action_active = False

    def _observe(self, position: list[int]) -> dict[str, Any]:
        sequence = self.charge("charged_observation", {"op": "block", "position": position})
        validate_build_action(self.contract, "break", self.position(position))
        try:
            result = self.reader.request({"op": "block", "position": position})
            self.manifest.observed(sequence, result)
            self.check()
            return result
        except (SidecarError, UnknownOutcome):
            self.stop_unknown()
            raise

    def apply(
        self,
        action: str,
        position: list[int],
        block: str | None = None,
        properties: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request = {"action": action, "position": position, "block": block, "properties": properties}
        estimate = self.estimate(action, position, block, properties)
        try:
            validate_build_action(self.contract, action, self.position(position), block)
            if action != "place" and (block is not None or properties is not None):
                raise ValueError("Unexpected block state")
            state = {} if properties is None else properties
            if not isinstance(state, dict) or any(
                not isinstance(key, str)
                or not re.fullmatch("[a-z_]+", key)
                or not re.fullmatch("[a-z0-9_]+", str(value).lower())
                for key, value in state.items()
            ):
                raise ValueError("Invalid property syntax")
        except BaseException as error:
            sequence = self.charge("bounded_action", request)
            self.manifest.data["errors"].append(
                {"stage": "bounded_action", "type": type(error).__name__, "sequence": sequence}
            )
            self.manifest.save()
            raise
        self.check()
        if estimate.primitives > self.maximum - self.used:
            raise ActionAdmissionDenied("Action cannot fit in remaining primitive budget")
        if self.admit is not None:
            self.admit(estimate)
        sequence = self.charge("bounded_action", request)
        self.action_active = True
        try:
            if action == "place":
                validation = self.read({"op": "validate", "name": block, "properties": state})
                if validation == {"valid": False}:
                    self.manifest.observed(sequence, {"rejected": "invalid_block_state"})
                    raise InvalidBlockState("Block state rejected by Minecraft registry")
            before = self.observe(position)
            if (
                before["name"] != "minecraft:air"
                and before["name"] not in self.contract.permitted_blocks
            ):
                raise ValueError("Target block is not permitted")
            if action == "place" and block == "minecraft:redstone_wire":
                support_position = [position[0], position[1] - 1, position[2]]
                support = self.read({"op": "block", "position": support_position})
                if support.get("name") not in _WIRE_SUPPORT_BLOCKS:
                    diagnostic = {
                        "type": "unsupported_support",
                        "target": position,
                        "required_support": {
                            "position": support_position,
                            "state": "full_top_solid_block",
                        },
                        "actual_support": _safe_actual_support(support),
                    }
                    self.manifest.observed(sequence, {"placement_diagnostic": diagnostic})
                    raise EffectMismatch(
                        "Redstone wire needs a full solid support block directly below"
                    )
            if action == "interact":
                pose = self.read({"op": "player"})
                coordinates = pose.get("position")
                yaw, pitch = pose.get("yaw"), pose.get("pitch")
                if (
                    not isinstance(coordinates, list)
                    or len(coordinates) != 3
                    or any(
                        type(v) not in (int, float) or not math.isfinite(v)
                        for v in [*coordinates, yaw, pitch]
                    )
                    or pose.get("orientationUnits") != "radians"
                ):
                    raise ValueError("Invalid observed player pose")
                command = (
                    f"tp noobagentbot {position[0] + 0.5} {position[1] + 1} {position[2] + 0.5}"
                )
                event = self.charge("bounded_movement", {"command": command})
                self.manifest.observed(event, {"response": self.transport.command(command)})
                self.read({"op": "settle"})
                self.read({"op": "interact", "position": position})
                command = (
                    f"tp noobagentbot {' '.join(map(str, coordinates))} "
                    f"{math.degrees(yaw)} {math.degrees(pitch)}"
                )
                event = self.charge("bounded_movement", {"command": command})
                self.manifest.observed(event, {"response": self.transport.command(command)})
                self.read({"op": "settle"})
                restored = self.read({"op": "player"})
                if any(
                    abs(a - b) > 0.01
                    for a, b in zip(restored.get("position", []), coordinates, strict=True)
                ):
                    raise EffectMismatch("Player position was not restored after interaction")
            else:
                target = "minecraft:air" if action == "break" else block
                suffix = (
                    "[" + ",".join(f"{k}={str(v).lower()}" for k, v in sorted(state.items())) + "]"
                    if state
                    else ""
                )
                command = f"setblock {' '.join(map(str, position))} {target}{suffix}"
                self.check()
                event = self.charge("bounded_command", {"command": command})
                self.manifest.observed(event, {"response": self.transport.command(command)})
                self.read({"op": "settle"})
            after = self.observe(position)
            expected = "minecraft:air" if action == "break" else block
            matched = after["name"] == expected and all(
                after["properties"].get(k) == v for k, v in state.items()
            )
            if action == "interact":
                matched = (
                    before["name"] == after["name"] == "minecraft:lever"
                    and type(before["properties"].get("powered")) is bool
                    and after["properties"].get("powered") is not before["properties"]["powered"]
                )
            result = {"before": before, "after": after, "effect_verified": matched}
            self.manifest.observed(sequence, result)
            if not matched:
                raise EffectMismatch("Actual effect differs from request")
            return result
        except BaseException as error:
            if isinstance(error, (SidecarError, UnknownOutcome)):
                self.stop_unknown()
            self.manifest.data["errors"].append(
                {"stage": "bounded_action", "type": type(error).__name__, "sequence": sequence}
            )
            self.manifest.save()
            raise
        finally:
            self.action_active = False
