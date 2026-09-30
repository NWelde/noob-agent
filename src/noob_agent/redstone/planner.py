"""Fresh public planner context and strict bounded intentions, without a blueprint."""

from __future__ import annotations

import importlib
import json
import math
import re
from typing import Any, Literal

from pydantic import Field

from noob_agent.models.client import ModelRequest, WandbInferenceClient
from noob_agent.redstone.circuit_plan import CircuitPlan
from noob_agent.redstone.contract import FrozenModel, MachineContract, validate_build_action
from noob_agent.redstone.demo import LAMP_POSITION, LEVER_POSITION, WIRE_POSITION
from noob_agent.redstone.modules import MachineDeclaration, ModuleDeclaration, validate_declaration
from noob_agent.redstone.provider_limits import (
    PLANNER_MAX_OUTPUT_TOKENS,
    PLANNER_TIMEOUT_SECONDS,
)
from noob_agent.settings import ModelSettings, WandbSettings

MAX_FEEDBACK_ENTRIES = 6
MAX_CONSTRUCTION_INTENTIONS_BEFORE_GRADE = 12


def planner_validation_instruction(reason: str, finish_reason: str | None) -> str:
    if reason.startswith("Missing register recipe load:"):
        return (
            "Declare recipe_templates.load covering load:0 through load:15. "
            "Use one test_input or programming control for each data bit in the "
            "recipe template, with parameter=value, zero-based bit indexes. "
            "Those controls must name the actual built data levers. Do not include "
            "reset or STEP in the load recipe. Return the complete register "
            "declaration; no world actions were dispatched."
        )
    if (
        reason.startswith("Recipe control ")
        or reason == "Recipe requires declared input/programming levels"
    ):
        return (
            "Every recipe control must appear in module_inspection.controls with role "
            "test_input or programming and the distinct position of an actual built lever. "
            "Reset and STEP cannot be used in recipes. Do not invent a lever that is not built. "
            "Concrete recipe levels must be JSON booleans. "
            "If input hardware is missing, return construction actions to build it first. "
            "Otherwise correct the control declaration and return the complete intention. "
            "No actions were dispatched."
        )
    if finish_reason == "length":
        return (
            "Return a complete compact JSON intention with at most 8 actions, "
            "one short summary, brief criteria, compact recipe_templates, "
            "and no explanatory prose. No actions were dispatched."
        )
    if reason == "Unexpected block state":
        return (
            "Remove `block` and `properties` from every `break`, `interact`, and "
            "`observe` action. Only a `place` action may include block state. "
            "No actions were dispatched."
        )
    if reason == "Block is not permitted":
        return (
            "Use the exact namespaced block ID from the frozen permitted-block list, "
            "including the `minecraft:` prefix; for redstone wire use "
            "`minecraft:redstone_wire`. No actions were dispatched."
        )
    if reason == "Exactly one reset and STEP are required":
        return (
            "In module_inspection.controls, include exactly one control with role `reset` "
            "and exactly one with role `step`. Give them distinct IDs and positions. "
            "No actions were dispatched."
        )
    if reason.startswith("Aliased "):
        match = re.fullmatch(r"Aliased (.+) and (.+) at x (-?\d+) y (-?\d+) z (-?\d+)", reason)
        if match is not None:
            first, second, x, y, z = match.groups()
            return (
                f"`{first}` and `{second}` both use [{x}, {y}, {z}]. Keep both "
                "declarations and move one to a distinct in-bounds position; do not "
                "remove a required control. Keep exactly one control with role `reset` "
                "and exactly one with role `step`, plus all required programming "
                "controls. No actions were dispatched."
            )
        return (
            "Keep every declared probe and control, and give each a distinct "
            "three-integer position. Move one declaration instead of deleting a "
            "required control. Keep exactly one `reset` and one `step` role, plus "
            "all required programming controls. No actions were dispatched."
        )
    return "Return a corrected complete intention. No actions were dispatched."


class OfferedAction(FrozenModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    criteria: str = Field(min_length=1, max_length=1000)
    action: Literal["place", "break", "interact", "observe"]
    position: list[int] = Field(min_length=3, max_length=3)
    block: str | None = None
    properties: dict[str, str | int | bool] | None = None
    depends_on: list[str] = Field(default_factory=list, max_length=32)


class Intention(FrozenModel):
    summary: str = Field(min_length=1, max_length=2000)
    actions: list[OfferedAction] = Field(max_length=32)
    module_inspection: ModuleDeclaration | None = None
    machine_inspection: MachineDeclaration | None = None
    circuit_plan: CircuitPlan | None = None
    request_grading: bool = False
    register_bit_check: int | None = Field(default=None, ge=0, le=3, strict=True)
    max_actions: int = Field(default=256, ge=1, le=256)
    max_seconds: int = Field(default=60, ge=1, le=60)


def validate_intention(value: object, contract: MachineContract) -> Intention:
    intention = Intention.model_validate(value)
    if (
        not intention.actions
        and intention.module_inspection is None
        and intention.machine_inspection is None
        and not intention.request_grading
        and intention.register_bit_check is None
    ):
        raise ValueError("Intention requires actions, module inspection or grading request")
    if intention.module_inspection is not None:
        validate_declaration(intention.module_inspection.model_dump(), contract)
    if intention.machine_inspection is not None:
        validate_declaration(intention.machine_inspection.model_dump(), contract)
        if intention.actions or intention.module_inspection or intention.request_grading:
            raise ValueError("Full machine grading requires only its interface and no actions")
    if intention.register_bit_check is not None and (
        intention.actions
        or intention.request_grading
        or intention.module_inspection is None
        or intention.module_inspection.module != "register"
    ):
        raise ValueError(
            "Register bit diagnostics require a register declaration "
            "and no actions or grading request"
        )
    if len(intention.actions) > contract.budgets.intention_actions:
        raise ValueError("Intention action limit exceeded")
    if (
        intention.max_actions > contract.budgets.intention_actions
        or intention.max_seconds > contract.budgets.intention_seconds
    ):
        raise ValueError("Intention limits exceed frozen budgets")
    ids = [action.id for action in intention.actions]
    if "__finish__" in ids:
        raise ValueError("Reserved termination ID")
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate action IDs")
    for action in intention.actions:
        if len(action.depends_on) != len(set(action.depends_on)):
            raise ValueError(f"Duplicate dependencies for action {action.id}")
        if action.id in action.depends_on or any(dep not in ids for dep in action.depends_on):
            invalid = next(dep for dep in action.depends_on if dep == action.id or dep not in ids)
            raise ValueError(
                f"Invalid dependencies for action {action.id[:24]}: {invalid[:24]}. "
                "Use other action IDs in this same response only; omit references to prior batches."
            )
    for action in intention.actions:
        try:
            validate_build_action(
                contract,
                "break" if action.action == "observe" else action.action,
                (action.position[0], action.position[1], action.position[2]),
                action.block,
            )
        except ValueError as error:
            if str(error) != "Target outside inclusive build bounds":
                raise
            x, y, z = action.position
            lower, upper = contract.build_min, contract.build_max
            raise ValueError(
                f"Action {action.id} at x {x} y {y} z {z} outside build bounds "
                f"x {lower[0]}..{upper[0]} y {lower[1]}..{upper[1]} "
                f"z {lower[2]}..{upper[2]}"
            ) from error
        if action.action != "place" and (action.block is not None or action.properties is not None):
            raise ValueError("Unexpected block state")
        if action.properties is not None and any(
            not re.fullmatch("[a-z_]+", key) or not re.fullmatch("[a-z0-9_]+", str(item).lower())
            for key, item in action.properties.items()
        ):
            raise ValueError("Invalid property syntax")
    return intention


class PlannerContext:
    """Create a new instance per trial; only actual public feedback is retained.

    No persistence or budgeting authority here: the trial orchestrator must journal
    and charge before dispatch. The independent final check list is never prompted.
    """

    def __init__(
        self,
        contract: MachineContract,
        *,
        require_module_grading: bool = False,
        task: Literal["computer", "lamp_repair"] = "computer",
        history: list[dict[str, Any]] | None = None,
        circuit_plan: dict[str, Any] | CircuitPlan | None = None,
        latest_verified_layout: list[dict[str, Any]] | None = None,
        register_workshop: bool = False,
    ) -> None:
        self.contract = contract
        self.require_module_grading = require_module_grading
        self.task = task
        self.register_workshop = register_workshop
        self.grading_enabled = False
        self.machine_phase = False
        self.force_grading = False
        self.history: list[dict[str, Any]] = (
            json.loads(json.dumps(history[-MAX_FEEDBACK_ENTRIES:], allow_nan=False))
            if history is not None
            else []
        )
        self.circuit_plan = (
            CircuitPlan.model_validate(circuit_plan) if circuit_plan is not None else None
        )
        self.latest_verified_layout = json.loads(
            json.dumps(latest_verified_layout or [], allow_nan=False)
        )
        if not self.latest_verified_layout:
            for entry in reversed(self.history):
                layout = _find_verified_layout(entry)
                if layout is not None:
                    self.latest_verified_layout = layout
                    break
        self.public = contract.model_dump(mode="json", exclude={"independent_final_checks"})
        self.public["module_grading_limits"] = {
            "aggregate_ticks": 96000,
            "aggregate_commands": 1000000,
            "scope": "all public module cases and repairs; action/wall limits still apply",
        }

    def feedback(self, actual: dict[str, Any]) -> None:
        copied = json.loads(json.dumps(actual, allow_nan=False))
        layout = _find_verified_layout(copied)
        if layout is not None:
            self.latest_verified_layout = layout
        self.history.append(copied)
        self.history = self.history[-MAX_FEEDBACK_ENTRIES:]

    def update_circuit_plan(self, plan: dict[str, Any] | CircuitPlan | None) -> None:
        """Save a planner-authored design note independently of trimmed feedback."""
        self.circuit_plan = CircuitPlan.model_validate(plan) if plan is not None else None

    def saved_state(self) -> dict[str, Any]:
        """Return compact planner state for the trial continuation checkpoint."""
        return {
            "history": self.history,
            "circuit_plan": (
                self.circuit_plan.model_dump(mode="json") if self.circuit_plan is not None else None
            ),
            "latest_verified_layout": self.latest_verified_layout,
            "machine_phase": self.machine_phase,
        }

    def request(self, observation: dict[str, Any]) -> ModelRequest:
        schema = Intention.model_json_schema()
        if not self.register_workshop:
            schema["properties"].pop("register_bit_check", None)
        found_layout = _find_verified_layout(observation)
        if found_layout is not None:
            self.latest_verified_layout = found_layout
        compact_observation = _without_layout(observation)
        plan = self.circuit_plan.model_dump(mode="json") if self.circuit_plan else None
        feedback = _compact_feedback(self.history)
        payload_feedback = feedback
        if self.machine_phase:
            schema["properties"].pop("module_inspection")
            schema["properties"].pop("register_bit_check", None)
            schema["properties"]["actions"]["maxItems"] = 8
            return ModelRequest(
                system=(
                    "Integrate the independently checked modules into the frozen "
                    "stored-program machine. Use bounded single-block construction offers "
                    "with prior verified supports and same-response dependency IDs. "
                    "The physical instruction memory must drive LOAD/ADD/OUT/HALT through "
                    "the PC and decoder; only STEP and RESET may drive execution externally. "
                    "When ready, submit machine_inspection with no actions. Declare A4, "
                    "O4 lamps, PC3, halted1, strobe1, words48 probes, one RESET and STEP, "
                    "and 48 programming levers. word_controls is eight rows of six "
                    "control IDs, address0..7 and MSB first. No test_input controls, "
                    "external execution recipes, commands or computed outputs. "
                    "The independent normal-speed grader will program real levers and "
                    "compare actual states; design notes cannot prove success."
                ),
                prompt=json.dumps(
                    dict(
                        requirements=self.public,
                        observations=compact_observation,
                        feedback=feedback,
                        circuit_plan=plan,
                        latest_verified_layout=_compact_layout(self.latest_verified_layout),
                    ),
                    allow_nan=False,
                ),
                max_output_tokens=PLANNER_MAX_OUTPUT_TOKENS,
                thinking=True,
                response_schema=schema,
            )
        schema["properties"].pop("machine_inspection")
        schema["$defs"].pop("MachineDeclaration", None)
        if self.task == "lamp_repair":
            schema["properties"].pop("module_inspection")
            _prune_compact_defs(schema)
            schema["properties"]["actions"]["maxItems"] = 1
            schema["properties"]["summary"]["maxLength"] = 200
            schema["$defs"]["OfferedAction"]["properties"]["criteria"]["maxLength"] = 160
            return ModelRequest(
                system=(
                    "Repair the seeded, deliberately incomplete lever-to-lamp circuit. "
                    "The harness placed a lever and lamp; you must place the missing "
                    "connection and verify the result by operating the lever yourself. "
                    "Use only bounded actions, inspect actual observations, and work "
                    "through off, on, then off again. Offer exactly one action per "
                    "intention so the independent checker can observe each state. "
                    "The lever is at x=48 y=64 z=95, the missing wire position is "
                    "x=49 y=64 z=95, and the lamp is at x=50 y=64 z=95. Never replace "
                    "the lever or lamp. Use the exact ID minecraft:redstone_wire. "
                    "Only place that wire at the missing wire "
                    "position, interact with the lever, or observe these three cells. "
                    "Only place actions may specify block or properties. Return one "
                    "compact JSON intention with a short summary and exactly one "
                    "action. Do not claim success; the independent checker reads "
                    "Minecraft block states after each intention."
                ),
                prompt=json.dumps(
                    {
                        "task": "Repair and verify the seeded lamp circuit.",
                        "fixture": {
                            "harness_created": True,
                            "agent_created": False,
                            "lever": LEVER_POSITION,
                            "open_connection": WIRE_POSITION,
                            "lamp": LAMP_POSITION,
                        },
                        "observation": compact_observation,
                        "feedback": payload_feedback,
                        "circuit_plan": plan,
                        "latest_verified_layout": _compact_layout(self.latest_verified_layout)
                        if self.latest_verified_layout
                        else None,
                    },
                    allow_nan=False,
                ),
                max_output_tokens=PLANNER_MAX_OUTPUT_TOKENS,
                thinking=False,
                response_schema=schema,
            )
        if self.require_module_grading and not (self.grading_enabled or self.force_grading):
            # Construction stays compact until the model explicitly requests
            # grading. Do not compile the probe/recipe schema for build offers.
            schema["properties"].pop("module_inspection")
            _prune_compact_defs(schema)
            schema["properties"]["actions"]["maxItems"] = 8
            schema["properties"]["summary"]["maxLength"] = 200
            schema["$defs"]["OfferedAction"]["properties"]["criteria"]["maxLength"] = 160
            return ModelRequest(
                system=(
                    "Design within the frozen public requirements. Start with the "
                    "register, then arithmetic, storage and output; use recent feedback "
                    "to continue the current module. "
                    "Offer at most eight supported construction actions and no "
                    "module_inspection. Set request_grading false while building; "
                    "when the module's controls and probes are built and ready for "
                    "public checks, return request_grading true to request the full "
                    "declaration schema on the next turn. Recent feedback reports cumulative "
                    "stone and signal or control placements. The existing grass floor at y=63 can "
                    "support blocks placed at y=64. Before any functional block exists, include "
                    "a lever, wire, torch, repeater, comparator or lamp. Later support-only "
                    "batches are allowed. Supports must be built in a previous batch before "
                    "offering wire or attached components; Jev can choose offers in any order. "
                    "Stone, "
                    "glass and redstone blocks alone cannot pass a module. Place controls, "
                    "wiring, memory or probes with only the supports "
                    "they need. Use x=0..95, "
                    "y=64..95, z=0..95; "
                    "grass at y=63 is protected. Levers need lasting solid support on "
                    "their attachment face. For a lever omit properties unless face is "
                    "floor, wall or ceiling and facing is horizontal. Use exact "
                    "permitted Minecraft block IDs "
                    "from the requirements. Each action needs a quoted string id, "
                    "criteria, action and position. Optional depends_on lists other action IDs "
                    "in this same response only. Never reference prior-batch actions or "
                    "circuit_plan "
                    "input IDs; already built hardware needs no depends_on entry. Select only "
                    "dependency-ready actions. Optionally "
                    "return circuit_plan as your own durable design notes, never as evidence. "
                    "Only place may include block or "
                    "properties; omit both for break, interact and observe. For boolean "
                    "block properties such as powered, use JSON true/false, not quoted "
                    "strings. Omit properties unless needed. Return "
                    "one compact JSON object with summary and actions; no raw commands. "
                    "Omit max_actions and max_seconds to use safe defaults: placement "
                    "uses at least five primitive actions including readbacks."
                    + (
                        " Register workshop pad: stone at x=40..48, y=64, z=40..48; "
                        "glass marker at x=40,y=65,z=40. Build above this pad yourself; it "
                        "contains no circuit or preplaced controls."
                        if self.register_workshop
                        else ""
                    )
                ),
                prompt=json.dumps(
                    {
                        "requirements": self.public,
                        "observations": compact_observation,
                        "feedback": payload_feedback,
                        "circuit_plan": plan,
                        "latest_verified_layout": _compact_layout(self.latest_verified_layout)
                        if self.latest_verified_layout
                        else None,
                    },
                    allow_nan=False,
                ),
                max_output_tokens=PLANNER_MAX_OUTPUT_TOKENS,
                thinking=False,
                response_schema=schema,
            )
        if self.require_module_grading:
            schema["properties"]["actions"]["maxItems"] = 8
            schema["properties"]["summary"]["maxLength"] = 200
            schema["$defs"]["OfferedAction"]["properties"]["criteria"]["maxLength"] = 160
        if self.force_grading:
            schema["properties"]["actions"]["minItems"] = 0
            schema["properties"]["actions"]["maxItems"] = 0
            schema["properties"]["module_inspection"] = {"$ref": "#/$defs/ModuleDeclaration"}
            if "module_inspection" not in schema["required"]:
                schema["required"].append("module_inspection")
            schema["properties"]["request_grading"] = {
                "type": "boolean",
                "enum": [False],
            }
            if "request_grading" not in schema["required"]:
                schema["required"].append("request_grading")
        if (
            not self.force_grading
            and self.history
            and "output token cap" in self.history[-1].get("intention_rejected", "")
        ):
            schema["properties"]["actions"]["maxItems"] = 8
            schema["properties"]["summary"]["maxLength"] = 200
            schema["$defs"]["OfferedAction"]["properties"]["criteria"]["maxLength"] = 160
        system = (
            "Design within the frozen public requirements. Work on one public "
            "module at a time in this order: register, arithmetic, storage, output. "
            "Build targets, including observations, must use x=0..95, y=64..95, "
            "z=0..95. The grass at y=63 is protected ground, and the player's "
            "starting z=98 is outside the build prism. Place controls on or above "
            "y=64 and inspect only in-bounds targets. A lever needs lasting solid "
            "support on its attachment face; do not attach a wall lever to another "
            "lever or a block you will replace. "
            "Declare and build only the current module; do not design all four modules "
            "or the full machine in one intention. Offer at most 8 actions per "
            "intention to keep each Jev request small. During construction, omit "
            "module_inspection and offer build actions. When ready to grade, supply "
            "module_inspection with probes, controls, and complete behavioral recipes "
            "or recipe_templates; grading intentions may have no build actions. "
            "Register needs "
            "recipe_templates.load for all 16 values. Arithmetic and output need "
            "recipe_templates.load and add plus recipes.out. Storage needs "
            "recipe_templates.address and write. A declaration "
            "with probes and controls alone is inspection only and is invalid. "
            "The latest construction_progress.verified_layout is the authoritative "
            "readback map: only declare probes and controls on cells whose listed "
            "block matches the expected interface. A missing cell is air. Redstone wire "
            "must have an allowed full-top support block directly below; offer and verify "
            "that support before offering the wire. Preserve existing controls when "
            "adding wires; do not overwrite a lever's cell. "
            "After a module passes, continue with the next module. Any later "
            "construction action invalidates earlier module passes. After the last "
            "build action, regrade all four modules with empty action lists so they "
            "pass on the same hardware. A failed public check needs a physical "
            "repair before regrading the same module; repeating the same grade "
            "on unchanged hardware cannot fix a missing lever, missing probe, or "
            "wrong reset signal. Return one JSON intention "
            "with summary and bounded actions, each with id, criteria, action, position, "
            "and optional block/properties and depends_on. Jev may select an action only "
            "after every ID in its depends_on list has succeeded; otherwise that offer is "
            "not ready. Each offer may be selected at most once, with readback after each, "
            "and Jev may finish early. Optionally include circuit_plan as your own durable "
            "design notes: input/control positions, storage nodes, intended signal paths, "
            "expected polarity, and unfinished connections. This plan is design intent only; "
            "world readbacks are the sole evidence of built hardware and the independent "
            "grader alone establishes behavior. Specify max_actions "
            "(including internal validation/readbacks/settling) and max_seconds within "
            "the frozen intention limits; omit them to use defaults of 256 and 60. "
            "Each placement needs at least five primitive actions. Criteria must "
            "explain ordering and dependencies. Only place actions may include block "
            "or properties; omit both for break, interact and observe. Boolean block "
            "properties must be JSON booleans, not quoted strings. "
            "Use actual feedback to revise failed work. No raw commands. "
            "Every place action's block must be exactly one permitted ID: "
            "minecraft:stone, minecraft:glass, minecraft:redstone_wire, "
            "minecraft:redstone_torch, minecraft:redstone_wall_torch, "
            "minecraft:repeater, minecraft:comparator, minecraft:lever, "
            "minecraft:stone_button, minecraft:redstone_lamp, "
            "minecraft:redstone_block, minecraft:oak_sign, or "
            "minecraft:oak_wall_sign. Do not add punctuation or other text to IDs. "
            "Optionally supply module_inspection with the current module, only its "
            "required probes and controls. Do not add PC/halted or other probe arrays "
            "unless required by that module. "
            "Probe arrays are MSB first: register a[4]; arithmetic a[4], o[4]; "
            "storage words[48] (addresses 0..7, bits 5..0), readout[6], address[3]; "
            "output o[4], strobe[1]. O probes must be lamps; others wire or lamps. "
            "Declare exactly one reset and step lever, plus programming/test_input levers "
            "as needed (storage requires programming). All positions must be distinct. "
            "Inspection only reads interfaces; it never establishes behavioral success "
            "or operates controls. To request public behavioral grading include recipes "
            "and/or compact recipe_templates. Templates map load/add/address/write to "
            "at most 128 operations with control and level or wait. A level is a boolean "
            "or {parameter: value|address|word, bit: integer, invert: optional boolean}. "
            "Bits are zero-based from the least significant bit: load/add use value bits "
            "0..3; address uses address bits 0..2; write uses address bits 0..2 and word "
            "bits 0..5. Bit selection only sets declared input/programming controls. "
            "Choose your own control IDs and encoding; templates supply no wiring. "
            "Always use compact recipe_templates for load/add/address/write value "
            "families; do not enumerate those recipes explicitly. Templates cover all "
            "values in that family; do not also supply explicit "
            "recipes for the same family. Supply out as an explicit recipe. "
            "Explicit recipes are "
            "a mapping of value keys to at most 128 operations, each exactly "
            "{control: declared input/programming ID, level: boolean} or {wait: 1..200}. "
            "No reset or STEP in recipes. Register requires load:0 through load:15; "
            "arithmetic/output also add:0 through add:15 and out. These prepare an "
            "instruction for one grader-controlled STEP. Storage requires address:0..7 "
            "and write:address:word for two word arrays [0,63,21,42,15,48,32,16] and "
            "[63,0,42,21,48,15,31,47]; writes run while reset is held. "
            "All controls and waits are declared, no layouts are supplied. "
            "Actions may be empty when requesting inspection or grading."
        )
        if self.register_workshop:
            system += (
                " This is the optional register_workshop profile. Its stone pad is at "
                "x=40..48, y=64, z=40..48, with a glass marker at x=40,y=65,z=40. "
                "The pad has no controls, wiring, probes, circuit or blueprint. You must "
                "design and build the register yourself. "
                "After a single bit is ready, you may return register_bit_check as an "
                "integer 0..3 with no construction actions or module_inspection. This "
                "runs a diagnostic of load zero, load one, hold while input changes, "
                "and reset for that bit. Its result is diagnostic evidence only; it "
                "never passes the four-bit register checkpoint. Continue building and "
                "request the unchanged all-16 register behavior suite when ready."
            )
        if self.force_grading:
            system = (
                "A construction limit was reached. Stop building now. First return "
                "exactly one JSON object with an empty actions array, "
                "request_grading false, and one complete module_inspection. Do not "
                "include any build, break, interact, or observe action. The schema "
                "forbids actions and requires module_inspection. Then follow these "
                "declaration requirements: " + system + " Return a "
                "complete behavioral module_inspection with empty actions. Include all "
                "required probes, controls, and recipes or recipe_templates so the "
                "independent public grader can test the current hardware."
            )
        return ModelRequest(
            system=system,
            prompt=json.dumps(
                {
                    "requirements": self.public,
                    "observations": compact_observation,
                    "feedback": payload_feedback,
                    "circuit_plan": plan,
                    "latest_verified_layout": _compact_layout(self.latest_verified_layout)
                    if self.latest_verified_layout
                    else None,
                },
                allow_nan=False,
            ),
            max_output_tokens=PLANNER_MAX_OUTPUT_TOKENS,
            thinking=False,
            response_schema=schema,
        )


def _find_verified_layout(value: Any) -> list[dict[str, Any]] | None:
    if isinstance(value, dict):
        candidate = value.get("verified_layout")
        if isinstance(candidate, list):
            copied: list[dict[str, Any]] = json.loads(json.dumps(candidate, allow_nan=False))
            return copied
        for item in value.values():
            found = _find_verified_layout(item)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_verified_layout(item)
            if found is not None:
                return found
    return None


def _without_layout(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_layout(item)
            for key, item in value.items()
            if key not in {"verified_layout", "latest_verified_layout"}
        }
    if isinstance(value, list):
        return [_without_layout(item) for item in value]
    return value


def _compact_feedback(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep outcomes and checks; replace old full layout snapshots with deltas."""
    previous: set[str] | None = None
    compact: list[dict[str, Any]] = []
    for entry in history:
        item = _without_layout(entry)
        layout = _find_verified_layout(entry)
        if layout is not None:
            current = set(_compact_layout(layout)["cells"])
            if previous is not None:
                added, removed = sorted(current - previous), sorted(previous - current)
                if added or removed:
                    item["observed_layout_delta"] = {"added": added, "removed": removed}
            previous = current
        compact.append(item)
    return compact


def _compact_layout(layout: list[dict[str, Any]]) -> dict[str, Any]:
    """Render actual readbacks as a small coordinate index and layer map."""
    cells: list[tuple[int, int, int, str]] = []
    for cell in layout:
        pos = cell.get("position")
        if isinstance(pos, list) and len(pos) == 3:
            x, y, z = pos
            name = str(cell.get("name", "unknown"))
            cells.append((int(x), int(y), int(z), name.removeprefix("minecraft:")))
    layers: dict[str, list[str]] = {}
    vertical: dict[str, list[str]] = {}
    for y in sorted({cell[1] for cell in cells}):
        layers[str(y)] = [f"{x},{z}:{name}" for x, cy, z, name in cells if cy == y]
    for x in sorted({cell[0] for cell in cells}):
        vertical[str(x)] = [f"{y},{z}:{name}" for cx, y, z, name in cells if cx == x]
    return {
        "source": "verified world readbacks; design intent is not evidence",
        "cells": [f"{x},{y},{z}:{name}" for x, y, z, name in sorted(cells)],
        "properties_by_cell": {
            ",".join(map(str, cell["position"])): cell.get("properties", {})
            for cell in layout
            if cell.get("properties")
        },
        "top_down_by_y": layers,
        "vertical_slices_by_x": vertical,
    }


def _prune_compact_defs(schema: dict[str, Any]) -> None:
    keep = {
        "OfferedAction",
        "CircuitPlan",
        "PlannedInput",
        "PlannedStorageNode",
        "PlannedSignalPath",
        "PlannedConnection",
    }
    schema["$defs"] = {key: value for key, value in schema["$defs"].items() if key in keep}


class TrialWandbClient(WandbInferenceClient):
    """Existing ModelClient completion behavior with per-attempt SDK limits.

    Explicit product ModelSettings/WandbSettings are independent of development
    Astra. Callers must also bound the entire await by remaining trial time and
    charge each attempted complete() before calling it. No automatic SDK retries.
    """

    def __init__(
        self,
        model: ModelSettings,
        wandb: WandbSettings,
        *,
        timeout: float = PLANNER_TIMEOUT_SECONDS,
    ) -> None:
        if not math.isfinite(timeout) or not 0 < timeout <= PLANNER_TIMEOUT_SECONDS:
            raise ValueError("Invalid planner request timeout")
        super().__init__(model, wandb)
        self.timeout = timeout

    def _client(self) -> Any:
        openai = importlib.import_module("openai")
        headers = (
            {"OpenAI-Project": self._model.inference_project}
            if self._model.inference_project
            else None
        )
        return openai.AsyncOpenAI(
            base_url=self._model.inference_base_url,
            api_key=self._wandb.api_key,
            default_headers=headers,
            max_retries=0,
            timeout=self.timeout,
        )
