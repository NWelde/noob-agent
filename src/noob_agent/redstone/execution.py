"""Dependency-aware execution queue for one validated construction intention.

The queue only tracks verified action outcomes. A planned placement is never
treated as an existing support until its action has completed and readback passed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


class DependencyError(ValueError):
    """An intention contains unknown dependencies or a dependency cycle."""


def _field(action: Any, name: str, default: Any = None) -> Any:
    return (
        action.get(name, default) if isinstance(action, Mapping) else getattr(action, name, default)
    )


def _pos(action: Any) -> tuple[int, int, int] | None:
    value = _field(action, "position")
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return value[0], value[1], value[2]
    return None


def _adjacent(first: tuple[int, int, int] | None, second: tuple[int, int, int]) -> bool:
    return first is not None and sum(abs(a - b) for a, b in zip(first, second)) == 1


@dataclass(frozen=True)
class Blocker:
    action_id: str
    dependencies: tuple[str, ...]
    failed_dependencies: tuple[str, ...] = ()


_DIRECTIONS = {
    "north": (0, 0, -1),
    "south": (0, 0, 1),
    "east": (1, 0, 0),
    "west": (-1, 0, 0),
}
_FLOOR_SUPPORTED = frozenset(
    {
        "minecraft:redstone_wire",
        "minecraft:redstone_torch",
        "minecraft:repeater",
        "minecraft:comparator",
    }
)
_ATTACHABLE = _FLOOR_SUPPORTED | frozenset(
    {
        "minecraft:lever",
        "minecraft:redstone_wall_torch",
    }
)


def _support_position(action: Any) -> tuple[int, int, int] | None:
    """Return the unique support cell implied by block and placement properties."""
    pos = _pos(action)
    if pos is None:
        return None
    block = _field(action, "block")
    props = _field(action, "properties", {}) or {}
    if block in _FLOOR_SUPPORTED:
        return pos[0], pos[1] - 1, pos[2]
    if block == "minecraft:lever":
        face = props.get("face")
        if face == "floor":
            return pos[0], pos[1] - 1, pos[2]
        if face == "ceiling":
            return pos[0], pos[1] + 1, pos[2]
        if face == "wall" and props.get("facing") in _DIRECTIONS:
            dx, dy, dz = _DIRECTIONS[props["facing"]]
            return pos[0] - dx, pos[1] - dy, pos[2] - dz
        return None
    if block == "minecraft:redstone_wall_torch":
        facing = props.get("facing")
        if facing in _DIRECTIONS:
            dx, dy, dz = _DIRECTIONS[facing]
            # Wall torch facing points away from its supporting block.
            return pos[0] - dx, pos[1] - dy, pos[2] - dz
    return None


def _layout_cells(verified_layout: Any) -> dict[tuple[int, int, int], dict[str, Any]]:
    """Normalize VerifiedLayout or its public JSON representation."""
    if verified_layout is None:
        return {}
    if hasattr(verified_layout, "to_jsonable"):
        verified_layout = verified_layout.to_jsonable()
    cells: dict[tuple[int, int, int], dict[str, Any]] = {}
    if isinstance(verified_layout, Mapping):
        source = verified_layout.items()
        for position, state in source:
            if (
                isinstance(position, (list, tuple))
                and len(position) == 3
                and isinstance(state, Mapping)
            ):
                cells[tuple(position)] = dict(state)
        return cells
    if isinstance(verified_layout, Iterable) and not isinstance(verified_layout, (str, bytes)):
        for cell in verified_layout:
            if not isinstance(cell, Mapping):
                continue
            position = cell.get("position")
            if isinstance(position, (list, tuple)) and len(position) == 3:
                cells[tuple(position)] = dict(cell)
    return cells


def _supported_at(
    block: str,
    properties: Mapping[str, Any],
    pos: tuple[int, int, int],
    support: tuple[int, int, int],
) -> bool:
    proxy = {"action": "place", "position": pos, "block": block, "properties": properties}
    return _support_position(proxy) == support


class ConstructionExecution:
    """Select only dependency-ready actions; record completion after verification.

    An offer becomes available only when every dependency has a verified success.
    ``finish_status`` explicitly distinguishes clean completion from blocked work.
    """

    def __init__(self, actions: Iterable[Any], *, verified_layout: Any = None):
        items = list(actions)
        self.actions = {str(_field(item, "id")): item for item in items}
        if len(self.actions) != len(items):
            raise DependencyError("Duplicate action IDs")
        self.dependencies: dict[str, set[str]] = {
            action_id: set(_field(item, "depends_on", ()) or ())
            for action_id, item in self.actions.items()
        }
        layout = _layout_cells(verified_layout)
        for action_id, item in self.actions.items():
            pos = _pos(item)
            kind = _field(item, "action")
            # Replacing a cell must first clear its prior occupant, regardless of
            # the planner's input order.
            if kind == "place" and pos is not None:
                self.dependencies[action_id].update(
                    other_id
                    for other_id, other in self.actions.items()
                    if other_id != action_id
                    and _field(other, "action") == "break"
                    and _pos(other) == pos
                )
            # Attachments depend on a support placement included in this intention.
            # The dependency is satisfied only after the support's observed
            # effect_verified outcome is true.
            if kind == "place" and _field(item, "block") in _ATTACHABLE and pos:
                support_pos = _support_position(item)
                neighboring_placements = {
                    other_id
                    for other_id, other in self.actions.items()
                    if other_id != action_id
                    and _field(other, "action") == "place"
                    and _adjacent(_pos(other), pos)
                }
                if support_pos is None:
                    if neighboring_placements and not (
                        self.dependencies[action_id] & neighboring_placements
                    ):
                        raise DependencyError(
                            f"Ambiguous attachment for {action_id}; declare depends_on"
                        )
                    continue
                self.dependencies[action_id].update(
                    other_id
                    for other_id, other in self.actions.items()
                    if other_id != action_id
                    and _field(other, "action") == "place"
                    and _pos(other) == support_pos
                )
            # A support cannot be removed while verified attached hardware is
            # left in place. If that hardware is part of this intention, remove
            # it first; otherwise reject the unsupported teardown plan.
            if kind == "break" and pos is not None:
                attached_existing = {
                    cell_pos
                    for cell_pos, cell in layout.items()
                    if isinstance(cell.get("name"), str)
                    and _supported_at(cell["name"], cell.get("properties", {}), cell_pos, pos)
                }
                for cell_pos in attached_existing:
                    removal_ids = {
                        other_id
                        for other_id, other in self.actions.items()
                        if other_id != action_id
                        and _field(other, "action") == "break"
                        and _pos(other) == cell_pos
                    }
                    if not removal_ids:
                        raise DependencyError(
                            f"Cannot remove support at {pos}; verified attached hardware remains"
                        )
                    self.dependencies[action_id].update(removal_ids)
        for action_id, deps in self.dependencies.items():
            missing = deps - self.actions.keys()
            if missing:
                raise DependencyError(f"Unknown dependency for {action_id}: {sorted(missing)}")
        self._check_cycles()
        self.status = {action_id: "pending" for action_id in self.actions}
        self.outcomes: dict[str, Any] = {}

    def _check_cycles(self) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(action_id: str) -> None:
            if action_id in visiting:
                raise DependencyError("Dependency cycle")
            if action_id in visited:
                return
            visiting.add(action_id)
            for dep in self.dependencies[action_id]:
                visit(dep)
            visiting.remove(action_id)
            visited.add(action_id)

        for action_id in self.actions:
            visit(action_id)

    def eligible(self) -> dict[str, Any]:
        return {
            action_id: self.actions[action_id]
            for action_id in self.actions
            if self.status[action_id] == "pending"
            and all(self.status[dep] == "verified" for dep in self.dependencies[action_id])
        }

    def start(self, action_id: str) -> Any:
        if action_id not in self.eligible():
            raise DependencyError("Action is not currently eligible")
        self.status[action_id] = "running"
        return self.actions[action_id]

    def complete(self, action_id: str, outcome: Any, *, verified: bool) -> None:
        if self.status.get(action_id) != "running":
            raise DependencyError("Action was not started")
        self.outcomes[action_id] = outcome
        self.status[action_id] = "verified" if verified else "failed"

    def blockers(self) -> list[Blocker]:
        blocked: list[Blocker] = []
        for action_id in self.actions:
            if self.status[action_id] != "pending":
                continue
            deps = self.dependencies[action_id]
            unmet = tuple(sorted(dep for dep in deps if self.status[dep] != "verified"))
            if unmet:
                failed = tuple(dep for dep in unmet if self.status[dep] == "failed")
                blocked.append(Blocker(action_id, unmet, failed))
        return blocked

    def finish_status(self) -> dict[str, Any]:
        remaining = [key for key, value in self.status.items() if value != "verified"]
        blockers = self.blockers()
        failures = [key for key, value in self.status.items() if value == "failed"]
        return {
            "complete": not remaining,
            "blocked": bool(blockers or failures),
            "remaining": remaining,
            "failed": failures,
            "blockers": [
                {
                    "action_id": item.action_id,
                    "dependencies": list(item.dependencies),
                    "failed_dependencies": list(item.failed_dependencies),
                }
                for item in blockers
            ]
            + [
                {"action_id": key, "dependencies": [], "failed_dependencies": [key]}
                for key in failures
            ],
        }
