"""Fresh public planner context and strict bounded intentions, without a blueprint."""

from __future__ import annotations

import importlib
import json
import math
import re
from typing import Any, Literal

from pydantic import Field

from noob_agent.models.client import ModelRequest, WandbInferenceClient
from noob_agent.redstone.contract import FrozenModel, MachineContract, validate_build_action
from noob_agent.redstone.modules import ModuleDeclaration, validate_declaration
from noob_agent.redstone.provider_limits import (
    PLANNER_MAX_OUTPUT_TOKENS,
    PLANNER_TIMEOUT_SECONDS,
)
from noob_agent.settings import ModelSettings, WandbSettings

MAX_FEEDBACK_ENTRIES = 6


class OfferedAction(FrozenModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    criteria: str = Field(min_length=1, max_length=1000)
    action: Literal["place", "break", "interact", "observe"]
    position: list[int] = Field(min_length=3, max_length=3)
    block: str | None = None
    properties: dict[str, str | int | bool] | None = None


class Intention(FrozenModel):
    summary: str = Field(min_length=1, max_length=2000)
    actions: list[OfferedAction] = Field(max_length=32)
    module_inspection: ModuleDeclaration | None = None
    request_grading: bool = False
    max_actions: int = Field(default=256, ge=1, le=256)
    max_seconds: int = Field(default=60, ge=1, le=60)


def validate_intention(value: object, contract: MachineContract) -> Intention:
    intention = Intention.model_validate(value)
    if (
        not intention.actions
        and intention.module_inspection is None
        and not intention.request_grading
    ):
        raise ValueError("Intention requires actions, module inspection or grading request")
    if intention.module_inspection is not None:
        validate_declaration(intention.module_inspection.model_dump(), contract)
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

    def __init__(self, contract: MachineContract, *, require_module_grading: bool = False) -> None:
        self.contract = contract
        self.require_module_grading = require_module_grading
        self.grading_enabled = False
        self.history: list[dict[str, Any]] = []
        self.public = contract.model_dump(mode="json", exclude={"independent_final_checks"})
        self.public["module_grading_limits"] = {
            "aggregate_ticks": 96000,
            "aggregate_commands": 1000000,
            "scope": "all public module cases and repairs; action/wall limits still apply",
        }

    def feedback(self, actual: dict[str, Any]) -> None:
        self.history.append(json.loads(json.dumps(actual, allow_nan=False)))
        self.history = self.history[-MAX_FEEDBACK_ENTRIES:]

    def request(self, observation: dict[str, Any]) -> ModelRequest:
        schema = Intention.model_json_schema()
        if self.require_module_grading and not self.grading_enabled:
            # Construction stays compact until the model explicitly requests
            # grading. Do not compile the probe/recipe schema for build offers.
            schema["properties"].pop("module_inspection")
            schema["$defs"] = {"OfferedAction": schema["$defs"]["OfferedAction"]}
            schema["properties"]["actions"]["maxItems"] = 8
            schema["properties"]["summary"]["maxLength"] = 200
            schema["$defs"]["OfferedAction"]["properties"]["criteria"]["maxLength"] = 160
            return ModelRequest(
                system="Design within the frozen public requirements. Start with the "
                "register, then arithmetic, storage and output; use recent feedback "
                "to continue the current module. "
                "Offer at most eight supported construction actions and no "
                "module_inspection. Set request_grading false while building; "
                "when the module's controls and probes are built and ready for "
                "public checks, return request_grading true to request the full "
                "declaration schema on the next turn. Recent feedback reports cumulative "
                "stone and signal or control placements. The existing grass floor at y=63 can "
                "support blocks placed at y=64. Every early construction intention must "
                "include a lever, wire, torch, repeater, comparator or lamp; stone, "
                "glass and redstone blocks alone cannot pass a module. Place controls, "
                "wiring, memory or probes with only the supports "
                "they need. Use x=0..95, "
                "y=64..95, z=0..95; "
                "grass at y=63 is protected. Levers need lasting solid support on "
                "their attachment face. For a lever omit properties unless face is "
                "floor, wall or ceiling and facing is horizontal. Use exact "
                "permitted Minecraft block IDs "
                "from the requirements. Each action needs a quoted string id, "
                "criteria, action and position, plus block for placement. Return "
                "one compact JSON object with summary and actions; no raw commands. "
                "Omit max_actions and max_seconds to use safe defaults: placement "
                "uses at least five primitive actions including readbacks.",
                prompt=json.dumps(
                    {
                        "requirements": self.public,
                        "observations": observation,
                        "feedback": self.history,
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
        if self.history and "output token cap" in self.history[-1].get("intention_rejected", ""):
            schema["properties"]["actions"]["maxItems"] = 8
            schema["properties"]["summary"]["maxLength"] = 200
            schema["$defs"]["OfferedAction"]["properties"]["criteria"]["maxLength"] = 160
        return ModelRequest(
            system="Design within the frozen public requirements. Work on one public "
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
            "After a module passes, continue with the next module. Any later "
            "construction action invalidates earlier module passes. After the last "
            "build action, regrade all four modules with empty action lists so they "
            "pass on the same hardware. A failed public check needs a physical "
            "repair before regrading the same module; repeating the same grade "
            "on unchanged hardware cannot fix a missing lever, missing probe, or "
            "wrong reset signal. Return one JSON intention "
            "with summary and bounded actions, each with id, criteria, action, position, "
            "and optional block/properties. Jev selects a sequence from these offers, each "
            "at most once, observing after each, and may finish early. Specify max_actions "
            "(including internal validation/readbacks/settling) and max_seconds within "
            "the frozen intention limits; omit them to use defaults of 256 and 60. "
            "Each placement needs at least five primitive actions. Criteria must "
            "explain ordering and dependencies. "
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
            "Actions may be empty when requesting inspection or grading.",
            prompt=json.dumps(
                {
                    "requirements": self.public,
                    "observations": observation,
                    "feedback": self.history,
                },
                allow_nan=False,
            ),
            max_output_tokens=PLANNER_MAX_OUTPUT_TOKENS,
            thinking=False,
            response_schema=schema,
        )


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
