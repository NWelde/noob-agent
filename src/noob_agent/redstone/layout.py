"""Authoritative, observed world layout for the redstone build prism."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

Position = tuple[int, int, int]

_TRANSIENT_PROPERTIES = {
    "minecraft:redstone_wire": {"power"},
    "minecraft:redstone_lamp": {"lit"},
    "minecraft:lever": {"powered"},
    "minecraft:stone_button": {"powered"},
    "minecraft:repeater": {"powered", "locked"},
    "minecraft:comparator": {"powered"},
    "minecraft:redstone_torch": {"lit"},
    "minecraft:redstone_wall_torch": {"lit"},
}


class VerifiedLayout:
    """Track block cells only when an action or readback supplies evidence.

    ``record_action`` accepts the result returned by ``Actions.apply``. It
    intentionally ignores incomplete, failed, and unknown outcomes. Use
    ``reconcile`` only with a completed authoritative block read.
    """

    def __init__(self) -> None:
        self._cells: dict[Position, dict[str, Any]] = {}

    @staticmethod
    def _position(position: object) -> Position:
        if (
            not isinstance(position, (list, tuple))
            or len(position) != 3
            or any(type(value) is not int for value in position)
        ):
            raise ValueError("Position must contain three integers")
        return position[0], position[1], position[2]

    @staticmethod
    def _cell(state: object) -> dict[str, Any]:
        if not isinstance(state, Mapping):
            raise ValueError("Observed block state must be an object")
        name = state.get("name")
        properties = state.get("properties", {})
        if not isinstance(name, str) or not name or not isinstance(properties, Mapping):
            raise ValueError("Observed block state is incomplete")
        return {"name": name, "properties": dict(properties)}

    def record_action(self, action: str, position: object, result: object) -> None:
        """Apply a verified placement/removal result; ignore unknown outcomes."""
        if action not in ("place", "break") or not isinstance(result, Mapping):
            return
        if result.get("effect_verified") is not True:
            return
        try:
            pos = self._position(position)
            cell = self._cell(result.get("after"))
        except (ValueError, TypeError):
            return
        if action == "place":
            self._cells[pos] = cell
        elif cell["name"] == "minecraft:air":
            self._cells.pop(pos, None)
        # A break is only a removal when the observed result is air.

    def reconcile(self, position: object, observed: object) -> None:
        """Replace one cell from a completed authoritative block read."""
        pos = self._position(position)
        cell = self._cell(observed)
        if cell["name"] == "minecraft:air":
            self._cells.pop(pos, None)
        else:
            self._cells[pos] = cell

    def to_jsonable(self) -> list[dict[str, Any]]:
        """Return cells in coordinate order with JSON-native values."""
        return [
            {
                "position": list(position),
                "name": self._cells[position]["name"],
                "properties": dict(sorted(self._cells[position]["properties"].items())),
            }
            for position in sorted(self._cells)
        ]

    def fingerprint(self) -> str:
        """Stable SHA-256 of hardware identity, excluding live signal values."""
        hardware = [
            {
                **cell,
                "properties": {
                    key: value
                    for key, value in cell["properties"].items()
                    if key not in _TRANSIENT_PROPERTIES.get(cell["name"], set())
                },
            }
            for cell in self.to_jsonable()
        ]
        payload = json.dumps(
            hardware, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def validate_declaration_layout(
    layout: VerifiedLayout, declaration: object
) -> list[dict[str, Any]]:
    """Report declared probe/control positions whose expected block is absent.

    This checks only structural block identity. Redstone power, lamp state, and
    other behavior remain the behavioral grader's responsibility.
    """
    if hasattr(declaration, "model_dump"):
        declaration = declaration.model_dump(mode="json")
    if not isinstance(declaration, Mapping):
        raise ValueError("Declaration must be an object")
    probes = declaration.get("probes", {})
    controls = declaration.get("controls", [])
    if not isinstance(probes, Mapping) or not isinstance(controls, list):
        raise ValueError("Declaration probes and controls are malformed")

    mismatches: list[dict[str, Any]] = []

    def check(target: str, position: object, expected: str) -> None:
        pos = VerifiedLayout._position(position)
        actual = layout._cells.get(pos, {"name": "minecraft:air"})["name"]
        if actual != expected:
            mismatches.append(
                {"target": target, "position": list(pos), "expected": expected, "actual": actual}
            )

    for role, role_probes in probes.items():
        if not isinstance(role_probes, list):
            raise ValueError("Declared probes must be lists")
        for index, probe in enumerate(role_probes):
            if hasattr(probe, "model_dump"):
                probe = probe.model_dump(mode="json")
            if not isinstance(probe, Mapping):
                raise ValueError("Declared probe must be an object")
            check(f"probe {role}[{index}]", probe.get("position"), str(probe.get("block")))
    for control in controls:
        if hasattr(control, "model_dump"):
            control = control.model_dump(mode="json")
        if not isinstance(control, Mapping):
            raise ValueError("Declared control must be an object")
        check(f"control {control.get('id')}", control.get("position"), "minecraft:lever")
    return mismatches
