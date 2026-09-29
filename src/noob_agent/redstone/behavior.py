"""Public module checks. Expectations stay in Python, outside world functions."""

from __future__ import annotations

from typing import Any

from noob_agent.redstone.actions import Actions
from noob_agent.redstone.grading import ControlEvidenceMismatch, GraderControl
from noob_agent.redstone.modules import ModuleDeclaration, resolve_recipe, validate_declaration
from noob_agent.redstone.rcon import RconClient

# Two complementary patterns exercise both states of every physical storage bit.
STORAGE_PATTERNS = (
    (0, 63, 21, 42, 15, 48, 32, 16),
    (63, 0, 42, 21, 48, 15, 31, 47),
)
ARITHMETIC_PAIRS = ((0, 0), (0, 15), (15, 0), (15, 1), (14, 5), (7, 8))
MODULE_TICKS = 96000
MODULE_COMMANDS = 1000000
# Maximum observed excess across the available infrastructure proofs: 2.994 s
# on an older four-probe timeline before batched score readback. This fallback
# is conservative relative to current samples, but is not a statistical bound.
FALLBACK_TIMELINE_OVERHEAD_SECONDS = 2.994
# Deliberately below the provider-free 1,600-tick sprint proof's measured rate.
# A forecast is advisory; the live wall deadline and exact tick checks still gate.
SPRINT_FORECAST_TPS = 100


def estimate_module_schedule(declaration: ModuleDeclaration) -> dict[str, int | float]:
    """Exact scheduled-work forecast for the frozen suite and validated recipes."""
    ticks = 0
    timelines = 0
    reset_id = next(c.id for c in declaration.controls if c.role == "reset")

    def run(operations: list[dict[str, Any]]) -> None:
        nonlocal ticks, timelines
        ticks += sum(operation.get("wait", 0) for operation in operations)
        timelines += 1

    def run_cycles(recipes: list[list[dict[str, Any]]]) -> None:
        nonlocal ticks, timelines
        ticks += 200 * len(recipes)
        ticks += sum(operation.get("wait", 0) for recipe_ops in recipes for operation in recipe_ops)
        timelines += 1

    def run_samples(recipes: list[list[dict[str, Any]]]) -> None:
        nonlocal ticks, timelines
        ticks += sum(operation.get("wait", 0) for recipe_ops in recipes for operation in recipe_ops)
        timelines += 1

    def run_mixed(recipes: list[list[dict[str, Any]]], sample_indexes: set[int]) -> None:
        nonlocal ticks, timelines
        ticks += 200 * (len(recipes) - len(sample_indexes))
        ticks += sum(operation.get("wait", 0) for recipe_ops in recipes for operation in recipe_ops)
        timelines += 1

    def reset() -> None:
        run([{"wait": 200}, {"wait": 200}])

    if declaration.module == "register":
        reset()
        for value in range(16):
            run_mixed(
                [
                    resolve_recipe(declaration, f"load:{value}"),
                    [{"wait": 200}, {"wait": 200}],
                    [
                        {"control": reset_id, "level": True},
                        {"wait": 200},
                        {"control": reset_id, "level": False},
                        {"wait": 200},
                    ],
                ],
                {1, 2},
            )
    elif declaration.module == "arithmetic":
        reset()
        for a, value in ARITHMETIC_PAIRS:
            run_cycles(
                [
                    resolve_recipe(declaration, "load:9"),
                    resolve_recipe(declaration, "out"),
                    resolve_recipe(declaration, f"load:{a}"),
                    resolve_recipe(declaration, f"add:{value}"),
                ]
            )
    elif declaration.module == "storage":
        for words in STORAGE_PATTERNS:
            run(
                [
                    {"wait": 200},
                    *[
                        operation
                        for address, word in enumerate(words)
                        for operation in resolve_recipe(declaration, f"write:{address}:{word}")
                    ],
                    {"wait": 200},
                ]
            )
            reset()
            run_samples(
                [
                    [*resolve_recipe(declaration, f"address:{address}"), {"wait": 200}]
                    for address in range(8)
                ]
            )
    else:
        reset()
        for value in range(16):
            run_mixed(
                [
                    resolve_recipe(declaration, f"load:{value}"),
                    resolve_recipe(declaration, "out"),
                    [{"wait": 200}, {"wait": 200}],
                ],
                {2},
            )
            run_mixed(
                [
                    resolve_recipe(declaration, f"load:{15 - value}"),
                    resolve_recipe(declaration, "add:1"),
                    resolve_recipe(declaration, "out"),
                    [
                        {"control": reset_id, "level": True},
                        {"wait": 200},
                        {"control": reset_id, "level": False},
                        {"wait": 200},
                    ],
                ],
                {3},
            )
    return {
        "scheduled_ticks": ticks,
        "timeline_count": timelines,
        "nominal_seconds": ticks / 20,
    }


def required_recipes(module: str) -> set[str]:
    if module == "storage":
        return {f"address:{a}" for a in range(8)} | {
            f"write:{a}:{word}" for words in STORAGE_PATTERNS for a, word in enumerate(words)
        }
    keys = {f"load:{n}" for n in range(16)}
    if module in ("arithmetic", "output"):
        keys |= {f"add:{n}" for n in range(16)} | {"out"}
    return keys


def grade_module(
    actions: Actions,
    declaration: ModuleDeclaration,
    *,
    control: Any = None,
    fail_fast: bool = False,
) -> dict[str, Any]:
    """Run the frozen public cases; injection is for controlled reader tests only.

    The production path always uses trusted server snapshots. A failure is saved
    before continuing to the next case; transport/budget failures stay incomplete.
    No result from planner/Jev participates in the comparison.
    """
    declaration = validate_declaration(declaration.model_dump(), actions.contract)
    for key in sorted(required_recipes(declaration.module)):
        resolve_recipe(declaration, key)
    grader = (
        control
        if control is not None
        else GraderControl(
            actions, declaration, max_ticks=MODULE_TICKS, max_commands=MODULE_COMMANDS
        )
    )
    schedule = estimate_module_schedule(declaration)
    observed_overheads = [
        event["result"]["overhead_seconds"]
        for event in actions.manifest.data["events"]
        if event.get("kind") == "grader_operation"
        and event.get("request", {}).get("op") == "timeline"
        and isinstance(event.get("result"), dict)
        and isinstance(event["result"].get("overhead_seconds"), (int, float))
    ]
    overhead_per_timeline = max([FALLBACK_TIMELINE_OVERHEAD_SECONDS, *observed_overheads])
    forecast_tick_rate = SPRINT_FORECAST_TPS if isinstance(actions.transport, RconClient) else 20
    forecast_seconds = (
        int(schedule["scheduled_ticks"]) / forecast_tick_rate
        + int(schedule["timeline_count"]) * overhead_per_timeline
    )
    budgets = actions.contract.budgets
    elapsed_seconds = actions.clock() - actions.started
    remaining_seconds = max(0.0, budgets.wall_seconds - elapsed_seconds)
    final_program_reserve = 2 * budgets.program_wall_seconds
    available_ticks = max(
        0,
        getattr(grader, "max_ticks", MODULE_TICKS) - getattr(grader, "ticks", 0),
    )
    result: dict[str, Any] = {
        "module": declaration.module,
        "scope": "public_module_behavior",
        "behavioral_passed": False,
        "checks": [],
        "failed_checks": [],
        "declaration": declaration.model_dump(mode="json"),
        "schedule_forecast": {
            **schedule,
            "timeline_overhead_per_call_seconds": overhead_per_timeline,
            "forecast_tick_rate": forecast_tick_rate,
            "projected_module_seconds": forecast_seconds,
            "elapsed_trial_seconds": elapsed_seconds,
            "remaining_trial_seconds": remaining_seconds,
            "reserved_two_program_seconds": final_program_reserve,
            "remaining_ticks_before_module": available_ticks,
            "estimate_is_advisory": True,
        },
        "complete": False,
    }
    actions.manifest.data["checks"].append(result)
    actions.manifest.save()
    if int(schedule["scheduled_ticks"]) > available_ticks:
        result["failed_checks"].append(
            {
                "reason": "module_schedule_exceeds_remaining_aggregate_ticks",
                "required": schedule["scheduled_ticks"],
                "available": available_ticks,
                "incomplete": True,
            }
        )
        result["stopped_before_execution"] = True
        actions.manifest.save()
        return result
    if forecast_seconds + final_program_reserve > remaining_seconds:
        result["failed_checks"].append(
            {
                "reason": "module_forecast_would_consume_two_program_reserve",
                "projected_module_seconds": forecast_seconds,
                "reserved_two_program_seconds": final_program_reserve,
                "remaining_trial_seconds": remaining_seconds,
                "incomplete": True,
            }
        )
        result["stopped_before_execution"] = True
        actions.manifest.save()
        return result
    # Verify every declared probe exists before spending the behavioral suite's
    # timeline budget. Otherwise a bad coordinate can produce dozens of
    # misleading per-case failures before we discover the declaration error.
    if control is None:
        for role, probes in declaration.probes.items():
            for index, _probe in enumerate(probes):
                try:
                    grader.probe(role, index)
                except ValueError as error:
                    result["failed_checks"].append(
                        {
                            "reason": "declared_probe_missing_or_mismatched",
                            "role": role,
                            "index": index,
                            "detail": str(error),
                        }
                    )
                    result["complete"] = True
                    actions.manifest.save()
                    return result
    reset_id = next(c.id for c in declaration.controls if c.role == "reset")

    class FailedCheck(Exception):
        pass

    def run(recipe: list[dict[str, Any]], *, step: bool = False) -> dict[str, Any]:
        if control is None:
            return grader.timeline(
                recipe,
                cycles=1,
                sample_only=not step,
                fail_on_missing_probe=False,
            )
        return grader.timeline(recipe, cycles=1, sample_only=not step)

    def batch(recipes: list[list[dict[str, Any]]], *, sample_only: bool = False) -> dict[str, Any]:
        if control is None:
            return grader.timeline(
                [],
                cycles=len(recipes),
                sample_only=sample_only,
                cycle_recipes=recipes,
                fail_on_missing_probe=False,
            )
        # Injected readers model one world observation at a time; keep their
        # narrow interface while production uses a single batched timeline.
        snapshots = []
        for operations in recipes:
            one = grader.timeline(operations, cycles=1, sample_only=sample_only)
            snapshots.extend(one["snapshots"])
        return {"snapshots": snapshots}

    def mixed(recipes: list[list[dict[str, Any]]], sample_indexes: set[int]) -> dict[str, Any]:
        if control is None:
            return grader.timeline(
                [],
                cycles=len(recipes),
                cycle_recipes=recipes,
                sample_cycles=sorted(sample_indexes),
                fail_on_missing_probe=False,
            )
        snapshots = []
        for index, operations in enumerate(recipes):
            one = grader.timeline(operations, cycles=1, sample_only=index in sample_indexes)
            snapshots.extend(one["snapshots"])
        return {"snapshots": snapshots}

    def recipe(key: str) -> list[dict[str, Any]]:
        return resolve_recipe(declaration, key)

    def check(label: str, snapshot: dict[str, Any], expected: dict[str, int]) -> None:
        raw = snapshot["snapshots"][-1]["signals"]
        actual: dict[str, int | None] = {}
        for role in expected:
            values = raw.get(role)
            probes = declaration.probes[role]
            if (
                not isinstance(values, list)
                or len(values) != len(probes)
                or any(
                    type(v) is not int or not 0 <= v <= (15 if p.block.endswith("wire") else 1)
                    for v, p in zip(values, probes, strict=True)
                )
            ):
                actual[role] = None
            else:
                actual[role] = sum(
                    int(v > 0) << (len(values) - i - 1) for i, v in enumerate(values)
                )
        item = {
            "case": label,
            "expected": expected,
            "actual": actual,
            "failure_reason": (
                "missing_or_mismatched_probe"
                if any(value is None for value in actual.values())
                else "signal_mismatch"
            ),
            "raw": snapshot,
            "passed": actual == expected,
        }
        result["checks"].append(item)
        if not item["passed"]:
            result["failed_checks"].append(item)
        actions.manifest.save()
        if not item["passed"] and fail_fast:
            raise FailedCheck

    def check_cycles(
        labels: list[str], snapshot: dict[str, Any], expectations: list[dict[str, int]]
    ) -> None:
        settled = [item for item in snapshot["snapshots"] if item["phase"] == "settled"]
        if len(settled) != len(labels):
            raise RuntimeError("Batched timeline did not return one settled snapshot per cycle")
        for label, item, expected in zip(labels, settled, expectations, strict=True):
            check(label, {"snapshots": [item]}, expected)

    def reset() -> dict[str, Any]:
        return run(
            [
                {"control": reset_id, "level": True},
                {"wait": 200},
                {"control": reset_id, "level": False},
                {"wait": 200},
            ]
        )

    try:
        module = declaration.module
        if module == "register":
            for n in range(16):
                # The preceding value's checked reset is also the initial reset
                # for this value. Keep one reset before the first load, then
                # avoid spending another full 400-tick reset timeline.
                if n == 0:
                    check(f"{n}:initial_reset", reset(), {"a": 0})
                check_cycles(
                    [f"{n}:load", f"{n}:hold400", f"{n}:reset"],
                    mixed(
                        [
                            recipe(f"load:{n}"),
                            [{"wait": 200}, {"wait": 200}],
                            [
                                {"control": reset_id, "level": True},
                                {"wait": 200},
                                {"control": reset_id, "level": False},
                                {"wait": 200},
                            ],
                        ],
                        {1, 2},
                    ),
                    [{"a": n}, {"a": n}, {"a": 0}],
                )
        elif module == "arithmetic":
            for index, (a, n) in enumerate(ARITHMETIC_PAIRS):
                label = f"{a}+{n}"
                # LOAD 9 and OUT re-establish the sentinel for each pair. The
                # prior pair already left O at 9, so reset is needed only to
                # verify the initial state.
                if index == 0:
                    check(label + ":reset", reset(), {"a": 0, "o": 0})
                # A nonzero sentinel exposes implementations that always clear O.
                batched = batch(
                    [
                        recipe("load:9"),
                        recipe("out"),
                        recipe(f"load:{a}"),
                        recipe(f"add:{n}"),
                    ]
                )
                check_cycles(
                    [label + suffix for suffix in (":seed", ":out", ":load", ":add")],
                    batched,
                    [
                        {"a": 9, "o": 0 if index == 0 else 9},
                        {"a": 9, "o": 9},
                        {"a": a, "o": 9},
                        {"a": (a + n) % 16, "o": 9},
                    ],
                )
        elif module == "storage":
            for index, words in enumerate(STORAGE_PATTERNS):
                encoded = sum(word << (6 * (7 - a)) for a, word in enumerate(words))
                program_and_settle = [
                    {"control": reset_id, "level": True},
                    {"wait": 200},
                    *[
                        operation
                        for address, word in enumerate(words)
                        for operation in recipe(f"write:{address}:{word}")
                    ],
                    {"wait": 200},
                ]
                programmed = (
                    grader.timeline(
                        program_and_settle,
                        sample_only=True,
                        fail_on_missing_probe=False,
                    )
                    if control is None
                    else grader.timeline(program_and_settle, sample_only=True)
                )
                check(f"{index}:programmed", programmed, {"words": encoded})
                check(f"{index}:reset_preserves", reset(), {"words": encoded})
                address_batch = batch(
                    [[*recipe(f"address:{a}"), {"wait": 200}] for a in range(8)],
                    sample_only=True,
                )
                check_cycles(
                    [f"{index}:address:{a}" for a in range(8)],
                    address_batch,
                    [
                        {"words": encoded, "address": a, "readout": word}
                        for a, word in enumerate(words)
                    ],
                )
        else:
            for n in range(16):
                # The previous value's checked clear is the initial reset for
                # this one. Verify initial reset once, then preserve a clear
                # check after each value.
                if n == 0:
                    check(f"{n}:reset", reset(), {"o": 0, "strobe": 0})
                first_batch = mixed(
                    [recipe(f"load:{n}"), recipe("out"), [{"wait": 200}, {"wait": 200}]],
                    {2},
                )
                check_cycles(
                    [f"{n}:load", f"{n}:out", f"{n}:persist"],
                    first_batch,
                    [
                        {"o": 0, "strobe": 0},
                        {"o": n, "strobe": 1},
                        {"o": n, "strobe": 1},
                    ],
                )
                second_batch = mixed(
                    [
                        recipe(f"load:{15 - n}"),
                        recipe("add:1"),
                        recipe("out"),
                        [
                            {"control": reset_id, "level": True},
                            {"wait": 200},
                            {"control": reset_id, "level": False},
                            {"wait": 200},
                        ],
                    ],
                    {3},
                )
                check_cycles(
                    [
                        f"{n}:load_holds",
                        f"{n}:add_holds",
                        f"{n}:out_toggles",
                        f"{n}:clear",
                    ],
                    second_batch,
                    [
                        {"o": n, "strobe": 1},
                        {"o": n, "strobe": 1},
                        {"o": (16 - n) % 16, "strobe": 0},
                        {"o": 0, "strobe": 0},
                    ],
                )
        result["complete"] = True
        result["behavioral_passed"] = not result["failed_checks"]
    except FailedCheck:
        result["stopped_after_failure"] = True
    except ControlEvidenceMismatch as error:
        declared_control = next(
            item for item in declaration.controls if item.id == error.control_id
        )
        item = {
            "case": "control_interface",
            "expected": {
                "id": declared_control.id,
                "role": declared_control.role,
                "position": declared_control.position,
                "block": "minecraft:lever",
                "powered": False if declared_control.role == "step" else None,
            },
            "actual": {"powered": error.powered},
            "failure_reason": "control_evidence_mismatch",
            "detail": error.reason,
            "passed": False,
        }
        result["checks"].append(item)
        result["failed_checks"].append(item)
        result["stopped_after_failure"] = True
        actions.manifest.save()
    except Exception as error:
        result["failed_checks"].append({"reason": type(error).__name__, "incomplete": True})
        actions.manifest.save()
        raise
    finally:
        actions.manifest.save()
    return result


def run_behavior_negative_proof() -> Any:
    """A powered, non-register fixture must fail reset; never a supplied design."""
    from noob_agent.redstone.rcon import RconClient
    from noob_agent.redstone.trial import RUN_DIRECTORY, TrialManifest

    manifest = TrialManifest(RUN_DIRECTORY)
    manifest.data["kind"] = "public_register_negative_proof"
    probes = [[2, 64, 4], [5, 64, 4], [8, 64, 4], [11, 64, 4]]
    cells = probes + [[1, 64, 4], [0, 64, 2], [1, 64, 2]]
    installed: list[list[int]] = []
    try:
        with RconClient.dedicated() as transport:
            actions = Actions(manifest, transport, None)  # type: ignore[arg-type]
            declaration = validate_declaration(
                {
                    "module": "register",
                    "probes": {
                        "a": [{"position": p, "block": "minecraft:redstone_wire"} for p in probes]
                    },
                    "controls": [
                        {"id": "reset", "role": "reset", "position": [0, 64, 2]},
                        {"id": "step", "role": "step", "position": [1, 64, 2]},
                    ],
                    "recipes": {f"load:{n}": [] for n in range(16)},
                },
                actions.contract,
            )
            grader = GraderControl(
                actions, declaration, max_ticks=MODULE_TICKS, max_commands=MODULE_COMMANDS
            )
            try:
                for pos in cells:
                    if not grader._matches(pos, "minecraft:air") or not grader._matches(
                        [pos[0], 63, pos[2]], "minecraft:grass_block"
                    ):
                        raise ValueError("Proof requires empty supported cells")
                for pos in cells:
                    installed.append(pos)
                    block = (
                        "minecraft:redstone_wire"
                        if pos in probes
                        else (
                            "minecraft:redstone_block"
                            if pos == [1, 64, 4]
                            else "minecraft:lever[face=floor,facing=north,powered=false]"
                        )
                    )
                    grader._command(f"setblock {' '.join(map(str, pos))} {block}")
                grade_module(actions, declaration, fail_fast=True)
            finally:
                with RconClient.dedicated() as cleanup:
                    for pos in reversed(installed):
                        for command in (
                            f"setblock {' '.join(map(str, pos))} minecraft:air",
                            f"execute if block {' '.join(map(str, pos))} minecraft:air",
                        ):
                            event = manifest.attempt("proof_cleanup", {"command": command})
                            response = cleanup.command(command)
                            manifest.observed(event, {"response": response})
                            if command.startswith("execute") and response not in (
                                "Test passed",
                                "Test passed, count: 1",
                            ):
                                raise RuntimeError("Fixture removal unverified")
    except Exception as error:
        manifest.data["errors"].append(
            {"stage": "behavior_negative_proof", "type": type(error).__name__}
        )
    finally:
        manifest.finish_incomplete(
            [
                "Actual register reset failure; remaining cases not run",
                "No model-designed passing circuit or milestone acceptance",
            ]
        )
    return manifest
