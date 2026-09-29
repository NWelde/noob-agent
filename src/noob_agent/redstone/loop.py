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
from noob_agent.redstone.actions import ActionLimit, Actions, EffectMismatch, InvalidBlockState
from noob_agent.redstone.behavior import grade_module, required_recipes
from noob_agent.redstone.demo import LAMP_POSITION, LEVER_POSITION, WIRE_POSITION
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
        if task == "lamp_repair" and require_module_grading:
            raise ValueError("Lamp repair demo does not use computer module grading")
        self.context = PlannerContext(
            self.contract,
            require_module_grading=require_module_grading,
            task=task,
            history=None if resume_state is None else resume_state.get("history", []),
        )
        if resume_state is not None:
            self.context.grading_enabled = resume_state.get("grading_enabled") is True
            self.context.force_grading = resume_state.get("force_grading") is True
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
        self.manifest.save()

    def remaining(self) -> float:
        deadline = min(self.deadline, self.intention_deadline or self.deadline)
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise LoopLimit("Wall or intention time exhausted")
        return remaining

    def guard(self, charging: bool = False) -> None:
        self.remaining()
        if charging and self.intention_deadline is not None:
            if self.actions.used - self.intention_start >= self.intention_max:
                raise LoopLimit("Intention action limit exhausted")

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
                    self.context.force_grading = True
                    self.context.grading_enabled = True
                request = self.context.request(observation)
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
                                    "Lamp repair demo permits observations of the three "
                                    "fixture cells, lever interaction, and redstone wire "
                                    "placement at the open connection only."
                                )
                    if self.require_module_grading:
                        for offer in intention.actions:
                            if offer.action != "place" or offer.block is None:
                                continue
                            cell = tuple(offer.position)
                            state = (offer.block, tuple(sorted((offer.properties or {}).items())))
                            if self.placed_cells.get(cell) == state:
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
                            cell = tuple(offer.position)
                            raise ValueError(
                                "All offered placements are already verified; repeated "
                                f"block at x {cell[0]} y {cell[1]} z {cell[2]}. "
                                "Change the circuit or request grading."
                            )
                        minimum_actions = sum(
                            {"place": 5, "break": 4, "interact": 4, "observe": 1}[offer.action]
                            for offer in intention.actions
                        )
                        if intention.max_actions < minimum_actions:
                            raise ValueError(
                                "max_actions cannot cover offered actions and readbacks; "
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
                                "No signal or control block exists yet; include a lever, "
                                "wire, torch, repeater, comparator, or lamp with support."
                            )
                        declaration = intention.module_inspection
                        if self.context.force_grading and (
                            declaration is None or intention.actions
                        ):
                            raise ValueError(
                                "Construction limit reached; return a complete behavioral "
                                "module_inspection with empty actions now."
                            )
                        if declaration is not None:
                            recipe_help = {
                                "register": "recipe_templates.load for load:0..15",
                                "arithmetic": "recipe_templates.load and add plus recipes.out",
                                "storage": "recipe_templates.address and write",
                                "output": "recipe_templates.load and add plus recipes.out",
                            }
                            for key in sorted(required_recipes(declaration.module)):
                                try:
                                    resolve_recipe(declaration, key)
                                except ValueError as error:
                                    raise ValueError(
                                        f"Missing {declaration.module} recipe {key}; "
                                        f"provide {recipe_help[declaration.module]}"
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
                            "Target outside inclusive build bounds; use x=0..95, "
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
                        raise ValueError("Repeated planner validation failure")
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
                while offers:
                    criteria = {key: offer.criteria for key, offer in offers.items()}
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
                    self.remaining()
                    choice = selected_action(answer, jev_request)
                    if choice == "__finish__":
                        break
                    offer = offers.pop(choice)
                    if self.announce is not None:
                        self.announce(offer.action, offer.block)
                    if self.require_module_grading and offer.action != "observe":
                        milestone = self.manifest.data["milestone_4"]
                        prior_modules = sorted(milestone["modules"])
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
                        milestone["modules"] = {}
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
                                "Check exact block property names and JSON value types. "
                                "Boolean properties require JSON true/false, not quoted "
                                "strings; omit properties unless required."
                            ),
                        }
                        repair = True
                    results.append(result)
                    event = self.manifest.attempt("action_feedback", result)
                    self.manifest.observed(event, result)
                    if repair:
                        break
                self.intention_deadline = None
                public: dict[str, Any] = {}
                if self.checker is not None:
                    self.remaining()
                    event = self.manifest.attempt("public_check", {})
                    public = self.checker(self.actions)
                    self.manifest.observed(event, public)
                    self.manifest.data["checks"].append(public)
                    self.manifest.save()
                    self.remaining()
                inspection: dict[str, Any] = {}
                if intention.module_inspection is not None:
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
                    if repeated_grade:
                        inspection = {
                            "module": module,
                            "scope": "repair_guard",
                            "valid": False,
                            "behavioral_passed": False,
                            "failed_checks": prior_failure["failed_checks"],
                            "repair_required": True,
                            "reason": (
                                "This exact module declaration already failed against the "
                                "same verified layout. Make a relevant physical repair "
                                "before requesting this grade again."
                            ),
                        }
                        self.manifest.observed(event, inspection)
                    else:
                        behavioral = bool(
                            intention.module_inspection.recipes
                            or intention.module_inspection.recipe_templates
                        )
                        readiness = inspect_module(self.actions, intention.module_inspection)
                        # Inspection readbacks are authoritative. Reconcile them into the
                        # persistent layout before checking the declaration so stale local
                        # state cannot create a false mismatch.
                        for cell in readiness.get("hardware", []):
                            if isinstance(cell, dict) and isinstance(cell.get("position"), list):
                                self.layout.reconcile(cell["position"], cell)
                        for failed in readiness.get("failed_checks", []):
                            actual = failed.get("actual") if isinstance(failed, dict) else None
                            if isinstance(actual, dict) and isinstance(
                                actual.get("position"), list
                            ):
                                self.layout.reconcile(actual["position"], actual)
                        # Some unit-level callers replace inspect_module with a minimal
                        # stub. Only enforce declaration/layout consistency when we have
                        # actual hardware evidence (the production inspector always emits it).
                        layout_mismatches = (
                            self._declaration_layout_mismatches(declaration)
                            if self.require_module_grading and "hardware" in readiness
                            else []
                        )
                        readiness["verified_layout_mismatches"] = layout_mismatches
                        if layout_mismatches:
                            readiness["valid"] = False
                        if behavioral and readiness["valid"]:
                            inspection = grade_module(
                                self.actions, intention.module_inspection, fail_fast=True
                            )
                            inspection["interface_readiness"] = readiness
                        else:
                            inspection = readiness
                        self.manifest.observed(event, inspection)
                    if self.require_module_grading:
                        self.construction_intentions_since_grade = 0
                        self.context.force_grading = False
                        milestone = self.manifest.data["milestone_4"]
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
                            }
                        if set(milestone["modules"]) == PUBLIC_MODULES:
                            milestone["status"] = "checks_passed"
                        if milestone["status"] != "checks_passed":
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
                        self.context.force_grading = True
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
                continuation_state: dict[str, Any] = {"history": self.context.history}
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
                    self.require_module_grading
                    and self.manifest.data["milestone_4"]["status"] == "checks_passed"
                    and not repair
                ):
                    self.manifest.data["loop"] = {"status": "public_modules_passed"}
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
            self.manifest.data["errors"].append(recorded_error)
            self.manifest.data["loop"] = {"status": "stopped", "reason": type(error).__name__}
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
                    "All four public modules passed on one construction epoch; "
                    "trusted final reset still required for milestone 4 acceptance",
                    "Full-machine independent grading and recording pending; model success false",
                ]
                if self.manifest.data.get("milestone_4", {}).get("status") == "checks_passed"
                else [
                    "Public checkpoint only; real-provider and full-machine verification pending",
                    "Recording pending; no model success or milestone acceptance",
                ]
            )
            self.manifest.finish_incomplete(reasons)
