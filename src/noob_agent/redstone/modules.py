"""Public interface declarations and charged world evidence, never circuit designs.

This inspection is a prerequisite to behavioral grading, not a module grader.
Bit arrays are MSB first; stored words flatten address 0..7, each bit 5..0.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from noob_agent.redstone.actions import Actions
from noob_agent.redstone.contract import FrozenModel, MachineContract, validate_build_action
from noob_agent.redstone.trial import RUN_DIRECTORY, TrialManifest


class Probe(FrozenModel):
    position: list[int] = Field(min_length=3, max_length=3)
    block: Literal["minecraft:redstone_wire", "minecraft:redstone_lamp"]


class Control(FrozenModel):
    id: str = Field(min_length=1, max_length=40, pattern=r"^[a-zA-Z0-9_-]+$")
    role: Literal["reset", "step", "programming", "test_input"]
    position: list[int] = Field(min_length=3, max_length=3)


class ParameterBit(FrozenModel):
    parameter: Literal["value", "address", "word"]
    bit: int = Field(ge=0, le=5)
    invert: bool = False


class TemplateLevel(FrozenModel):
    control: str
    level: bool | ParameterBit


class TemplateWait(FrozenModel):
    wait: int = Field(ge=1, le=200)


PARAMETERS = {
    "load": {"value": 4},
    "add": {"value": 4},
    "address": {"address": 3},
    "write": {"address": 3, "word": 6},
}


class ModuleDeclaration(FrozenModel):
    module: Literal["register", "arithmetic", "storage", "output"]
    probes: dict[str, list[Probe]]
    controls: list[Control] = Field(min_length=2, max_length=66)
    recipes: dict[str, list[dict[str, Any]]] = Field(default_factory=dict, max_length=160)
    recipe_templates: dict[
        Literal["load", "add", "address", "write"], list[TemplateLevel | TemplateWait]
    ] = Field(default_factory=dict)


class MachineDeclaration(ModuleDeclaration):
    """Full machine interface; programming IDs are address-major, MSB first."""

    # Pydantic replaces the inherited discriminator for the full-machine schema.
    # Keep ModuleDeclaration restricted to public modules in the planner schema.
    module: Literal["machine"]  # type: ignore[assignment]
    word_controls: list[list[str]] = Field(min_length=8, max_length=8)


def resolve_recipe(declaration: ModuleDeclaration, key: str) -> list[dict[str, Any]]:
    """Expand only bounded bit selections; no expressions, commands or layouts."""
    if key in declaration.recipes:
        return declaration.recipes[key]
    family, *arguments = key.split(":")
    if family not in declaration.recipe_templates:
        raise ValueError("Missing recipe: " + key)
    widths = PARAMETERS[family]
    if len(arguments) != len(widths):
        raise ValueError("Invalid recipe arguments")
    values = {}
    for argument, (parameter, width) in zip(arguments, widths.items(), strict=True):
        if argument not in {str(n) for n in range(1 << width)}:
            raise ValueError("Invalid recipe argument")
        values[parameter] = int(argument)
    expanded: list[dict[str, Any]] = []
    for operation in declaration.recipe_templates[family]:
        if isinstance(operation, TemplateWait):
            expanded.append({"wait": operation.wait})
        else:
            level = operation.level
            if isinstance(level, ParameterBit):
                level = bool(values[level.parameter] & (1 << level.bit)) ^ level.invert
            expanded.append({"control": operation.control, "level": level})
    return expanded


WIDTHS = {
    "machine": {"a": 4, "o": 4, "pc": 3, "halted": 1, "strobe": 1, "words": 48},
    "register": {"a": 4},
    "arithmetic": {"a": 4, "o": 4},
    "storage": {"words": 48, "readout": 6, "address": 3},
    "output": {"o": 4, "strobe": 1},
}


def validate_declaration(value: object, contract: MachineContract) -> ModuleDeclaration:
    declaration = (
        MachineDeclaration.model_validate(value)
        if isinstance(value, dict) and value.get("module") == "machine"
        else ModuleDeclaration.model_validate(value)
    )
    widths = WIDTHS[declaration.module]
    if set(declaration.probes) != set(widths):
        raise ValueError("Missing or unexpected probe roles")
    labelled_positions: list[tuple[str, list[int]]] = []
    for role, width in widths.items():
        probes = declaration.probes[role]
        if len(probes) != width:
            raise ValueError("Incorrect probe width")
        for index, probe in enumerate(probes):
            if role == "o" and probe.block != "minecraft:redstone_lamp":
                raise ValueError("Visible output requires lamps")
            labelled_positions.append((f"{role} probe {index}", probe.position))
    ids = [control.id for control in declaration.controls]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate control IDs")
    roles = [control.role for control in declaration.controls]
    if isinstance(declaration, MachineDeclaration):
        flat = [key for row in declaration.word_controls for key in row]
        programming = {c.id for c in declaration.controls if c.role == "programming"}
        if any(len(row) != 6 for row in declaration.word_controls) or len(set(flat)) != 48:
            raise ValueError("Machine requires 48 distinct programming controls")
        if set(flat) != programming or any(role == "test_input" for role in roles):
            raise ValueError("Machine accepts only RESET, STEP and its 48 programming controls")
        if declaration.recipes or declaration.recipe_templates:
            raise ValueError("Machine has no external instruction-execution recipes")
    if roles.count("reset") != 1 or roles.count("step") != 1:
        raise ValueError("Exactly one reset and STEP are required")
    if declaration.module == "storage" and "programming" not in roles:
        raise ValueError("Storage requires programming controls")
    labelled_positions.extend(
        (f"control {control.id}", control.position) for control in declaration.controls
    )
    seen_positions: dict[tuple[int, int, int], str] = {}
    for label, position in labelled_positions:
        position_key = (position[0], position[1], position[2])
        prior = seen_positions.get(position_key)
        if prior is not None:
            raise ValueError(
                f"Aliased {prior} and {label} at x {position_key[0]} "
                f"y {position_key[1]} z {position_key[2]}"
            )
        seen_positions[position_key] = label
    for label, position in labelled_positions:
        try:
            validate_build_action(contract, "break", (position[0], position[1], position[2]))
        except ValueError as error:
            if str(error) != "Target outside inclusive build bounds":
                raise
            x, y, z = position
            lower, upper = contract.build_min, contract.build_max
            raise ValueError(
                f"Declared {label} at x {x} y {y} z {z} outside build bounds "
                f"x {lower[0]}..{upper[0]} y {lower[1]}..{upper[1]} "
                f"z {lower[2]}..{upper[2]}"
            ) from error
    for key, recipe in declaration.recipes.items():
        if len(key) > 40:
            raise ValueError("Recipe key too long")
        validate_recipe(recipe, declaration)
    allowed = {"address", "write"} if declaration.module == "storage" else {"load"}
    if declaration.module in ("arithmetic", "output"):
        allowed.add("add")
    for family, template in declaration.recipe_templates.items():
        if family not in allowed or len(template) > 128:
            raise ValueError("Invalid recipe template")
        if any(key.split(":")[0] == family for key in declaration.recipes):
            raise ValueError("Overlapping recipe and template")
        for operation in template:
            if isinstance(operation, TemplateLevel) and isinstance(operation.level, ParameterBit):
                level = operation.level
                if (
                    level.parameter not in PARAMETERS[family]
                    or level.bit >= PARAMETERS[family][level.parameter]
                ):
                    raise ValueError("Invalid parameter bit for recipe family")
        # Validate controls and operation bounds on a concrete expansion. Bit
        # selection can only change booleans, never the operations or targets.
        key = family + ":0" * len(PARAMETERS[family])
        validate_recipe(resolve_recipe(declaration, key), declaration)
    return declaration


def validate_recipe(recipe: object, declaration: ModuleDeclaration) -> int:
    """Validate finite control levels/waits without accepting executable content."""
    if type(recipe) is not list or len(recipe) > 128:
        raise ValueError("Recipe must contain at most 128 operations")
    controls = {c.id: c for c in declaration.controls}
    ticks = 0
    for op in recipe:
        if type(op) is not dict:
            raise ValueError("Invalid recipe operation")
        if set(op) == {"wait"}:
            if type(op["wait"]) is not int or not 1 <= op["wait"] <= 200:
                raise ValueError("Wait must be 1..200 server ticks")
            ticks += op["wait"]
        elif set(op) == {"control", "level"}:
            key = op["control"]
            if (
                type(key) is not str
                or key not in controls
                or controls[key].role not in ("programming", "test_input")
                or type(op["level"]) is not bool
            ):
                if type(key) is str and key not in controls:
                    raise ValueError(f"Recipe control {key} is not declared")
                if (
                    type(key) is str
                    and key in controls
                    and controls[key].role not in ("programming", "test_input")
                ):
                    raise ValueError(
                        f"Recipe control {key} requires test_input or programming role"
                    )
                raise ValueError("Recipe requires declared input/programming levels")
        else:
            raise ValueError("Invalid recipe operation")
    return ticks


# Only these state fields are known to vary without changing hardware configuration.
# Do not drop unknown fields, repeater delay, comparator mode, facing or wire shape.
TRANSIENT = {
    "minecraft:redstone_wire": {"power"},
    "minecraft:redstone_lamp": {"lit"},
    "minecraft:lever": {"powered"},
    "minecraft:stone_button": {"powered"},
    "minecraft:repeater": {"powered", "locked"},
    "minecraft:comparator": {"powered"},
    "minecraft:redstone_torch": {"lit"},
    "minecraft:redstone_wall_torch": {"lit"},
}


def structural_identity(state: dict[str, Any]) -> dict[str, Any]:
    """Identity of one observed cell; not a full-region hardware verification."""
    return {
        "position": list(state["position"]),
        "name": state["name"],
        "properties": {
            k: v
            for k, v in state["properties"].items()
            if k not in TRANSIENT.get(state["name"], set())
        },
    }


def inspect_module(actions: Actions, declaration: ModuleDeclaration) -> dict[str, Any]:
    # Revalidate even model instances: nested lists/dicts can have been mutated.
    declaration = validate_declaration(declaration.model_dump(), actions.contract)
    result: dict[str, Any] = {
        "module": declaration.module,
        "scope": "interface_readback_only",
        "valid": True,
        "behavioral_passed": False,
        "declaration": declaration.model_dump(mode="json"),
        "signals": {},
        "controls": {},
        "hardware": [],
        "failed_checks": [],
    }
    targets: list[tuple[str, list[int], str, str]] = [
        (f"{role}[{index}]", probe.position, probe.block, role)
        for role, probes in declaration.probes.items()
        for index, probe in enumerate(probes)
    ]
    targets.extend(
        (control.id, control.position, "minecraft:lever", "control")
        for control in declaration.controls
    )
    for target, position, block, role in targets:
        actual = actions.observe(position)
        properties = actual.get("properties")
        field = (
            "power"
            if block == "minecraft:redstone_wire"
            else ("lit" if block == "minecraft:redstone_lamp" else "powered")
        )
        signal = properties.get(field) if isinstance(properties, dict) else None
        valid_signal = (
            type(signal) is int and 0 <= signal <= 15 if field == "power" else type(signal) is bool
        )
        valid = actual.get("position") == position and actual.get("name") == block and valid_signal
        if not valid:
            result["valid"] = False
            result["failed_checks"].append(
                {
                    "target": target,
                    "expected": {"position": position, "block": block, "signal": field},
                    "actual": actual,
                    "reason": "Missing, mismatched or ambiguous world evidence",
                }
            )
        else:
            result["hardware"].append(structural_identity(actual))
        bit = bool(signal) if valid else None
        if role == "control":
            result["controls"][target] = bit
        else:
            result["signals"].setdefault(role, []).append(bit)
    return result


def run_module_inspection(path: Path, root: Path = RUN_DIRECTORY) -> TrialManifest:
    """Read-only dedicated-world evidence; never resets or builds hardware."""
    from noob_agent.redstone.contract import load_contract
    from noob_agent.redstone.rcon import RconClient
    from noob_agent.redstone.sidecar import Sidecar
    from noob_agent.redstone.trial import CONTRACT_PATH

    manifest = TrialManifest(root)
    manifest.data["kind"] = "module_interface_inspection"
    stage = "declaration"
    try:
        if path.stat().st_size > 32768:
            raise ValueError("Declaration too large")
        declaration = validate_declaration(
            json.loads(path.read_text()), load_contract(CONTRACT_PATH)
        )
        manifest.data["module_declaration"] = declaration.model_dump(mode="json")
        manifest.save()
        stage = "world_inspection"
        with RconClient.dedicated() as transport, Sidecar(manifest) as sidecar:
            actions = Actions(manifest, transport, sidecar)
            event = manifest.attempt("module_inspection", declaration.model_dump(mode="json"))
            result = inspect_module(actions, declaration)
            manifest.observed(event, result)
            manifest.data["checks"].append(result)
    except Exception as error:
        manifest.data["errors"].append({"stage": stage, "type": type(error).__name__})
    finally:
        manifest.finish_incomplete(
            [
                "Interface readback only; all four behavioral module graders remain pending",
                "No construction, timed controls, model success or milestone acceptance",
            ]
        )
    return manifest
