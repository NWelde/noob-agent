"""Budgeted single-target runtime. No reset, raw commands or bulk operations exposed."""

from __future__ import annotations

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
        sequence = self.charge("bounded_action", request)
        try:
            validate_build_action(self.contract, action, self.position(position), block)
            if action != "place" and (block is not None or properties is not None):
                raise ValueError("Unexpected block state")
            state = {} if properties is None else properties
            if not isinstance(state, dict) or any(
                not re.fullmatch("[a-z_]+", key)
                or not re.fullmatch("[a-z0-9_]+", str(value).lower())
                for key, value in state.items()
            ):
                raise ValueError("Invalid property syntax")
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
            if action == "interact":
                self.read({"op": "interact", "position": position})
            else:
                target = "minecraft:air" if action == "break" else block
                suffix = (
                    "[" + ",".join(f"{k}={str(v).lower()}" for k, v in sorted(state.items())) + "]"
                    if state
                    else ""
                )
                command = f"setblock {' '.join(map(str, position))} {target}{suffix}"
                self.check()
                event = self.manifest.attempt("bounded_command", {"command": command})
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
