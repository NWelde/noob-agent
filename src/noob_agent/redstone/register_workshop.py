"""One-bit diagnostic for a model-built register; never a register grade."""

from __future__ import annotations

from typing import Any

from noob_agent.redstone.actions import Actions
from noob_agent.redstone.grading import GraderControl
from noob_agent.redstone.modules import ModuleDeclaration, resolve_recipe, validate_declaration


def grade_register_bit(
    actions: Actions, declaration: ModuleDeclaration | dict[str, Any], bit: int
) -> dict[str, Any]:
    """Exercise one LSB-numbered register bit using the trusted timeline.

    The public ``a`` probe array is MSB first, so logical bit 0 maps to probe 3.
    This diagnostic intentionally returns evidence only; ``passed`` here must
    never be consumed as a register checkpoint or full-computer grade.
    """
    if type(bit) is not int or not 0 <= bit < 4:
        raise ValueError("Register diagnostic bit must be 0..3")
    value = declaration.model_dump() if isinstance(declaration, ModuleDeclaration) else declaration
    if not isinstance(value, dict) or value.get("module") != "register":
        raise ValueError("One-bit workshop requires a register declaration")
    validated = validate_declaration(value, actions.contract)

    roles = {control.id: control.role for control in validated.controls}
    recipes = [resolve_recipe(validated, f"load:{n}") for n in (0, 1 << bit, 0)]
    for recipe in recipes:
        for operation in recipe:
            if "control" in operation and roles.get(operation["control"]) not in {
                "test_input",
                "programming",
            }:
                raise ValueError("Register load recipe may only drive declared test inputs")

    reset = next(c.id for c in validated.controls if c.role == "reset")
    grader = GraderControl(actions, validated)
    probe_index = 3 - bit
    grader.probe("a", probe_index)
    # Clock input 0, clock input 1, change the input while STEP stays low,
    # then assert reset while STEP stays low. STEP timing remains grader-owned.
    cycle_recipes: list[list[dict[str, Any]]] = [
        [
            {"control": reset, "level": True},
            {"wait": 200},
            {"control": reset, "level": False},
            {"wait": 200},
            *recipes[0],
        ],
        recipes[1],
        [*recipes[2], {"wait": 200}, {"wait": 200}],
        [
            {"control": reset, "level": True},
            {"wait": 200},
            {"control": reset, "level": False},
            {"wait": 200},
        ],
    ]
    timeline = grader.timeline(
        [],
        cycles=4,
        cycle_recipes=cycle_recipes,
        sample_cycles=[2, 3],
        fail_on_missing_probe=False,
    )
    settled = [s for s in timeline["snapshots"] if s.get("phase") == "settled"]
    observed: list[int | None] = []
    for snapshot in settled:
        values = snapshot.get("signals", {}).get("a")
        signal = values[probe_index] if isinstance(values, list) and len(values) == 4 else None
        observed.append(int(signal > 0) if type(signal) is int and 0 <= signal <= 15 else None)
    # Wire probes expose redstone power (0..15), not a packed logical word.
    expected = [0, 1, 1, 0]
    effects = timeline.get("control_effects", [])
    step_id = next(c.id for c in validated.controls if c.role == "step")
    reset_effects = [e for e in effects if e.get("control") == reset]
    step_effects = [e for e in effects if e.get("control") == step_id]
    evidence = {
        "load_zero": {"observed": observed[0] if len(observed) > 0 else None, "expected": 0},
        "load_one": {"observed": observed[1] if len(observed) > 1 else None, "expected": 1},
        "hold_after_input_change": {
            "observed": observed[2] if len(observed) > 2 else None,
            "expected": 1,
            "step_low": True,
        },
        "reset": {"observed": observed[3] if len(observed) > 3 else None, "expected": 0},
    }
    controls_verified = (
        all(e.get("verified") is True for e in effects)
        and sum(e.get("level") is True for e in step_effects) == 2
        and sum(e.get("level") is False for e in step_effects) >= 2
        and sum(e.get("level") is True for e in reset_effects) == 2
        and sum(e.get("level") is False for e in reset_effects) == 2
    )
    passed = observed == expected and controls_verified
    return {
        "kind": "register_one_bit_diagnostic",
        "bit": bit,
        "probe_index_msb_first": probe_index,
        "passed": passed,
        "checkpoint_eligible": False,
        "fullcomputer_eligible": False,
        "evidence": evidence,
        "control_evidence": {
            "step_effects_verified": bool(step_effects)
            and all(e.get("verified") is True for e in step_effects),
            "reset_effects_verified": bool(reset_effects)
            and all(e.get("verified") is True for e in reset_effects),
            "step_effect_count": len(step_effects),
            "reset_effect_count": len(reset_effects),
        },
        "timeline": timeline,
    }


def register_workshop_setup_metadata() -> dict[str, Any]:
    """Metadata for the optional bare support scaffold, separate from baseline."""
    return {
        "profile": "register_workshop",
        "scenario_version": "register-workshop-v1",
        "baseline_comparable": False,
        "provides_circuit": False,
        "provides_blueprint": False,
        "scaffold": {"block": "minecraft:stone", "dimensions": [9, 1, 9]},
        "setup_charges_construction_budget": False,
        "verification_reads_charged": True,
    }


def scaffold_commands(origin: tuple[int, int, int]) -> list[str]:
    """Return deterministic world commands for a marked 9x1x9 support pad."""
    if type(origin) is not tuple or len(origin) != 3 or any(type(n) is not int for n in origin):
        raise ValueError("Origin must be an integer coordinate tuple")
    x, y, z = origin
    if not (0 <= x <= 87 and 64 <= y <= 91 and 0 <= z <= 87):
        raise ValueError("Workshop scaffold must remain within build bounds")
    return [
        f"fill {x} {y} {z} {x + 8} {y} {z + 8} minecraft:stone",
        f"fill {x} {y + 1} {z} {x + 8} {y + 4} {z + 8} minecraft:air",
        f"setblock {x} {y + 1} {z} minecraft:glass",
    ]


def setup_register_workshop(manifest: Any, transport: Any, actions: Actions) -> dict[str, Any]:
    """Install and journal the bare scaffold outside construction action charges."""
    import hashlib
    from pathlib import Path

    scenario = Path("scenarios/minecraft/register-workshop-v1/scenario.json")
    raw = scenario.read_bytes()
    metadata = register_workshop_setup_metadata()
    metadata["scenario_sha256"] = hashlib.sha256(raw).hexdigest()
    metadata["scenario_path"] = str(scenario)
    metadata["effective_profile"] = "register_workshop"
    metadata["baseline_comparable"] = False
    manifest.data["setup_profile"] = metadata
    manifest.save()
    results = []
    origin = (40, 64, 40)
    metadata["origin"] = list(origin)
    # The marked pad starts inside the build prism; controls/circuit go above it.
    for index, command in enumerate(scaffold_commands(origin)):
        event = manifest.attempt("register_workshop_setup", {"index": index, "command": command})
        try:
            response = transport.command(command)
        except BaseException:
            manifest.observed(event, {"outcome": "unknown"})
            raise RuntimeError("Workshop scaffold setup outcome unknown") from None
        manifest.observed(event, {"response": response})
        results.append({"index": index, "response": response})
    x, y, z = origin
    expected_cells = [
        ([x + dx, y, z + dz], "minecraft:stone") for dx in range(9) for dz in range(9)
    ]
    expected_cells.extend(
        ([x + dx, y + dy, z + dz], "minecraft:air")
        for dx, dz in ((0, 0), (0, 8), (8, 0), (8, 8))
        for dy in range(1, 5)
        if (dx, dy, dz) != (0, 1, 0)
    )
    expected_cells.append(([x, y + 1, z], "minecraft:glass"))
    for position, block in expected_cells:
        actual = actions.observe(position)
        if actual.get("name") != block:
            raise RuntimeError("Workshop scaffold verification failed")
    metadata["verified"] = True
    metadata["verification_reads"] = len(expected_cells)
    manifest.data["setup_profile"]["commands"] = results
    manifest.save()
    return dict(manifest.data["setup_profile"])
