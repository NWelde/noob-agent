"""Public module checks. Expectations stay in Python, outside world functions."""

from __future__ import annotations

from typing import Any

from noob_agent.redstone.actions import Actions
from noob_agent.redstone.grading import GraderControl
from noob_agent.redstone.modules import ModuleDeclaration, resolve_recipe, validate_declaration

# Two complementary patterns exercise both states of every physical storage bit.
STORAGE_PATTERNS = (
    (0, 63, 21, 42, 15, 48, 32, 16),
    (63, 0, 42, 21, 48, 15, 31, 47),
)
ARITHMETIC_PAIRS = ((0, 0), (0, 15), (15, 0), (15, 1), (14, 5), (7, 8))
MODULE_TICKS = 96000
MODULE_COMMANDS = 1000000


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
    result: dict[str, Any] = {
        "module": declaration.module,
        "scope": "public_module_behavior",
        "behavioral_passed": False,
        "checks": [],
        "failed_checks": [],
        "declaration": declaration.model_dump(mode="json"),
        "complete": False,
    }
    actions.manifest.data["checks"].append(result)
    actions.manifest.save()
    reset_id = next(c.id for c in declaration.controls if c.role == "reset")

    class FailedCheck(Exception):
        pass

    def run(recipe: list[dict[str, Any]], *, step: bool = False) -> dict[str, Any]:
        return grader.timeline(recipe, cycles=1, sample_only=not step)

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
            "raw": snapshot,
            "passed": actual == expected,
        }
        result["checks"].append(item)
        if not item["passed"]:
            result["failed_checks"].append(item)
        actions.manifest.save()
        if not item["passed"] and fail_fast:
            raise FailedCheck

    def reset() -> dict[str, Any]:
        return run(
            [
                {"control": reset_id, "level": True},
                {"wait": 200},
                {"control": reset_id, "level": False},
                {"wait": 200},
            ]
        )

    def hold() -> dict[str, Any]:
        return run([{"wait": 200}, {"wait": 200}])

    try:
        module = declaration.module
        if module == "register":
            for n in range(16):
                check(f"{n}:initial_reset", reset(), {"a": 0})
                check(f"{n}:load", run(recipe(f"load:{n}"), step=True), {"a": n})
                check(f"{n}:hold400", hold(), {"a": n})
                check(f"{n}:reset", reset(), {"a": 0})
        elif module == "arithmetic":
            for a, n in ARITHMETIC_PAIRS:
                label = f"{a}+{n}"
                check(label + ":reset", reset(), {"a": 0, "o": 0})
                # A nonzero sentinel exposes implementations that always clear O.
                check(label + ":seed", run(recipe("load:9"), step=True), {"a": 9, "o": 0})
                check(label + ":out", run(recipe("out"), step=True), {"a": 9, "o": 9})
                check(label + ":load", run(recipe(f"load:{a}"), step=True), {"a": a, "o": 9})
                check(
                    label + ":add", run(recipe(f"add:{n}"), step=True), {"a": (a + n) % 16, "o": 9}
                )
                check(label + ":hold", hold(), {"a": (a + n) % 16, "o": 9})
        elif module == "storage":
            for index, words in enumerate(STORAGE_PATTERNS):
                run([{"control": reset_id, "level": True}, {"wait": 200}])
                for a, word in enumerate(words):
                    run(recipe(f"write:{a}:{word}"))
                encoded = sum(word << (6 * (7 - a)) for a, word in enumerate(words))
                check(f"{index}:programmed", run([{"wait": 200}]), {"words": encoded})
                check(f"{index}:reset_preserves", reset(), {"words": encoded})
                for a, word in enumerate(words):
                    check(
                        f"{index}:address:{a}",
                        run([*recipe(f"address:{a}"), {"wait": 200}]),
                        {"words": encoded, "address": a, "readout": word},
                    )
        else:
            for n in range(16):
                check(f"{n}:reset", reset(), {"o": 0, "strobe": 0})
                check(f"{n}:load", run(recipe(f"load:{n}"), step=True), {"o": 0, "strobe": 0})
                check(f"{n}:out", run(recipe("out"), step=True), {"o": n, "strobe": 1})
                check(f"{n}:persist", hold(), {"o": n, "strobe": 1})
                check(
                    f"{n}:load_holds",
                    run(recipe(f"load:{15 - n}"), step=True),
                    {"o": n, "strobe": 1},
                )
                check(f"{n}:add_holds", run(recipe("add:1"), step=True), {"o": n, "strobe": 1})
                check(
                    f"{n}:out_toggles",
                    run(recipe("out"), step=True),
                    {"o": (16 - n) % 16, "strobe": 0},
                )
                check(f"{n}:clear", reset(), {"o": 0, "strobe": 0})
        result["complete"] = True
        result["behavioral_passed"] = not result["failed_checks"]
    except FailedCheck:
        result["stopped_after_failure"] = True
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
