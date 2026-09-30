"""Single-trial planner/Jev execution; checkpoint success is never machine success."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from collections.abc import Callable
from typing import Any, Literal, Protocol

from pydantic import ValidationError

from noob_agent.models.client import ModelClient
from noob_agent.redstone.actions import (
    ActionAdmissionDenied,
    ActionEstimate,
    ActionLimit,
    Actions,
    EffectMismatch,
    InvalidBlockState,
)
from noob_agent.redstone.behavior import grade_module, required_recipes
from noob_agent.redstone.demo import LAMP_POSITION, LEVER_POSITION, WIRE_POSITION
from noob_agent.redstone.execution import ConstructionExecution
from noob_agent.redstone.jev import JevError, action_request, selected_action
from noob_agent.redstone.layout import VerifiedLayout, validate_declaration_layout
from noob_agent.redstone.modules import inspect_module, resolve_recipe
from noob_agent.redstone.planner import (
    MAX_CONSTRUCTION_INTENTIONS_BEFORE_GRADE,
    PlannerContext,
    planner_validation_instruction,
    validate_intention,
)
from noob_agent.redstone.provider_limits import (
    JEV_HTTP_503_RETRIES_PER_SELECTION,
    PLANNER_TIMEOUT_SECONDS,
)
from noob_agent.redstone.repair_guard import RepairGuard
from noob_agent.redstone.trial import CONTRACT_PATH, TrialManifest


class Evaluator(Protocol):
    def evaluate(self, request: dict[str, Any], *, timeout: float) -> dict[str, Any]: ...


class LoopLimit(RuntimeError):
    pass


class PlannerValidationExhausted(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__("Repeated planner validation failure")
        self.reason = reason


PUBLIC_MODULES = frozenset({"register", "arithmetic", "storage", "output"})
FUNCTIONAL_BLOCKS = frozenset(
    {
        "minecraft:redstone_wire",
        "minecraft:redstone_torch",
        "minecraft:redstone_wall_torch",
        "minecraft:repeater",
        "minecraft:comparator",
        "minecraft:lever",
        "minecraft:stone_button",
        "minecraft:redstone_lamp",
    }
)


def _validation_field_reason(error: ValidationError) -> str:
    """Return a bounded schema path and code, never untrusted input or error prose."""
    first = error.errors(include_input=False, include_url=False)[0]
    parts = first.get("loc", ())
    safe_parts = []
    for part in parts:
        if type(part) is int and 0 <= part <= 1000:
            safe_parts.append(str(part))
        elif (
            type(part) is str
            and 1 <= len(part) <= 40
            and part.isascii()
            and all(char.isalnum() or char in "_-:" for char in part)
        ):
            safe_parts.append(part)
        else:
            return "Intention failed strict validation."
    code = first.get("type")
    if (
        not safe_parts
        or type(code) is not str
        or not code.isascii()
        or not all(char.islower() or char == "_" for char in code)
    ):
        return "Intention failed strict validation."
    reason = f"Field {'.'.join(safe_parts)} failed {code}."
    return reason if len(reason) <= 180 else "Intention failed strict validation."


class TrialLoop:
    """Own a fresh context, persist charges before calls, never retry uncertainty.

    The trusted public checker must use the supplied Actions for observations.
    Returning complete stops at a public checkpoint, without setting a final grade.
    A selected offer is consumed once; Jev may select further offers or finish.
    All internal validation/settling/readback charges count toward intention caps.
    """

    def __init__(
        self,
        manifest: TrialManifest,
        actions: Actions,
        planner: ModelClient,
        jev: Evaluator,
        *,
        check: Callable[[Actions], dict[str, Any]] | None = None,
        require_module_grading: bool = False,
        full_machine_verification: bool = False,
        strict_module_gate: bool = False,
        batch_construction: bool = False,
        stop_after_module: Literal["register"] | None = None,
        register_workshop: bool = False,
        task: Literal["computer", "lamp_repair"] = "computer",
        resume_state: dict[str, Any] | None = None,
        jev_min_interval_seconds: float = 0,
        announce: Callable[[str, str | None], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ("loop" in manifest.data and resume_state is None) or actions.stopped:
            raise ValueError("Loop requires a fresh session or a saved continuation")
        self.manifest, self.actions, self.planner, self.jev = manifest, actions, planner, jev
        self.contract = actions.contract
        self.strict_module_gate = strict_module_gate
        self.batch_construction = batch_construction
        if task == "lamp_repair" and require_module_grading:
            raise ValueError("Lamp repair demo does not use computer module grading")
        self.context = PlannerContext(
            self.contract,
            require_module_grading=require_module_grading,
            task=task,
            register_workshop=register_workshop,
            history=None if resume_state is None else resume_state.get("history", []),
            circuit_plan=None if resume_state is None else resume_state.get("circuit_plan"),
            latest_verified_layout=(
                None if resume_state is None else resume_state.get("latest_verified_layout")
            ),
        )
        if resume_state is not None:
            self.context.grading_enabled = resume_state.get("grading_enabled") is True
            # Old sessions may have been stopped by the obsolete three-turn gate.
            self.context.force_grading = False
        self.clock, self.started = clock, clock()
        self.deadline = self.started + self.contract.budgets.wall_seconds
        self.intention_deadline: float | None = None
        self.intention_start = 0
        self.intention_max = self.contract.budgets.intention_actions
        self.checker = check
        if (
            resume_state is not None
            and self.checker is not None
            and hasattr(self.checker, "load_state")
        ):
            self.checker.load_state(resume_state.get("checker", {}))
        self.require_module_grading = require_module_grading
        self.full_machine_verification = full_machine_verification
        if (resume_state or {}).get("machine_phase") is True:
            if not full_machine_verification:
                raise ValueError("Machine-phase continuation requires full verification")
            self.require_module_grading = False
            self.context.require_module_grading = False
            self.context.machine_phase = True
        if stop_after_module is not None and not require_module_grading:
            raise ValueError("Module checkpoint requires behavioral module grading")
        self.stop_after_module = stop_after_module
        self.register_workshop = register_workshop
        self.task = task
        self.announce = announce
        if not math.isfinite(jev_min_interval_seconds) or not 0 <= jev_min_interval_seconds <= 30:
            raise ValueError("Invalid Jev call interval")
        self.jev_min_interval_seconds = jev_min_interval_seconds
        self.last_jev_started: float | None = None
        self.used = {"planner_calls": 0, "jev_calls": 0, "repair_rounds": 0}
        self.stone_placements = int((resume_state or {}).get("stone_placements", 0))
        self.signal_control_placements = int(
            (resume_state or {}).get("signal_control_placements", 0)
        )
        self.construction_intentions_since_grade = int(
            (resume_state or {}).get("construction_intentions_since_grade", 0)
        )
        self.layout = VerifiedLayout()
        self.repair_guard = RepairGuard.from_state((resume_state or {}).get("repair_guard", {}))
        self.repair_cell_states: dict[str, Any] = dict(
            (resume_state or {}).get("repair_cell_states", {})
        )
        self.failed_module_checks: dict[str, dict[str, Any]] = {}
        restored_failures = (resume_state or {}).get("failed_module_checks", {})
        if isinstance(restored_failures, dict):
            self.failed_module_checks = {
                module: evidence
                for module, evidence in restored_failures.items()
                if isinstance(module, str)
                and isinstance(evidence, dict)
                and isinstance(evidence.get("failed_checks"), list)
            }
        self.placed_cells: dict[tuple[int, ...], tuple[str, tuple[tuple[str, Any], ...]]] = {}
        for item in (resume_state or {}).get("placed_cells", []):
            position = tuple(item["position"])
            observed = next(
                (
                    cell
                    for cell in (resume_state or {}).get("world_states", [])
                    if tuple(cell.get("position", ())) == position
                ),
                None,
            )
            # Keep only cells still present in the world. Unsupported wire and
            # other changed blocks must be rebuilt by the planner after resume.
            if observed is None or observed.get("name") != item["block"]:
                continue
            actual_properties = observed.get("properties", {})
            if all(
                actual_properties.get(key) == value
                for key, value in item.get("properties", {}).items()
            ):
                self.placed_cells[position] = (
                    item["block"],
                    tuple(sorted(item.get("properties", {}).items())),
                )
                self.layout.reconcile(
                    position,
                    {
                        "name": item["block"],
                        "properties": actual_properties,
                    },
                )
        self.running = False
        self.manifest.data.update(
            contract={
                "version": self.contract.version,
                "path": str(CONTRACT_PATH),
                "sha256": hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest(),
            },
            limits=self.contract.budgets.model_dump(mode="json"),
            loop_budget=self.used,
            loop={"status": "ready"},
        )
        if require_module_grading and resume_state is None:
            self.manifest.data["milestone_4"] = {
                "status": "pending",
                "construction_epoch": 0,
                "modules": {},
            }
        self.actions.guard = self.guard
        self.actions.admit = self.admit_action
        self.manifest.data["execution_policy"] = {
            "version": "action-boundary-v1",
            "intention_seconds": "admission deadline; admitted verification may finish afterward",
            "global_wall_and_primitive_limits": "hard; reserved before dispatch",
            "comparison": "intention timing differs from the original v1 execution policy",
        }
        self.manifest.save()

    def remaining(self) -> float:
        deadline = (
            self.deadline
            if self.actions.action_active
            else min(self.deadline, self.intention_deadline or self.deadline)
        )
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise LoopLimit("Wall or intention time exhausted")
        return remaining

    def guard(self, charging: bool = False) -> None:
        self.remaining()
        if charging and self.intention_deadline is not None and not self.actions.action_active:
            if self.actions.used - self.intention_start >= self.intention_max:
                raise LoopLimit("Intention action limit exhausted")

    def admit_action(self, estimate: ActionEstimate) -> None:
        if estimate.seconds > self.deadline - self.clock():
            raise LoopLimit("Global time cannot cover action and verification")
        if self.intention_deadline is not None:
            if self.clock() >= self.intention_deadline:
                raise ActionAdmissionDenied("Intention admission deadline reached")
            if estimate.primitives > self.intention_max - (
                self.actions.used - self.intention_start
            ):
                raise ActionAdmissionDenied("Intention cannot cover action and verification")

    def _repair_context(self) -> tuple[str, list[dict[str, Any]]] | None:
        module: str | None = self.stop_after_module
        if module not in self.failed_module_checks and len(self.failed_module_checks) == 1:
            module = next(iter(self.failed_module_checks))
        if module is None or module not in self.failed_module_checks:
            return None
        failure = self.failed_module_checks[module]
        return module, [
            {
                key: item[key]
                for key in ("case", "target", "reason", "failure_reason", "expected", "actual")
                if key in item
            }
            for item in failure["failed_checks"]
        ]

    @staticmethod
    def _cell_hardware(state: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": state.get("name"),
            "properties": {
                key: value
                for key, value in state.get("properties", {}).items()
                if key not in {"power", "powered", "lit", "locked"}
            },
        }

    def charge(self, name: str) -> None:
        self.guard(name == "jev_calls")
        if self.actions.stopped or self.actions.used >= self.actions.maximum:
            raise LoopLimit("Primitive action budget exhausted or runtime stopped")
        if self.used[name] >= getattr(self.contract.budgets, name):
            raise LoopLimit(name)
        self.used[name] += 1
        self.manifest.save()

    def _declaration_layout_mismatches(self, declaration: Any) -> list[dict[str, Any]]:
        """Compare declared interface cells with verified world readbacks."""
        return validate_declaration_layout(self.layout, declaration)

    async def run(self, observation: dict[str, Any]) -> None:
        if self.running:
            raise ValueError("A trial loop cannot be reused")
        self.running = True
        repair = False
        validation_retries = 0
        last_invalid_response_sha256: str | None = None
        limit_reached = False
        try:
            while True:
                self.intention_deadline = None
                if repair:
                    self.charge("repair_rounds")
                if (
                    self.require_module_grading
                    and self.construction_intentions_since_grade
                    >= MAX_CONSTRUCTION_INTENTIONS_BEFORE_GRADE
                ):
                    if (
                        self.construction_intentions_since_grade
                        % MAX_CONSTRUCTION_INTENTIONS_BEFORE_GRADE
                        == 0
                    ):
                        self.context.feedback(
                            {
                                "readiness_reminder": (
                                    "Assess readiness for load, hold and reset che"
                                    "cks. "
                                    "Request grading when ready; otherwise continue "
                                    "building the missing hardware."
                                )
                            }
                        )
                self.context.latest_verified_layout = self.layout.to_jsonable()
                request = self.context.request(observation)
                if self.failed_module_checks:
                    prompt = json.loads(request.prompt)
                    prompt["unresolved_module_failures"] = self.failed_module_checks
                    request = request.model_copy(
                        update={
                            "prompt": json.dumps(prompt, allow_nan=False),
                            "system": (
                                "Prioritize the unresolved module failures in "
                                "the prompt. "
                                "The failures describe the last inspection "
                                "and may predate repairs. "
                                "Compare them with latest_verified_layout befo"
                                "re acting. "
                                "If the expected hardware is already present, "
                                "request a new "
                                "module inspection instead of breaking or repl"
                                "acing it again. "
                                "Otherwise repair the specific failed interfac"
                                "e or behavior "
                                "before unrelated wiring, then request grading. " + request.system
                            ),
                        }
                    )
                if self.stop_after_module == "register":
                    request = request.model_copy(
                        update={
                            "system": (
                                "This run targets ONLY an independently verifi"
                                "ed "
                                "four-bit register. "
                                "Do not build arithmetic, PC, program storage "
                                "or output modules. "
                                "Finish your own register design in its existi"
                                "ng location. "
                                "It must load every value 0..15 on STEP, retai"
                                "n it without STEP, "
                                "and clear all bits on reset. Build actual dat"
                                "a input controls, "
                                "four probes, reset and STEP before requesting"
                                " grading. "
                                "The grader supplies a two-tick STEP pulse for"
                                " each load "
                                "and samples after settling. STEP being low at"
                                " the settled "
                                "readback is expected; it does not imply a mis"
                                "sing pulse. "
                                "Before repeating a failed repair, observe and"
                                " trace the "
                                "data input, stored bit, STEP gate and output "
                                "path. "
                                "Identify the first observed point where the s"
                                "ignal differs "
                                "from your design and repair that point. Do no"
                                "t alternate "
                                "wire and torch at the same cell without new d"
                                "iagnostic "
                                "evidence. A direct input-to-lamp connection a"
                                "lone cannot "
                                "satisfy retention: identify where your circui"
                                "t stores each "
                                "bit and how STEP controls updates while reset"
                                " clears it. "
                                "Continue repairing this register until "
                                "its behavioral suite passes. " + request.system
                            )
                        }
                    )
                timeout_retries = 0
                while True:
                    self.charge("planner_calls")
                    event = self.manifest.attempt("planner_call", request.model_dump(mode="json"))
                    try:
                        async with asyncio.timeout(min(PLANNER_TIMEOUT_SECONDS, self.remaining())):
                            response = await self.planner.complete(request)
                    except TimeoutError:
                        retry_scheduled = (
                            timeout_retries == 0
                            and self.used["planner_calls"] < self.contract.budgets.planner_calls
                            and self.clock() < self.deadline
                        )
                        timeout_event = self.manifest.attempt(
                            "planner_timeout",
                            {
                                "planner_call_sequence": event,
                                "attempt": timeout_retries + 1,
                            },
                        )
                        self.manifest.observed(timeout_event, {"retry_scheduled": retry_scheduled})
                        if not retry_scheduled:
                            raise
                        timeout_retries += 1
                        continue
                    break
                # Public output and identity/usage only; omit separate private reasoning.
                self.manifest.observed(
                    event, response.model_dump(mode="json", exclude={"reasoning"})
                )
                self.remaining()
                duplicate_placement_ids: set[str] = set()
                try:
                    intention = validate_intention(json.loads(response.text), self.contract)
                    if self.strict_module_gate:
                        from noob_agent.redstone.module_gate import admit

                        admit(intention, self.manifest.data["milestone_4"]["modules"])
                    ConstructionExecution(intention.actions, verified_layout=self.layout)
                    if intention.register_bit_check is not None and (
                        not self.register_workshop
                        or intention.actions
                        or intention.request_grading
                        or intention.module_inspection is None
                        or intention.module_inspection.module != "register"
                    ):
                        raise ValueError(
                            "A register bit diagnostic requires workshop m"
                            "ode, a register "
                            "declaration, and no other request or actions"
                        )
                    if (
                        self.stop_after_module is not None
                        and intention.module_inspection is not None
                        and intention.module_inspection.module != self.stop_after_module
                    ):
                        raise ValueError("This checkpoint requires the register module only")
                    if self.task == "lamp_repair":
                        if len(intention.actions) != 1:
                            raise ValueError(
                                "Lamp repair demo requires exactly one action per intention"
                            )
                        if intention.module_inspection is not None or intention.request_grading:
                            raise ValueError("Lamp repair demo does not accept module grading")
                        positions = {
                            tuple(LEVER_POSITION),
                            tuple(WIRE_POSITION),
                            tuple(LAMP_POSITION),
                        }
                        for offer in intention.actions:
                            position = tuple(offer.position)
                            allowed = (
                                (offer.action == "observe" and position in positions)
                                or (
                                    offer.action == "interact" and position == tuple(LEVER_POSITION)
                                )
                                or (
                                    offer.action == "place"
                                    and position == tuple(WIRE_POSITION)
                                    and offer.block == "minecraft:redstone_wire"
                                    and not offer.properties
                                )
                            )
                            if not allowed:
                                raise ValueError(
                                    "Lamp repair demo permits observations of the "
                                    "three "
                                    "fixture cells, lever interaction, and redston"
                                    "e wire "
                                    "placement at the open connection only."
                                )
                    if self.require_module_grading:
                        removed_cells = {
                            tuple(offer.position)
                            for offer in intention.actions
                            if offer.action == "break"
                        }
                        observed_cells = {
                            tuple(c["position"]): c for c in self.layout.to_jsonable()
                        }
                        for offer in intention.actions:
                            if offer.action != "place" or offer.block is None:
                                continue
                            placement_cell = tuple(offer.position)
                            observed_cell = observed_cells.get(placement_cell, {})
                            state = (offer.block, tuple(sorted((offer.properties or {}).items())))
                            already_verified = (
                                self.placed_cells.get(placement_cell) == state
                                or observed_cell.get("name") == offer.block
                                and all(
                                    observed_cell.get("properties", {}).get(k) == v
                                    for k, v in (offer.properties or {}).items()
                                )
                            )
                            if placement_cell not in removed_cells and already_verified:
                                duplicate_placement_ids.add(offer.id)
                        if (
                            duplicate_placement_ids
                            and len(duplicate_placement_ids) == len(intention.actions)
                            and intention.module_inspection is None
                            and not intention.request_grading
                        ):
                            offer = next(
                                action
                                for action in intention.actions
                                if action.id in duplicate_placement_ids
                            )
                            placement_cell = tuple(offer.position)
                            raise ValueError(
                                "All offered placements are already verified; "
                                "repeated "
                                f"block at x {placement_cell[0]} y {placement_cell[1]} "
                                f"z {placement_cell[2]}. "
                                "Change the circuit or request grading."
                            )
                        minimum_actions = sum(
                            {"place": 6, "break": 5, "interact": 4, "observe": 1}[offer.action]
                            for offer in intention.actions
                        )
                        if intention.max_actions < minimum_actions:
                            raise ValueError(
                                "max_actions cannot cover offered actions and "
                                "readbacks; "
                                "omit it for default 256 or offer fewer actions."
                            )
                        if (
                            self.signal_control_placements == 0
                            and intention.actions
                            and any(offer.action == "place" for offer in intention.actions)
                            and not any(
                                offer.action == "place" and offer.block in FUNCTIONAL_BLOCKS
                                for offer in intention.actions
                            )
                        ):
                            raise ValueError(
                                "No signal or control block exists yet; includ"
                                "e a lever, "
                                "wire, torch, repeater, comparator, or lamp wi"
                                "th support."
                            )
                        requested_declaration = intention.module_inspection
                        if self.context.force_grading and (
                            requested_declaration is None or intention.actions
                        ):
                            raise ValueError(
                                "Construction limit reached; return a complete"
                                " behavioral "
                                "module_inspection with empty actions now."
                            )
                        if requested_declaration is not None:
                            recipe_help = {
                                "register": "recipe_templates.load for load:0..15",
                                "arithmetic": ("recipe_templates.load and add plus recipes.out"),
                                "storage": "recipe_templates.address and write",
                                "output": ("recipe_templates.load and add plus recipes.out"),
                            }
                            for key in sorted(required_recipes(requested_declaration.module)):
                                try:
                                    resolve_recipe(requested_declaration, key)
                                except ValueError as error:
                                    raise ValueError(
                                        f"Missing {requested_declaration.module} recipe {key}; "
                                        f"provide {recipe_help[requested_declaration.module]}"
                                    ) from error
                except json.JSONDecodeError as error:
                    if response.finish_reason == "length":
                        reason = "Response reached its output token cap before complete JSON."
                    else:
                        reason = (
                            "Response was not one complete JSON intention "
                            f"(line {error.lineno}, column {error.colno})."
                        )
                except ValidationError as error:
                    reason = _validation_field_reason(error)
                except ValueError as error:
                    raw_reason = str(error)
                    if raw_reason == "Target outside inclusive build bounds":
                        raw_reason = (
                            "Target outside inclusive build bounds; use x="
                            "0..95, "
                            "y=64..95, z=0..95 for every offered action."
                        )
                    reason = (
                        raw_reason
                        if raw_reason
                        and len(raw_reason) <= 180
                        and raw_reason.isascii()
                        and all(char.isalnum() or char in " _.,:;()/-'" for char in raw_reason)
                        else "Intention failed strict validation."
                    )
                else:
                    reason = ""
                if reason:
                    response_sha256 = hashlib.sha256(response.text.encode()).hexdigest()
                    repeat_response = response_sha256 == last_invalid_response_sha256
                    validation = {
                        "planner_call_sequence": event,
                        "reason": reason,
                        "response_sha256": response_sha256,
                        "response_characters": len(response.text),
                    }
                    retry_scheduled = validation_retries < 2 and not repeat_response
                    validation_event = self.manifest.attempt("planner_validation", validation)
                    self.manifest.observed(
                        validation_event,
                        {
                            "accepted": False,
                            "jev_dispatched": False,
                            "world_actions": 0,
                            "retry_scheduled": retry_scheduled,
                            "repeat_response": repeat_response,
                        },
                    )
                    if not retry_scheduled:
                        raise PlannerValidationExhausted(reason)
                    validation_retries += 1
                    last_invalid_response_sha256 = response_sha256
                    instruction = planner_validation_instruction(reason, response.finish_reason)
                    self.context.feedback(
                        {
                            "intention_rejected": reason,
                            "instruction": instruction,
                        }
                    )
                    repair = True
                    continue
                validation_retries = 0
                last_invalid_response_sha256 = None
                self.manifest.data["planner_intentions"].append(intention.model_dump(mode="json"))
                if intention.circuit_plan is not None:
                    self.context.update_circuit_plan(intention.circuit_plan)
                self.manifest.save()
                if self.require_module_grading and intention.request_grading:
                    self.context.grading_enabled = True
                self.intention_deadline = self.clock() + intention.max_seconds
                self.intention_start, self.intention_max = self.actions.used, intention.max_actions
                offers = {
                    action.id: action
                    for action in intention.actions
                    if action.id not in duplicate_placement_ids
                }
                execution = ConstructionExecution(
                    [
                        offer.model_copy(
                            update={
                                "depends_on": [dep for dep in offer.depends_on if dep in offers]
                            }
                        )
                        for offer in offers.values()
                    ],
                    verified_layout=self.layout,
                )
                results: list[dict[str, Any]] = []
                for offer in intention.actions:
                    if offer.id not in duplicate_placement_ids:
                        continue
                    skipped = {
                        "id": offer.id,
                        "skipped": "identical_verified_placement",
                        "position": offer.position,
                    }
                    skipped_event = self.manifest.attempt("action_offer_skipped", skipped)
                    self.manifest.observed(skipped_event, skipped)
                    results.append(skipped)
                repair = False
                section_selected = False
                while offers:
                    if (
                        self.batch_construction
                        and self.clock() < self.deadline
                        and (
                            self.clock() >= (self.intention_deadline or self.deadline)
                            or self.actions.used - self.intention_start >= self.intention_max
                        )
                    ):
                        results.append(
                            {
                                "deferred": "action_admission",
                                "reason": "section budget ended before next primitive",
                            }
                        )
                        break
                    eligible = execution.eligible()
                    blocked_repairs = []
                    repair_context = self._repair_context()
                    if repair_context is not None:
                        module, failure = repair_context
                        for key, candidate in list(eligible.items()):
                            cell = ",".join(map(str, candidate.position))
                            if (
                                candidate.action != "observe"
                                and self.repair_guard.blocks_repeated_repair(
                                    module, cell, candidate.position, failure
                                )
                            ):
                                blocked_repairs.append(
                                    self.repair_guard.repair_feedback(
                                        module, cell, candidate.position, failure
                                    )
                                )
                                eligible.pop(key)
                    if blocked_repairs:
                        results.append({"repair_cycle_blocked": blocked_repairs})
                    if not eligible:
                        break
                    criteria = {key: offer.criteria for key, offer in eligible.items()}
                    if not self.require_module_grading:
                        criteria["__finish__"] = (
                            "End this intention and return actual results to planner"
                        )
                    jev_request = action_request(
                        {
                            "intention": intention.model_dump(mode="json"),
                            "observation": observation,
                            "results": results,
                            "remaining_actions": self.intention_max
                            - (self.actions.used - self.intention_start),
                        },
                        criteria,
                    )
                    if self.batch_construction and section_selected:
                        # Explicit assisted-production authorization: execute the
                        # next dependency-ready primitive in planner order. This
                        # is not a provider response or a Jev call.
                        chosen = next(iter(eligible))
                        event = self.manifest.attempt(
                            "construction_batch_selection",
                            {
                                "action_id": chosen,
                                "eligible_ids": list(eligible),
                                "method": "dependency-ready planner order after first Je"
                                "v selection",
                            },
                        )
                        answer = {"answers": {"action": {"choice": chosen}}}
                        self.manifest.observed(event, {"action_id": chosen, "provider_call": False})
                    else:
                        gateway_retries = 0
                        while True:
                            if self.last_jev_started is not None:
                                delay = self.jev_min_interval_seconds - (
                                    self.clock() - self.last_jev_started
                                )
                                if delay > 0:
                                    if delay >= self.remaining():
                                        raise LoopLimit("Jev pacing exceeds intention time")
                                    await asyncio.sleep(delay)
                            self.charge("jev_calls")
                            event = self.manifest.attempt("jev_call", jev_request)
                            try:
                                self.last_jev_started = self.clock()
                                answer = self.jev.evaluate(
                                    jev_request, timeout=min(30, self.remaining())
                                )
                            except JevError as error:
                                if (
                                    (error.diagnostic or {}).get("name") == "SelectionTimeout"
                                    and self.intention_deadline is not None
                                    and self.clock() >= self.intention_deadline
                                    and self.clock() < self.deadline
                                ):
                                    exhausted = {
                                        "provider_error": error.diagnostic,
                                        "intention_time_exhausted": True,
                                        "guidance": (
                                            "Continue unfinished work in the next intention."
                                        ),
                                    }
                                    self.manifest.observed(event, exhausted)
                                    results.append(exhausted)
                                    answer = None
                                    break
                                if (error.diagnostic or {}).get(
                                    "statusCode"
                                ) != 503 or gateway_retries >= JEV_HTTP_503_RETRIES_PER_SELECTION:
                                    raise
                                gateway_retries += 1
                                self.manifest.observed(
                                    event,
                                    {"provider_error": error.diagnostic, "retry_scheduled": True},
                                )
                                continue
                            self.manifest.observed(event, answer)
                            break
                    if answer is None:
                        break
                    if (
                        self.batch_construction
                        and self.clock() < self.deadline
                        and self.clock() >= (self.intention_deadline or self.deadline)
                    ):
                        results.append(
                            {
                                "deferred": "action_admission",
                                "reason": "section ended during selection; no primitive dispatched",
                            }
                        )
                        break
                    self.remaining()
                    section_selected = True
                    choice = selected_action(answer, jev_request)
                    if choice == "__finish__":
                        break
                    offer = offers.pop(choice)
                    execution.start(choice)
                    if self.announce is not None:
                        self.announce(offer.action, offer.block)
                    if self.require_module_grading and offer.action != "observe":
                        milestone = self.manifest.data["milestone_4"]
                        prior_modules = sorted(milestone["modules"])
                        if self.strict_module_gate:
                            from noob_agent.redstone.module_gate import affected_modules

                            prior_modules = sorted(affected_modules(offer.position, prior_modules))
                        if prior_modules:
                            invalidation = self.manifest.attempt(
                                "milestone_4_invalidation",
                                {"action_id": choice, "modules": prior_modules},
                            )
                            self.manifest.observed(
                                invalidation,
                                {"reason": "construction_action_attempted"},
                            )
                        milestone["construction_epoch"] += 1
                        for prior_module in prior_modules:
                            milestone["modules"].pop(prior_module, None)
                        milestone["status"] = "pending"
                        self.manifest.save()
                    try:
                        actual = (
                            self.actions.observe(offer.position)
                            if offer.action == "observe"
                            else self.actions.apply(
                                offer.action, offer.position, offer.block, offer.properties
                            )
                        )
                        result: dict[str, Any] = {"id": choice, "result": actual}
                        repair_context = self._repair_context()
                        if repair_context is not None:
                            module, failure = repair_context
                            cell = ",".join(map(str, offer.position))
                            if offer.action == "observe":
                                declaration_value = self.failed_module_checks[module]["declaration"]
                                relevant = {
                                    tuple(control["position"])
                                    for control in declaration_value.get("controls", [])
                                } | {
                                    tuple(probe["position"])
                                    for probes in declaration_value.get("probes", {}).values()
                                    for probe in probes
                                }
                                if self.context.circuit_plan is not None:
                                    relevant.update(
                                        tuple(position)
                                        for path in self.context.circuit_plan.signal_paths
                                        for position in path.positions
                                    )
                                for tracked in self.repair_cell_states:
                                    if tracked.startswith(module + ":"):
                                        tracked_cell = tracked.split(":", 1)[1]
                                        tracked_position = list(map(int, tracked_cell.split(",")))
                                        if (
                                            tuple(offer.position) in relevant
                                            or cell == tracked_cell
                                        ):
                                            self.repair_guard.record_targeted_diagnostic(
                                                module,
                                                tracked_cell,
                                                tracked_position,
                                                failure,
                                                actual,
                                                explicit_request=True,
                                            )
                            elif offer.action == "place" and actual.get("effect_verified") is True:
                                before, after = actual.get("before"), actual.get("after")
                                if isinstance(before, dict) and isinstance(after, dict):
                                    tracked = module + ":" + cell
                                    previous = self.repair_cell_states.get(
                                        tracked, self._cell_hardware(before)
                                    )
                                    current = self._cell_hardware(after)
                                    self.repair_guard.record_observed_mutation(
                                        module, cell, offer.position, previous, current, failure
                                    )
                                    self.repair_cell_states[tracked] = current
                        if offer.action == "place" and actual.get("effect_verified") is True:
                            assert offer.block is not None
                            self.placed_cells[tuple(offer.position)] = (
                                offer.block,
                                tuple(sorted((offer.properties or {}).items())),
                            )
                            if offer.block == "minecraft:stone":
                                self.stone_placements += 1
                            elif offer.block in FUNCTIONAL_BLOCKS:
                                self.signal_control_placements += 1
                            self.layout.record_action("place", offer.position, actual)
                        elif offer.action == "break" and actual.get("effect_verified") is True:
                            self.placed_cells.pop(tuple(offer.position), None)
                            self.layout.record_action("break", offer.position, actual)
                        elif offer.action == "interact" and actual.get("effect_verified") is True:
                            after = actual.get("after")
                            if isinstance(after, dict):
                                self.layout.reconcile(offer.position, after)
                                if after.get("name") != "minecraft:air":
                                    self.placed_cells[tuple(offer.position)] = (
                                        after["name"],
                                        tuple(sorted(after.get("properties", {}).items())),
                                    )
                    except (ActionAdmissionDenied, LoopLimit) as error:
                        if isinstance(error, LoopLimit) and not (
                            self.batch_construction
                            and self.clock() < self.deadline
                            and self.actions.used < self.actions.maximum
                        ):
                            raise
                        result = {
                            "id": choice,
                            "deferred": "action_admission",
                            "reason": str(error),
                        }
                        execution.complete(choice, result, verified=False)
                        results.append(result)
                        break
                    except EffectMismatch:
                        # Actions already journaled the actual mismatching before/after state.
                        result = {
                            "id": choice,
                            "effect_mismatch": True,
                            "evidence": next(
                                event["result"]
                                for event in reversed(self.manifest.data["events"])
                                if event["kind"] == "bounded_action"
                            ),
                        }
                        evidence = result["evidence"]
                        diagnostic = evidence.get("placement_diagnostic", {})
                        if diagnostic.get("type") == "unsupported_support":
                            result["guidance"] = {
                                "instruction": (
                                    "Build the required support first, then retry "
                                    "the component in a later intention."
                                ),
                                "required_support": diagnostic["required_support"],
                            }
                        after = evidence.get("after") if isinstance(evidence, dict) else None
                        if isinstance(after, dict):
                            self.layout.reconcile(offer.position, after)
                            if after.get("name") == "minecraft:air":
                                self.placed_cells.pop(tuple(offer.position), None)
                            else:
                                self.placed_cells[tuple(offer.position)] = (
                                    after["name"],
                                    tuple(sorted(after.get("properties", {}).items())),
                                )
                        repair = True
                    except InvalidBlockState:
                        result = {
                            "id": choice,
                            "rejected": "invalid_block_state",
                            "guidance": (
                                "Check exact block property names and JSON val"
                                "ue types. "
                                "Boolean properties require JSON true/false, n"
                                "ot quoted "
                                "strings; omit properties unless required."
                            ),
                        }
                        repair = True
                    results.append(result)
                    execution.complete(
                        choice,
                        result,
                        verified=(
                            not repair
                            and (
                                offer.action == "observe"
                                or isinstance(result.get("result"), dict)
                                and result["result"].get("effect_verified") is True
                            )
                        ),
                    )
                    event = self.manifest.attempt("action_feedback", result)
                    self.manifest.observed(event, result)
                    if repair:
                        break
                self.intention_deadline = None
                schedule = execution.finish_status()
                if schedule["remaining"]:
                    results.append({"construction_schedule": schedule})
                public: dict[str, Any] = {}
                admission_deferred = any(
                    result.get("deferred") == "action_admission" for result in results
                )
                if self.checker is not None and not admission_deferred:
                    self.remaining()
                    event = self.manifest.attempt("public_check", {})
                    public = self.checker(self.actions)
                    self.manifest.observed(event, public)
                    self.manifest.data["checks"].append(public)
                    self.manifest.save()
                    self.remaining()
                inspection: dict[str, Any] = {}
                if intention.machine_inspection is not None:
                    if not self.full_machine_verification or not self.context.machine_phase:
                        raise ValueError(
                            "Full machine interface offered before module checks passed"
                        )
                    from noob_agent.redstone.machine import verify_programs
                    from noob_agent.redstone.machine_world import ServerMachineWorld

                    interface = intention.machine_inspection.model_dump(mode="json")
                    if self.strict_module_gate:
                        from noob_agent.redstone.modules import validate_declaration

                        milestone = self.manifest.data["milestone_4"]
                        failed_rechecks = []
                        for prior_module, acceptance in list(milestone["modules"].items()):
                            recheck_declaration = validate_declaration(
                                acceptance["declaration"], self.contract
                            )
                            recheck = self.manifest.attempt(
                                "module_integration_recheck", {"module": prior_module}
                            )
                            evidence = grade_module(
                                self.actions, recheck_declaration, fail_fast=True
                            )
                            self.manifest.observed(recheck, evidence)
                            if evidence.get("behavioral_passed") is not True:
                                failed_rechecks.append(prior_module)
                                milestone["modules"].pop(prior_module, None)
                                self.failed_module_checks[prior_module] = {
                                    "failed_checks": evidence.get("failed_checks", []),
                                    "declaration": acceptance["declaration"],
                                }
                        if failed_rechecks:
                            self.require_module_grading = True
                            self.context.require_module_grading = True
                            self.context.machine_phase = False
                            self.context.grading_enabled = True
                            milestone["status"] = "pending"
                            self.manifest.save()
                            observation = {"integration_rechecks_failed": failed_rechecks}
                            continue
                    event = self.manifest.attempt("full_machine_grade", {"interface": interface})
                    inspection = verify_programs(ServerMachineWorld(self.actions, interface))
                    self.manifest.observed(event, inspection)
                    self.manifest.data["full_machine_verification"] = inspection
                    self.manifest.save()
                    if inspection["passed"]:
                        self.manifest.data["loop"] = {"status": "full_machine_behavior_passed"}
                        break
                    repair = True
                if intention.module_inspection is not None and not admission_deferred:
                    declaration = intention.module_inspection.model_dump(mode="json")
                    module = intention.module_inspection.module
                    prior_failure = self.failed_module_checks.get(module)
                    repeated_grade = (
                        self.require_module_grading
                        and prior_failure is not None
                        and self.repair_guard.blocks_regrade(
                            module,
                            prior_failure["failed_checks"],
                            self.layout.fingerprint(),
                            declaration,
                        )
                    )
                    event = self.manifest.attempt(
                        "module_regrade_blocked" if repeated_grade else "module_inspection",
                        {"module": module},
                    )
                    behavioral = bool(
                        intention.module_inspection.recipes
                        or intention.module_inspection.recipe_templates
                    )
                    if repeated_grade:
                        assert prior_failure is not None
                        inspection = {
                            "module": module,
                            "scope": "repair_guard",
                            "valid": False,
                            "behavioral_passed": False,
                            "failed_checks": prior_failure["failed_checks"],
                            "repair_required": True,
                            "reason": (
                                "This exact module declaration already failed "
                                "against the "
                                "same verified layout. Make a relevant physica"
                                "l repair "
                                "before requesting this grade again."
                            ),
                        }
                        self.manifest.observed(event, inspection)
                    else:
                        behavioral = bool(
                            intention.module_inspection.recipes
                            or intention.module_inspection.recipe_templates
                        )
                        if intention.register_bit_check is not None:
                            # The diagnostic checks its selected data path directly
                            # through the trusted grader. Do not pre-grade all four
                            # probes or let that diagnostic imply interface readiness.
                            readiness: dict[str, Any] = {
                                "module": "register",
                                "scope": "one_bit_diagnostic_prerequisites",
                                "valid": True,
                                "selected_probe_only": True,
                            }
                        else:
                            readiness = inspect_module(self.actions, intention.module_inspection)
                        # Inspection readbacks are authoritative. Reconcile them into the
                        # persistent layout before checking the declaration so stale local
                        # state cannot create a false mismatch.
                        for hardware_cell in readiness.get("hardware", []):
                            if isinstance(hardware_cell, dict) and isinstance(
                                hardware_cell.get("position"), list
                            ):
                                self.layout.reconcile(hardware_cell["position"], hardware_cell)
                        for failed in readiness.get("failed_checks", []):
                            failed_actual = (
                                failed.get("actual") if isinstance(failed, dict) else None
                            )
                            if isinstance(failed_actual, dict) and isinstance(
                                failed_actual.get("position"), list
                            ):
                                self.layout.reconcile(failed_actual["position"], failed_actual)
                        # Some unit-level callers replace inspect_module with a minimal
                        # stub. Only enforce declaration/layout consistency when we have
                        # actual hardware evidence (the production inspector always emits it).
                        layout_mismatches = (
                            self._declaration_layout_mismatches(declaration)
                            if intention.register_bit_check is None
                            and self.require_module_grading
                            and "hardware" in readiness
                            else []
                        )
                        readiness["verified_layout_mismatches"] = layout_mismatches
                        if layout_mismatches:
                            readiness["valid"] = False
                        if behavioral and readiness["valid"]:
                            if intention.register_bit_check is not None:
                                from noob_agent.redstone.register_workshop import grade_register_bit

                                diagnostic = grade_register_bit(
                                    self.actions,
                                    intention.module_inspection,
                                    intention.register_bit_check,
                                )
                                inspection = {
                                    "scope": "register_bit_diagnostic",
                                    "valid": True,
                                    "behavioral_passed": False,
                                    "diagnostic_passed": diagnostic["passed"],
                                    "complete": False,
                                    "checkpoint_eligible": False,
                                    "fullcomputer_eligible": False,
                                    "diagnostic": diagnostic,
                                    "interface_readiness": readiness,
                                }
                            else:
                                inspection = grade_module(
                                    self.actions, intention.module_inspection, fail_fast=True
                                )
                                inspection["interface_readiness"] = readiness
                        else:
                            inspection = readiness
                        self.manifest.observed(event, inspection)
                    if self.require_module_grading:
                        if intention.register_bit_check is None:
                            self.construction_intentions_since_grade = 0
                            self.context.force_grading = False
                        milestone = self.manifest.data["milestone_4"]
                        if intention.register_bit_check is None:
                            milestone["modules"].pop(module, None)
                        if (
                            not repair
                            and inspection.get("scope") == "public_module_behavior"
                            and inspection.get("complete") is True
                            and inspection.get("behavioral_passed") is True
                        ):
                            milestone["modules"][module] = {
                                "construction_epoch": milestone["construction_epoch"],
                                "grader_event": event,
                                "check_count": len(inspection["checks"]),
                                "declaration": declaration,
                            }
                        if set(milestone["modules"]) == PUBLIC_MODULES:
                            milestone["status"] = (
                                "workshop_modules_passed"
                                if self.register_workshop
                                else "checks_passed"
                            )
                        if milestone["status"] not in {"checks_passed", "workshop_modules_passed"}:
                            self.context.grading_enabled = False
                        failed_grade = inspection.get("valid") is False or (
                            inspection.get("scope") == "public_module_behavior"
                            and inspection.get("behavioral_passed") is False
                        )
                        if failed_grade:
                            failed_checks = inspection.get("failed_checks", [])
                            self.failed_module_checks[module] = {
                                "failed_checks": failed_checks,
                                "declaration": declaration,
                            }
                            self.repair_guard.record(
                                module,
                                failed_checks,
                                self.layout.fingerprint(),
                                declaration,
                            )
                        elif inspection.get("behavioral_passed") is True:
                            self.failed_module_checks.pop(module, None)
                            self.repair_guard.record_module_success(module)
                    if not behavioral or inspection.get("scope") == "interface_readback_only":
                        self.manifest.data["checks"].append(inspection)
                    self.manifest.save()
                    repair = (
                        repair
                        or inspection.get("valid") is False
                        or (
                            inspection.get("scope") == "public_module_behavior"
                            and inspection.get("behavioral_passed") is False
                        )
                    )
                elif self.require_module_grading and not intention.request_grading:
                    # The planner asked for the full declaration schema but chose
                    # another build turn; return to the bounded build schema.
                    self.context.grading_enabled = False
                if (
                    self.require_module_grading
                    and intention.module_inspection is None
                    and not intention.request_grading
                ):
                    self.construction_intentions_since_grade += 1
                    if (
                        self.construction_intentions_since_grade
                        >= MAX_CONSTRUCTION_INTENTIONS_BEFORE_GRADE
                    ):
                        self.context.force_grading = False
                observation = {"results": results, "check": public, "module_inspection": inspection}
                if self.require_module_grading:
                    milestone = self.manifest.data["milestone_4"]
                    observation["milestone_4"] = {
                        "construction_epoch": milestone["construction_epoch"],
                        "passed_modules": sorted(milestone["modules"]),
                        "remaining_modules": sorted(PUBLIC_MODULES - milestone["modules"].keys()),
                    }
                    observation["construction_progress"] = {
                        "stone_placements": self.stone_placements,
                        "signal_control_placements": self.signal_control_placements,
                        "verified_layout": self.layout.to_jsonable(),
                    }
                self.context.feedback(observation)
                continuation_state: dict[str, Any] = self.context.saved_state()
                continuation_state.update(
                    grading_enabled=self.context.grading_enabled,
                    force_grading=self.context.force_grading,
                    construction_intentions_since_grade=self.construction_intentions_since_grade,
                    stone_placements=self.stone_placements,
                    signal_control_placements=self.signal_control_placements,
                    placed_cells=[
                        {
                            "position": list(position),
                            "block": block,
                            "properties": dict(properties),
                        }
                        for position, (block, properties) in self.placed_cells.items()
                    ],
                    verified_layout=self.layout.to_jsonable(),
                    repair_guard=self.repair_guard.to_state(),
                    repair_cell_states=self.repair_cell_states,
                    failed_module_checks=self.failed_module_checks,
                )
                if self.checker is not None and hasattr(self.checker, "save_state"):
                    continuation_state["checker"] = self.checker.save_state()
                self.manifest.data["continuation"] = {
                    "ready": True,
                    "task": self.task,
                    "state": continuation_state,
                    "saved_at": time.time(),
                }
                self.manifest.save()
                repair = repair or public.get("passed") is False
                if (
                    self.stop_after_module is not None
                    and self.stop_after_module in self.manifest.data["milestone_4"]["modules"]
                    and not repair
                ):
                    self.manifest.data["module_checkpoint"] = {
                        "module": self.stop_after_module,
                        "status": "passed",
                        "baseline_comparable": not self.register_workshop,
                        **self.manifest.data["milestone_4"]["modules"][self.stop_after_module],
                    }
                    self.manifest.data["loop"] = {"status": "module_checkpoint_passed"}
                    self.manifest.save()
                    break
                if (
                    self.require_module_grading
                    and self.manifest.data["milestone_4"]["status"]
                    in {"checks_passed", "workshop_modules_passed"}
                    and not repair
                ):
                    if self.full_machine_verification:
                        self.require_module_grading = False
                        self.context.require_module_grading = False
                        self.context.machine_phase = True
                        self.context.grading_enabled = False
                        self.context.feedback(
                            {
                                "full_machine_integration_required": True,
                                "passed_modules": sorted(PUBLIC_MODULES),
                                "verified_layout": self.layout.to_jsonable(),
                            }
                        )
                        self.manifest.data["machine_phase"] = True
                        self.manifest.save()
                        continue
                    self.manifest.data["loop"] = {
                        "status": (
                            "workshop_modules_passed"
                            if self.register_workshop
                            else "public_modules_passed"
                        ),
                        "baseline_comparable": not self.register_workshop,
                    }
                    self.manifest.save()
                    break
                if (
                    public.get("complete") is True
                    and public.get("passed") is True
                    and not repair
                    and not inspection
                ):
                    self.manifest.data["loop"] = {"status": "checkpoint_complete"}
                    break
        except BaseException as error:
            limit_reached = isinstance(error, (LoopLimit, ActionLimit))
            recorded_error: dict[str, Any] = {
                "stage": "trial_loop",
                "type": type(error).__name__,
            }
            if isinstance(error, JevError) and error.diagnostic:
                recorded_error["provider_diagnostic"] = error.diagnostic
            if isinstance(error, PlannerValidationExhausted):
                recorded_error["validation_reason"] = error.reason
            self.manifest.data["errors"].append(recorded_error)
            self.manifest.data["loop"] = {"status": "stopped", "reason": type(error).__name__}
            if isinstance(error, PlannerValidationExhausted):
                self.manifest.data["loop"]["reason"] = "planner_validation_exhausted"
                self.manifest.data["loop"]["validation_reason"] = error.reason
            if not isinstance(error, Exception):
                raise
        finally:
            self.actions.stopped = True
            self.manifest.data["action_budget"] = {
                "used": self.actions.used,
                "limit": self.actions.maximum,
                "stopped": True,
                "reason": self.manifest.data["loop"]["status"],
            }
            continuation = self.manifest.data.get("continuation", {})
            if isinstance(continuation, dict):
                continuation["ready"] = bool(
                    limit_reached and continuation.get("state") is not None
                )
                continuation["stop_reason"] = self.manifest.data["loop"].get("reason")
                continuation["session_number"] = len(self.manifest.data.get("sessions", [])) or 1
                self.manifest.save()
            reasons = (
                [
                    "All four public modules passed on one constru"
                    "ction epoch; "
                    "trusted final reset still required for milest"
                    "one 4 acceptance",
                    "Full-machine independent grading and recording pending; model success false",
                ]
                if self.manifest.data.get("milestone_4", {}).get("status") == "checks_passed"
                else [
                    "Public checkpoint only; real-provider and full-machine verification pending",
                    "Recording pending; no model success or milestone acceptance",
                ]
            )
            self.manifest.finish_incomplete(reasons)
