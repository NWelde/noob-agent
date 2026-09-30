"""Assisted-demo construction admission, independent of planner claims."""

from collections.abc import Iterable, Sequence

from noob_agent.redstone.planner import Intention

ORDER = ("register", "arithmetic", "storage", "output")
REGIONS = {
    "register": (4, 35, 4, 31),
    "arithmetic": (40, 87, 4, 31),
    "storage": (4, 87, 38, 67),
    "output": (4, 35, 74, 87),
}


def current_module(passed: Iterable[str]) -> str:
    return next((module for module in ORDER if module not in passed), "machine")


def admit(intention: Intention, passed: Iterable[str]) -> None:
    current = current_module(passed)
    if intention.module_inspection is not None and intention.module_inspection.module != current:
        raise ValueError(f"Strict module gate: grade {current} before another module")
    if not intention.actions:
        return
    if intention.circuit_plan is not None and intention.circuit_plan.module != current:
        raise ValueError(f"Strict module gate: actions require circuit_plan module {current}")
    if current == "machine":
        return
    x0, x1, z0, z1 = REGIONS[current]
    for action in intention.actions:
        x, _, z = action.position
        if action.action != "observe" and not (x0 <= x <= x1 and z0 <= z <= z1):
            raise ValueError(
                f"Strict module gate: {current} construction is restricted to "
                f"x {x0}..{x1}, z {z0}..{z1}; finish its behavioral grade first"
            )


def affected_modules(position: Sequence[int], passed: Iterable[str]) -> set[str]:
    """Conservative region plus electrical adjacency; later integration regrades all."""
    x, _, z = position
    return {
        module
        for module in passed
        if REGIONS[module][0] - 2 <= x <= REGIONS[module][1] + 2
        and REGIONS[module][2] - 2 <= z <= REGIONS[module][3] + 2
    }
