"""Single-trial planner/Jev execution; checkpoint success is never machine success."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Callable
from typing import Any, Protocol

from noob_agent.models.client import ModelClient
from noob_agent.redstone.actions import Actions, EffectMismatch
from noob_agent.redstone.behavior import grade_module
from noob_agent.redstone.jev import action_request, selected_action
from noob_agent.redstone.modules import inspect_module
from noob_agent.redstone.planner import PlannerContext, validate_intention
from noob_agent.redstone.trial import CONTRACT_PATH, TrialManifest


class Evaluator(Protocol):
    def evaluate(self, request: dict[str, Any], *, timeout: float) -> dict[str, Any]: ...


class LoopLimit(RuntimeError):
    pass


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
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if "loop" in manifest.data or actions.used or actions.stopped:
            raise ValueError("Loop requires a fresh runtime; resumption is not supported")
        self.manifest, self.actions, self.planner, self.jev = manifest, actions, planner, jev
        self.contract = actions.contract
        self.context = PlannerContext(self.contract)
        self.clock, self.started = clock, clock()
        self.deadline = self.started + self.contract.budgets.wall_seconds
        self.intention_deadline: float | None = None
        self.intention_start = 0
        self.intention_max = self.contract.budgets.intention_actions
        self.checker = check
        self.used = {"planner_calls": 0, "jev_calls": 0, "repair_rounds": 0}
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

    async def run(self, observation: dict[str, Any]) -> None:
        if self.running:
            raise ValueError("A trial loop cannot be reused")
        self.running = True
        repair = False
        try:
            while True:
                self.intention_deadline = None
                if repair:
                    self.charge("repair_rounds")
                request = self.context.request(observation)
                self.charge("planner_calls")
                event = self.manifest.attempt("planner_call", request.model_dump(mode="json"))
                async with asyncio.timeout(min(30, self.remaining())):
                    response = await self.planner.complete(request)
                # Public output and identity/usage only; omit separate private reasoning.
                self.manifest.observed(
                    event, response.model_dump(mode="json", exclude={"reasoning"})
                )
                self.remaining()
                intention = validate_intention(json.loads(response.text), self.contract)
                self.manifest.data["planner_intentions"].append(intention.model_dump(mode="json"))
                self.manifest.save()
                self.intention_deadline = self.clock() + intention.max_seconds
                self.intention_start, self.intention_max = self.actions.used, intention.max_actions
                offers = {action.id: action for action in intention.actions}
                results: list[dict[str, Any]] = []
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
                    self.charge("jev_calls")
                    event = self.manifest.attempt("jev_call", jev_request)
                    answer = self.jev.evaluate(jev_request, timeout=min(30, self.remaining()))
                    self.manifest.observed(event, answer)
                    self.remaining()
                    choice = selected_action(answer, jev_request)
                    if choice == "__finish__":
                        break
                    offer = offers.pop(choice)
                    try:
                        actual = (
                            self.actions.observe(offer.position)
                            if offer.action == "observe"
                            else self.actions.apply(
                                offer.action, offer.position, offer.block, offer.properties
                            )
                        )
                        result: dict[str, Any] = {"id": choice, "result": actual}
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
                    # Inspection shares global budgets and returns to the same conversation.
                    event = self.manifest.attempt(
                        "module_inspection", intention.module_inspection.model_dump(mode="json")
                    )
                    behavioral = bool(
                        intention.module_inspection.recipes
                        or intention.module_inspection.recipe_templates
                    )
                    inspection = (
                        grade_module(self.actions, intention.module_inspection, fail_fast=True)
                        if behavioral
                        else inspect_module(self.actions, intention.module_inspection)
                    )
                    self.manifest.observed(event, inspection)
                    if not behavioral:
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
                observation = {"results": results, "check": public, "module_inspection": inspection}
                self.context.feedback(observation)
                repair = repair or public.get("passed") is False
                if (
                    public.get("complete") is True
                    and public.get("passed") is True
                    and not repair
                    and not inspection
                ):
                    self.manifest.data["loop"] = {"status": "checkpoint_complete"}
                    break
        except BaseException as error:
            self.manifest.data["errors"].append(
                {"stage": "trial_loop", "type": type(error).__name__}
            )
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
            self.manifest.finish_incomplete(
                [
                    "Public checkpoint only; real-provider and full-machine verification pending",
                    "Recording pending; no model success or milestone acceptance",
                ]
            )
