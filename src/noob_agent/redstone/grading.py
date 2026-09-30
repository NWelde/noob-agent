"""Trusted harness-only controls. Never expose this object or RCON to planner/Jev.

Scoreboards contain timing/command evidence and observed timeline snapshots only.
They never calculate circuit results or hold expected arithmetic.
Client physics is not used. All public operations share Actions guards/budgets.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from noob_agent.redstone.actions import ActionLimit, Actions
from noob_agent.redstone.modules import Control, ModuleDeclaration, validate_declaration
from noob_agent.redstone.rcon import SERVER_DIRECTORY, RconClient
from noob_agent.redstone.trial import CommandTransport, TrialManifest


class ControlEvidenceMismatch(ValueError):
    """A trusted world read completed and disproved a declared control state."""

    def __init__(self, control_id: str, reason: str, powered: bool | None = None) -> None:
        super().__init__(reason)
        self.control_id = control_id
        self.reason = reason
        self.powered = powered


class GraderControl:
    def __init__(
        self,
        actions: Actions,
        declaration: ModuleDeclaration,
        *,
        max_ticks: int = 1600,
        max_commands: int = 4096,
        normal_speed: bool = False,
    ):
        if type(max_ticks) is not int or not 1 <= max_ticks <= 96000:
            raise ValueError("Invalid grading tick budget")
        if type(max_commands) is not int or not 1 <= max_commands <= 1000000:
            raise ValueError("Invalid grading command budget")
        if actions.manifest.data.get("timeline_resources", {}).get("state", "clean") != "clean":
            raise ValueError("Pending timeline resources require explicit recovery")
        self.actions = actions
        if type(normal_speed) is not bool:
            raise ValueError("Invalid normal-speed mode")
        self.normal_speed = normal_speed
        self.declaration = validate_declaration(declaration.model_dump(), actions.contract)
        prior = actions.manifest.data.get("grading_budget", {})
        if prior and (prior["tick_limit"] != max_ticks or prior["command_limit"] != max_commands):
            raise ValueError("Grading budget cannot change within a manifest")
        self.max_ticks = max_ticks
        self.max_commands = max_commands
        self.ticks = prior.get("scheduled_ticks", 0)
        self.operations = prior.get("operations", 0)
        self.commands = prior.get("commands", 0)
        self.namespace = "ng" + uuid4().hex[:12]
        self.objective = self.namespace
        self._save_budget()

    def _save_budget(self) -> None:
        budget = {
            "operations": self.operations,
            "commands": self.commands,
            "command_limit": self.max_commands,
            "stopped": self.actions.stopped,
            "scheduled_ticks": self.ticks,
            "tick_limit": self.max_ticks,
            "shared_action_limit": self.actions.maximum,
            "namespace": self.namespace,
            "normal_speed": self.normal_speed,
        }
        manifest = self.actions.manifest
        manifest.data["grading_budget"] = budget
        if not getattr(manifest, "journal_grading_budgets", False):
            manifest.save()
            return
        programs = manifest.data.get("grading_programs", {})
        event = manifest.attempt(
            "grading_budget_checkpoint",
            {"grading_budget": dict(budget), "grading_programs": json.loads(json.dumps(programs))},
        )
        manifest.observed(event, {"world_actions": 0})

    def _run(
        self, op: str, request: dict[str, Any], body: Callable[[], dict[str, Any]]
    ) -> dict[str, Any]:
        prior = self.actions.manifest.data["grading_budget"]
        self.ticks, self.commands = prior["scheduled_ticks"], prior["commands"]
        self.operations = prior["operations"]
        try:
            event = self.actions.charge("grader_operation", {"op": op, **request})
        except ActionLimit:
            event = self.actions.manifest.attempt("grader_rejected", {"op": op, **request})
            self.actions.manifest.observed(event, {"reason": "runtime_budget_or_guard"})
            self._save_budget()
            raise
        self.operations += 1
        self._save_budget()
        try:
            validate_declaration(self.declaration.model_dump(), self.actions.contract)
            result = body()
            self.actions.manifest.observed(event, result)
            self.actions.check()
            return result
        except BaseException as error:
            self.actions.manifest.data["errors"].append(
                {"stage": "grader_operation", "sequence": event, "type": type(error).__name__}
            )
            self._save_budget()
            raise

    def _command(self, command: str, *, reload_connection: bool = False) -> str:
        self.actions.check()
        if self.commands >= self.max_commands:
            self.actions.stopped = True
            self._save_budget()
            raise ActionLimit("Grading command budget exhausted")
        self.commands += 1
        self._save_budget()
        event = self.actions.manifest.attempt("grader_command", {"command": command})
        try:
            if reload_connection and isinstance(self.actions.transport, RconClient):
                # Reload can leave asynchronous protocol traffic on its connection.
                # Isolate it; never reuse/retry an uncertain mutation connection.
                with RconClient.dedicated() as transport:
                    response = transport.command(command)
            else:
                response = self.actions.transport.command(command)
        except BaseException:
            self.actions.stop_unknown()
            raise RuntimeError("Grader transport outcome unknown; stopped") from None
        self.actions.manifest.observed(event, {"response": response})
        self.actions.check()
        return response

    def _matches(self, position: list[int], state: str) -> bool:
        response = self._command(f"execute if block {' '.join(map(str, position))} {state}")
        if response in ("Test passed", "Test passed, count: 1"):
            return True
        if response == "Test failed":
            return False
        raise RuntimeError("Unrecognized server predicate response")

    def probe(self, role: str, index: int) -> dict[str, Any]:
        def read() -> dict[str, Any]:
            probes = self.declaration.probes.get(role, [])
            if type(index) is not int or not 0 <= index < len(probes):
                raise ValueError("Undeclared probe")
            probe = probes[index]
            field = "power" if probe.block == "minecraft:redstone_wire" else "lit"
            values = range(16) if field == "power" else (False, True)
            for value in values:
                if self._matches(probe.position, f"{probe.block}[{field}={str(value).lower()}]"):
                    return {
                        "position": probe.position,
                        "name": probe.block,
                        "properties": {field: value},
                    }
            raise ValueError("Declared probe missing or mismatched")

        return self._run("probe", {"role": role, "index": index}, read)

    def _control(self, control_id: str) -> tuple[Control, str, bool]:
        control = next((c for c in self.declaration.controls if c.id == control_id), None)
        if control is None:
            raise ValueError("Undeclared control")
        for face in ("floor", "wall", "ceiling"):
            for facing in ("north", "south", "east", "west"):
                geometry = f"face={face},facing={facing}"
                for powered in (False, True):
                    if self._matches(
                        control.position,
                        f"minecraft:lever[{geometry},powered={str(powered).lower()}]",
                    ):
                        return control, geometry, powered
        raise ControlEvidenceMismatch(control_id, "declared lever missing or mismatched")

    def set_control(self, control_id: str, powered: bool) -> dict[str, Any]:
        def apply() -> dict[str, Any]:
            control = next((c for c in self.declaration.controls if c.id == control_id), None)
            if control is None or control.role == "step" or type(powered) is not bool:
                raise ValueError("Only declared non-STEP controls accept levels")
            control, geometry, before = self._control(control_id)
            state = f"minecraft:lever[{geometry},powered={str(powered).lower()}]"
            if before != powered:
                pos = " ".join(map(str, control.position))
                self._command(
                    f"execute if block {pos} minecraft:lever[{geometry}] run setblock {pos} {state}"
                )
            if not self._matches(control.position, state):
                self.actions.stop_unknown()
                raise RuntimeError("Control effect unverified")
            return {"control": control_id, "powered": powered, "before": before}

        return self._run("set_control", {"control": control_id, "powered": powered}, apply)

    def _score(self, name: str) -> int:
        response = self._command(f"scoreboard players get {name} {self.objective}")
        match = re.fullmatch(rf"{name} has (-?\d+) \[{self.objective}\]", response)
        if not match:
            raise RuntimeError("Missing server timing evidence")
        return int(match[1])

    def pulse_step(self) -> dict[str, Any]:
        """Two-tick pulse through the journaled timeline and cleanup lifecycle."""
        result = self.timeline([], cycles=1, pulse_only=True)
        timestamps = result["timestamps"]
        effects = result["control_effects"]
        step_id = next(c.id for c in self.declaration.controls if c.role == "step")
        on = any(e["control"] == step_id and e["level"] for e in effects)
        off = any(e["control"] == step_id and not e["level"] for e in effects)
        return {
            "start": timestamps["s0"],
            "end": timestamps["s2"],
            "elapsed_server_ticks": timestamps["s2"] - timestamps["s0"],
            "on": int(on),
            "off": int(off),
        }

    def timeline(
        self,
        recipe: object,
        *,
        cycles: int = 1,
        sample_only: bool = False,
        program_id: str | None = None,
        pulse_only: bool = False,
        cycle_recipes: list[list[dict[str, Any]]] | None = None,
        sample_cycles: list[int] | None = None,
        fail_on_missing_probe: bool = True,
    ) -> dict[str, Any]:
        """One server timeline: bounded preparation and clock or sample cycles.

        Snapshots are raw observed wire powers/lamp levels, never expected results.
        Optional cycle_recipes supplies one bounded preparation recipe per cycle;
        ordinary recipe preparation still runs once before all cycles. Sample-only
        cycle recipes capture each settled checkpoint without issuing STEP.
        sample_cycles can mark selected cycle indexes as sample-only inside a
        clocked timeline; each index still needs its own bounded cycle recipe.
        Preparation and sample-only holds consume the shared aggregate allowance.
        Optional program IDs also retain eight-step/1600-tick/120-second deadlines
        across calls and instances; preparation is outside program execution ticks.
        """

        def run() -> dict[str, Any]:
            operation_started = time.monotonic()
            if (
                self.actions.manifest.data.get("timeline_resources", {}).get("state", "clean")
                != "clean"
            ):
                raise ValueError("Pending timeline resources require explicit recovery")
            if type(sample_only) is not bool:
                raise ValueError("Invalid sample mode")
            if type(pulse_only) is not bool:
                raise ValueError("Invalid pulse mode")
            if type(fail_on_missing_probe) is not bool:
                raise ValueError("Invalid missing-probe mode")
            if pulse_only and (sample_only or cycles != 1 or program_id is not None):
                raise ValueError("Pulse-only timelines require one non-program cycle")
            if sample_only and program_id is not None:
                raise ValueError("Program timelines require clock cycles")
            if type(cycles) is not int or not 1 <= cycles <= 8:
                raise ValueError("Cycles must be 1..8")
            if type(recipe) is not list or len(recipe) > 1027:
                raise ValueError("Recipe must contain at most 1027 operations")
            if cycle_recipes is not None:
                if (
                    type(cycle_recipes) is not list
                    or pulse_only
                    or len(cycle_recipes) != cycles
                    or any(type(item) is not list or len(item) > 129 for item in cycle_recipes)
                    or recipe
                ):
                    raise ValueError("Cycle recipes require one bounded recipe per cycle")
                recipes = cycle_recipes
            else:
                recipes = [recipe, *([[] for _ in range(cycles - 1)])]
            if sample_cycles is not None and (
                type(sample_cycles) is not list
                or sample_only
                or pulse_only
                or program_id is not None
                or cycle_recipes is None
                or any(type(index) is not int or not 0 <= index < cycles for index in sample_cycles)
                or len(set(sample_cycles)) != len(sample_cycles)
            ):
                raise ValueError("Invalid mixed sample cycles")
            sample_indexes = set(sample_cycles or [])
            clock_cycles = 0 if sample_only else cycles - len(sample_indexes)
            controls = {c.id: c for c in self.declaration.controls}
            duration = 2 if pulse_only else 200 * clock_cycles
            for cycle_recipe in recipes:
                for operation in cycle_recipe:
                    if type(operation) is not dict:
                        raise ValueError("Invalid recipe operation")
                    if set(operation) == {"wait"}:
                        wait = operation["wait"]
                        if type(wait) is not int or not 1 <= wait <= 200:
                            raise ValueError("Wait must be 1..200 server ticks")
                        duration += wait
                    elif set(operation) == {"control", "level"}:
                        control_id = operation["control"]
                        if (
                            type(control_id) is not str
                            or control_id not in controls
                            or controls[control_id].role == "step"
                            or type(operation["level"]) is not bool
                        ):
                            raise ValueError("Invalid declared level")
                    else:
                        raise ValueError("Invalid recipe operation")
            if self.ticks + duration > self.max_ticks:
                self.actions.stopped = True
                self._save_budget()
                raise ActionLimit("Grading tick budget exhausted")
            if program_id is not None:
                if not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", program_id):
                    raise ValueError("Invalid program ID")
                programs = self.actions.manifest.data.setdefault("grading_programs", {})
                budget = programs.setdefault(
                    program_id, {"steps": 0, "ticks": 0, "started": time.time()}
                )
                if (
                    budget["steps"] + cycles > 8
                    or budget["ticks"] + cycles * 200 > 1600
                    or time.time() - budget["started"] > 120
                ):
                    self.actions.stopped = True
                    self._save_budget()
                    raise ActionLimit("Program deadline exhausted")
                budget["steps"] += cycles
                budget["ticks"] += cycles * 200
            # Reserve the whole timeline before any server access; failed work
            # never returns allowance to this trial.
            self.ticks += duration
            self._save_budget()
            step_id = next(c.id for c in controls.values() if c.role == "step")
            used = {step_id} | {
                operation["control"]
                for cycle_recipe in recipes
                for operation in cycle_recipe
                if "control" in operation
            }
            geometry = {}
            for control_id in sorted(used):
                _, shape, powered = self._control(control_id)
                if control_id == step_id and powered:
                    raise ControlEvidenceMismatch(control_id, "STEP must begin low", powered)
                geometry[control_id] = shape
            frames: dict[int, list[str]] = {0: []}
            scores: list[str] = []
            snapshots: list[dict[str, Any]] = []
            control_effects: list[dict[str, Any]] = []
            offset = 0

            def frame(tick: int) -> list[str]:
                return frames.setdefault(tick, [])

            def level(tick: int, control_id: str, powered: bool) -> None:
                control = controls[control_id]
                pos = " ".join(map(str, control.position))
                state = f"minecraft:lever[{geometry[control_id]},powered={str(powered).lower()}]"
                name = f"x{len(scores)}"
                scores.append(name)
                control_effects.append({"control": control_id, "level": powered, "score": name})
                frame(tick).extend(
                    [
                        f"execute if block {pos} minecraft:lever[{geometry[control_id]}] "
                        f"run setblock {pos} {state}",
                        f"execute store success score {name} {self.objective} "
                        f"run execute if block {pos} {state}",
                    ]
                )

            def snapshot(tick: int, phase: str) -> None:
                names: dict[str, list[str]] = {}
                for role, probes in self.declaration.probes.items():
                    names[role] = []
                    for probe in probes:
                        name = f"b{len(scores)}"
                        scores.append(name)
                        names[role].append(name)
                        frame(tick).append(f"scoreboard players set {name} {self.objective} -1")
                        field = "power" if probe.block == "minecraft:redstone_wire" else "lit"
                        for value in range(16) if field == "power" else range(2):
                            state = str(value) if field == "power" else str(bool(value)).lower()
                            pos = " ".join(map(str, probe.position))
                            frame(tick).append(
                                f"execute if block {pos} {probe.block}[{field}={state}] "
                                f"run scoreboard players set {name} {self.objective} {value}"
                            )
                snapshots.append({"offset": tick, "phase": phase, "scores": names})

            if sample_only:
                if cycle_recipes is None:
                    for operation in recipe:
                        if "wait" in operation:
                            offset += operation["wait"]
                            frame(offset)
                        else:
                            level(offset, operation["control"], operation["level"])
                    snapshot(offset, "settled")
                else:
                    for cycle_recipe in recipes:
                        for operation in cycle_recipe:
                            if "wait" in operation:
                                offset += operation["wait"]
                                frame(offset)
                            else:
                                level(offset, operation["control"], operation["level"])
                        snapshot(offset, "settled")
            for cycle_index in range(0 if sample_only else cycles):
                for operation in recipes[cycle_index]:
                    if "wait" in operation:
                        offset += operation["wait"]
                        frame(offset)
                    else:
                        level(offset, operation["control"], operation["level"])
                if cycle_index in sample_indexes:
                    snapshot(offset, "settled")
                    continue
                level(offset, step_id, True)
                snapshot(offset + 2, "pulse_end")
                level(offset + 2, step_id, False)
                if pulse_only:
                    offset += 2
                else:
                    snapshot(offset + 200, "settled")
                    offset += 200
            functions: dict[str, str] = {}
            ticks = sorted(frames)
            for index, tick in enumerate(ticks):
                name = f"s{tick}"
                scores.append(name)
                lines = []
                if index == 0:
                    lines.append(
                        f"data modify storage {self.namespace}:timeline results set value {{}}"
                    )
                lines.append(
                    f"execute store result score {name} {self.objective} run time query gametime"
                )
                lines += frames[tick]
                if index + 1 < len(ticks):
                    following = ticks[index + 1]
                    lines.append(
                        f"schedule function {self.namespace}:t{following} "
                        f"{following - tick}t replace"
                    )
                else:
                    lines.append(f"scoreboard players set done {self.objective} 1")
                functions[f"t{tick}"] = "\n".join(lines) + "\n"
            terminal = f"t{ticks[-1]}"
            terminal_lines = functions[terminal].splitlines()
            terminal_lines[-1:-1] = [
                f"execute store result storage {self.namespace}:timeline results.{name} int 1 "
                f"run scoreboard players get {name} {self.objective}"
                for name in scores
            ]
            functions[terminal] = "\n".join(terminal_lines) + "\n"
            step_pos = " ".join(map(str, controls[step_id].position))
            functions["abort"] = (
                f"execute if block {step_pos} minecraft:lever[{geometry[step_id]}] "
                f"run setblock {step_pos} minecraft:lever[{geometry[step_id]},powered=false]\n"
            )
            values = self._execute_timeline(functions, duration, scores)
            if fail_on_missing_probe and any(
                values[name] < 0
                for item in snapshots
                for names in item["scores"].values()
                for name in names
            ):
                raise ValueError("Timeline probe missing or mismatched")
            if program_id is not None and time.time() - budget["started"] > 120:
                self.actions.stop_unknown()
                raise ActionLimit("Program wall deadline exhausted")
            elapsed = time.monotonic() - operation_started
            return {
                "duration": duration,
                "nominal_seconds": duration / 20,
                "elapsed_seconds": elapsed,
                "overhead_seconds": max(0.0, elapsed - duration / 20),
                "cycles": clock_cycles,
                "pulse_only": pulse_only,
                "control_effects": [
                    {
                        "control": effect["control"],
                        "level": effect["level"],
                        "verified": values[effect["score"]] == 1,
                    }
                    for effect in control_effects
                ],
                "timestamps": {key: val for key, val in values.items() if key.startswith("s")},
                "snapshots": [
                    {
                        "offset": s["offset"],
                        "phase": s["phase"],
                        "signals": {
                            role: [values[n] for n in names] for role, names in s["scores"].items()
                        },
                    }
                    for s in snapshots
                ],
            }

        return self._run(
            "timeline",
            {
                "recipe": recipe,
                "cycles": cycles,
                "sample_only": sample_only,
                "program_id": program_id,
                "pulse_only": pulse_only,
                "cycle_recipes": cycle_recipes,
                "sample_cycles": sample_cycles,
                "fail_on_missing_probe": fail_on_missing_probe,
            },
            run,
        )

    def _execute_timeline(
        self, functions: dict[str, str], duration: int, scores: list[str]
    ) -> dict[str, int]:
        # Reserve every generated command (including untaken predicates), rather
        # than counting only RCON calls. Poll/readback commands are charged too.
        generated = sum(len(body.splitlines()) for body in functions.values())
        if self.commands + generated > self.max_commands:
            self.actions.stopped = True
            self._save_budget()
            raise ActionLimit("Grading command budget exhausted")
        self.commands += generated
        self._save_budget()
        directory = SERVER_DIRECTORY / "redstone-trials/datapacks" / self.namespace
        target = directory / "data" / self.namespace / "function"
        evidence = self.actions.manifest.path.parent / self.namespace / str(self.operations)
        lifecycle = {
            "namespace": self.namespace,
            "functions": list(functions),
            "state": "pending",
            "cleanup_command_limit": len(functions)
            + 4
            + int(isinstance(self.actions.transport, RconClient)),
            "storage": f"{self.namespace}:timeline",
            "sprint_state": "not_started",
        }
        self.actions.manifest.data["timeline_resources"] = lifecycle
        self.actions.manifest.save()
        try:
            target.mkdir(parents=True, exist_ok=True)
            evidence.mkdir(parents=True, exist_ok=False)
            (directory / "pack.mcmeta").write_text(
                json.dumps({"pack": {"pack_format": 48, "description": "Observed server timeline"}})
            )
            for name, body in functions.items():
                (target / f"{name}.mcfunction").write_text(body)
                (evidence / f"{name}.mcfunction").write_text(body)
            self._command("reload", reload_connection=True)
            self._command(f"scoreboard objectives add {self.objective} dummy")
            self._command(f"scoreboard players set done {self.objective} 0")
            self._command(f"function {self.namespace}:t0")
            if (
                isinstance(self.actions.transport, RconClient)
                and duration > 0
                and not self.normal_speed
            ):
                # Sprint executes the same game ticks and scheduled functions as
                # normal play. Record intent before delivery so recovery stops
                # a sprint whose command outcome became uncertain.
                lifecycle["sprint_state"] = "starting"
                self.actions.manifest.save()
                response = self._command(f"tick sprint {duration}t")
                if response != "The game is sprinting":
                    raise RuntimeError("Server did not confirm timeline sprint")
                lifecycle["sprint_state"] = "started"
                self.actions.manifest.save()
            deadline = time.monotonic() + duration / 20 + 15
            while self._score("done") != 1:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Server timeline deadline exceeded")
                time.sleep(0.1)
            if isinstance(self.actions.transport, RconClient):
                response = self._command(f"data get storage {self.namespace}:timeline results")
                values: dict[str, int] = {}
                for name, value in re.findall(
                    r"(?<![A-Za-z0-9_])([bxs]\d+):\s*(-?\d+)(?:[bslfd])?(?![A-Za-z0-9_])",
                    response,
                ):
                    if name in values and values[name] != int(value):
                        raise RuntimeError("Conflicting timeline score readback")
                    values[name] = int(value)
                if set(values) != set(scores):
                    raise RuntimeError("Incomplete timeline score readback")
            else:
                values = {name: self._score(name) for name in scores}
            origin = values["s0"]
            for name, value in values.items():
                if name.startswith("s") and value != origin + int(name[1:]):
                    raise RuntimeError("Server timeline interval mismatch")
                if name.startswith("x") and value != 1:
                    raise RuntimeError("Timeline control effect unverified")
            return values
        except BaseException:
            self.actions.stop_unknown()
            raise
        finally:
            cleanup_timeline(self.actions.manifest, self.actions.transport)

    def wait_ticks(self, ticks: int) -> dict[str, Any]:
        """Compatibility adapter using journaled sample-only timelines."""
        return self.timeline([{"wait": ticks}], sample_only=True)


def run_control_proof() -> Any:
    """Temporary dedicated-world fixtures; infrastructure evidence, never a circuit grade."""
    from noob_agent.redstone.rcon import RconClient
    from noob_agent.redstone.trial import RUN_DIRECTORY, TrialManifest

    manifest = TrialManifest(RUN_DIRECTORY)
    manifest.data["kind"] = "trusted_grader_control_proof"
    value: dict[str, Any] = {
        "module": "register",
        "probes": {
            "a": [
                {"position": p, "block": "minecraft:redstone_wire"}
                for p in ([1, 64, 0], [2, 64, 0], [94, 64, 95], [93, 64, 95])
            ]
        },
        "controls": [
            {"id": "reset", "role": "reset", "position": [0, 64, 0]},
            {"id": "step", "role": "step", "position": [95, 64, 95]},
            {"id": "far_input", "role": "test_input", "position": [92, 64, 95]},
        ],
    }
    installed: list[list[int]] = []
    try:
        with RconClient.dedicated() as transport:
            # Reader intentionally absent: every grader read is a server predicate.
            actions = Actions(manifest, transport, None, max_actions=100)  # type: ignore[arg-type]
            grader = GraderControl(
                actions, validate_declaration(value, actions.contract), max_ticks=210
            )
            manifest.data["module_declaration"] = value
            manifest.save()
            positions = [p["position"] for p in value["probes"]["a"]]
            positions += [c["position"] for c in value["controls"]]
            try:
                for position in positions:
                    if not grader._matches(position, "minecraft:air"):
                        raise ValueError("Proof requires empty fixture cells")
                    below = [position[0], 63, position[2]]
                    if not grader._matches(below, "minecraft:grass_block"):
                        raise ValueError("Proof requires existing ground support")
                for position in positions:
                    block = (
                        "minecraft:lever[face=floor,facing=north,powered=false]"
                        if position in ([0, 64, 0], [95, 64, 95], [92, 64, 95])
                        else "minecraft:redstone_wire"
                    )
                    installed.append(position)
                    grader._command(f"setblock {' '.join(map(str, position))} {block}")
                checks: dict[str, Any] = {}
                checks["initial"] = [grader.probe("a", i) for i in range(4)]
                checks["reset_on"] = grader.set_control("reset", True)
                grader.wait_ticks(4)
                checks["near_power"] = grader.probe("a", 0)
                checks["reset_off"] = grader.set_control("reset", False)
                checks["far_on"] = grader.set_control("far_input", True)
                grader.wait_ticks(4)
                checks["far_power"] = grader.probe("a", 3)
                checks["far_off"] = grader.set_control("far_input", False)
                checks["step"] = grader.pulse_step()
                checks["final"] = [grader.probe("a", i) for i in range(4)]
                grader._command("setblock 92 64 95 minecraft:air")
                checks["rejections"] = []
                failures: tuple[tuple[str, Callable[[], Any]], ...] = (
                    ("undeclared", lambda: grader.set_control("unknown", True)),
                    ("step_level", lambda: grader.set_control("step", True)),
                    (
                        "oversize_wait",
                        lambda: grader.wait_ticks(201),
                    ),
                    ("missing_distant_lever", lambda: grader.set_control("far_input", True)),
                )
                for label, call in failures:
                    try:
                        call()
                    except ValueError:
                        checks["rejections"].append(label)
                assert checks["near_power"]["properties"]["power"] == 15
                assert all(p["properties"]["power"] == 0 for p in checks["final"])
                assert checks["far_power"]["properties"]["power"] == 15
                assert len(checks["rejections"]) == 4
                # Exhaust the already-frozen allowance with bounded server waits.
                while grader.ticks < grader.max_ticks:
                    grader.wait_ticks(min(200, grader.max_ticks - grader.ticks))
                try:
                    grader.wait_ticks(1)
                except ActionLimit:
                    checks["tick_budget_stopped"] = actions.stopped
                assert checks["tick_budget_stopped"] is True
                manifest.data["checks"].append(checks)
                manifest.save()
            finally:
                # Cleanup is trusted, separately journaled, even after a runtime stop.
                if installed:
                    with RconClient.dedicated() as cleanup:
                        for position in reversed(installed):
                            pos = " ".join(map(str, position))
                            for command in (
                                f"setblock {pos} minecraft:air",
                                f"execute if block {pos} minecraft:air",
                            ):
                                event = manifest.attempt("proof_cleanup", {"command": command})
                                manifest.observed(event, {"response": cleanup.command(command)})
    except Exception as error:
        manifest.data["errors"].append({"stage": "control_proof", "type": type(error).__name__})
    finally:
        manifest.finish_incomplete(
            [
                "Harness control proof only; no module behavioral grade or model success",
                "Programming recipes, four graders and live model-designed modules remain pending",
            ]
        )
    return manifest


def cleanup_timeline(manifest: TrialManifest, transport: CommandTransport) -> None:
    """Idempotent bounded recovery, permitted even after the trial runtime stops.

    Recovery first stops a possibly active sprint, then cancels schedules and
    removes this timeline's objective, score storage and pack. It does not
    resume grading or replenish any budget. The retained abort function returns
    the declared STEP low before its pack is removed.
    """
    resource = manifest.data.get("timeline_resources")
    if not resource or resource["state"] == "clean":
        return
    namespace, functions = resource["namespace"], resource["functions"]
    storage = resource.get("storage")
    if not re.fullmatch(r"ng[0-9a-f]{12}", namespace) or (
        type(functions) is not list
        or len(functions) > 153
        or any(
            type(n) is not str or not re.fullmatch(r"(?:t[0-9]{1,5}|abort)", n) for n in functions
        )
    ):
        raise ValueError("Invalid timeline recovery resource")
    if storage is not None and storage != f"{namespace}:timeline":
        raise ValueError("Invalid timeline storage recovery resource")
    sprint_state = resource.get("sprint_state", "not_started")
    if sprint_state not in ("not_started", "starting", "started"):
        raise ValueError("Invalid timeline sprint recovery resource")

    def command(client: CommandTransport, text: str) -> None:
        event = manifest.attempt("timeline_cleanup", {"command": text})
        manifest.observed(event, {"response": client.command(text)})

    resource["state"] = "cleanup_pending"
    manifest.save()
    try:
        # New connection for recovery from a possibly broken runtime connection.
        if isinstance(transport, RconClient):
            with RconClient.dedicated() as client:
                if sprint_state != "not_started":
                    command(client, "tick sprint stop")
                for name in functions:
                    command(client, f"schedule clear {namespace}:{name}")
                command(client, f"function {namespace}:abort")
                command(client, f"scoreboard objectives remove {namespace}")
                if storage:
                    command(client, f"data remove storage {storage} results")
        else:
            if sprint_state != "not_started":
                command(transport, "tick sprint stop")
            for name in functions:
                command(transport, f"schedule clear {namespace}:{name}")
            command(transport, f"function {namespace}:abort")
            command(transport, f"scoreboard objectives remove {namespace}")
            if storage:
                command(transport, f"data remove storage {storage} results")
        directory = SERVER_DIRECTORY / "redstone-trials/datapacks" / namespace
        if directory.exists():
            shutil.rmtree(directory)
        if isinstance(transport, RconClient):
            with RconClient.dedicated() as client:
                command(client, "reload")
        else:
            command(transport, "reload")
        resource["state"] = "clean"
        manifest.save()
    except BaseException:
        resource["state"] = "recovery_required"
        manifest.save()
        raise


def recover_timeline(path: Path) -> TrialManifest:
    """Explicit recovery of a stopped run; never call concurrently with its writer."""
    manifest = object.__new__(TrialManifest)
    manifest.path = path
    manifest.journal_path = path.with_name("events.jsonl")
    manifest._journal_records = TrialManifest._count_journal_records(manifest.journal_path)
    manifest.data = TrialManifest.load_data(path)
    manifest.started = time.monotonic()
    with RconClient.dedicated() as transport:
        cleanup_timeline(manifest, transport)
    return manifest


def run_timeline_proof() -> TrialManifest:
    """Storage-scale probe and lever fixture, not a module or model success."""
    from noob_agent.redstone.trial import RUN_DIRECTORY

    manifest = TrialManifest(RUN_DIRECTORY)
    manifest.data["kind"] = "server_timeline_proof"
    positions = [[94, 64, 95], [93, 64, 95], [1, 64, 0], [2, 64, 0]]
    isolated_positions = [
        [x, 64, z]
        for x in range(10, 35, 3)
        for z in range(10, 29, 3)
        if [x, 64, z] not in positions
    ][:53]
    positions.extend(isolated_positions)
    value = {
        "module": "storage",
        "probes": {
            "words": [{"position": p, "block": "minecraft:redstone_wire"} for p in positions[:48]],
            "readout": [
                {"position": p, "block": "minecraft:redstone_wire"} for p in positions[48:54]
            ],
            "address": [
                {"position": p, "block": "minecraft:redstone_wire"} for p in positions[54:57]
            ],
        },
        "controls": [
            {"id": "step", "role": "step", "position": [95, 64, 95]},
            {"id": "reset", "role": "reset", "position": [0, 64, 0]},
            {"id": "program", "role": "programming", "position": [0, 64, 2]},
        ],
    }
    cells = positions + [[95, 64, 95], [0, 64, 0], [0, 64, 2]]
    installed = []
    try:
        with RconClient.dedicated() as transport:
            actions = Actions(manifest, transport, None, max_actions=100)  # type: ignore[arg-type]
            grader = GraderControl(
                actions,
                validate_declaration(value, actions.contract),
                max_ticks=4008,
                max_commands=30000,
            )
            manifest.data["module_declaration"] = value
            manifest.save()
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
                        if pos in positions
                        else ("minecraft:lever[face=floor,facing=north,powered=false]")
                    )
                    grader._command(f"setblock {' '.join(map(str, pos))} {block}")
                result = grader.timeline(
                    [],
                    cycles=2,
                    cycle_recipes=[
                        [
                            {"control": "reset", "level": True},
                            {"wait": 4},
                            {"control": "reset", "level": False},
                        ],
                        [
                            {"control": "reset", "level": False},
                            {"wait": 4},
                        ],
                    ],
                )
                manifest.data["checks"].append(result)
                manifest.save()
                # Independent Python expectations; never embedded in functions.
                for snapshot in result["snapshots"]:
                    words = [15, 14, 0, 0] if snapshot["phase"] == "pulse_end" else [0] * 4
                    words.extend([0] * (48 - 4))
                    expected = {
                        "words": words,
                        "readout": [0] * 6,
                        "address": [0] * 3,
                    }
                    observed = snapshot["signals"]
                    check = {
                        "phase": snapshot["phase"],
                        "observed": observed,
                        "expected": expected,
                        "passed": observed == expected,
                    }
                    manifest.data["checks"].append(check)
                    assert check["passed"]
                hold_reset_batch = grader.timeline(
                    [],
                    cycles=2,
                    sample_only=True,
                    cycle_recipes=[
                        [{"wait": 200}, {"wait": 200}],
                        [
                            {"control": "reset", "level": True},
                            {"wait": 200},
                            {"control": "reset", "level": False},
                            {"wait": 200},
                        ],
                    ],
                )
                expected_sample = {
                    "words": [0] * 48,
                    "readout": [0] * 6,
                    "address": [0] * 3,
                }
                hold_reset_check = {
                    "fixture": "hold_and_reset_two_snapshots",
                    "offsets": [item["offset"] for item in hold_reset_batch["snapshots"]],
                    "signals_passed": all(
                        item["signals"] == expected_sample for item in hold_reset_batch["snapshots"]
                    ),
                    "control_effects_verified": all(
                        item["verified"] for item in hold_reset_batch["control_effects"]
                    ),
                }
                manifest.data["checks"].append(hold_reset_check)
                assert hold_reset_check["offsets"] == [400, 800]
                assert hold_reset_check["signals_passed"]
                assert hold_reset_check["control_effects_verified"]
                mixed_batch = grader.timeline(
                    [],
                    cycles=3,
                    cycle_recipes=[
                        [],
                        [{"wait": 200}, {"wait": 200}],
                        [
                            {"control": "reset", "level": True},
                            {"wait": 200},
                            {"control": "reset", "level": False},
                            {"wait": 200},
                        ],
                    ],
                    sample_cycles=[1, 2],
                )
                mixed_check = {
                    "fixture": "mixed_step_hold_reset",
                    "settled_offsets": [
                        item["offset"]
                        for item in mixed_batch["snapshots"]
                        if item["phase"] == "settled"
                    ],
                    "pulse_offsets": [
                        item["offset"]
                        for item in mixed_batch["snapshots"]
                        if item["phase"] == "pulse_end"
                    ],
                    "settled_signals_passed": all(
                        item["signals"] == expected_sample
                        for item in mixed_batch["snapshots"]
                        if item["phase"] == "settled"
                    ),
                    "control_effects_verified": all(
                        item["verified"] for item in mixed_batch["control_effects"]
                    ),
                }
                manifest.data["checks"].append(mixed_check)
                assert mixed_check["settled_offsets"] == [200, 600, 1000]
                assert mixed_check["pulse_offsets"] == [2]
                assert mixed_check["settled_signals_passed"]
                assert mixed_check["control_effects_verified"]
                sample_batch = grader.timeline(
                    [],
                    cycles=8,
                    sample_only=True,
                    cycle_recipes=[
                        [
                            {"control": "program", "level": bool(address & 1)},
                            {"wait": 200},
                        ]
                        for address in range(8)
                    ],
                )
                sample_checks = [
                    snapshot["signals"] == expected_sample for snapshot in sample_batch["snapshots"]
                ]
                manifest.data["checks"].append(
                    {
                        "sample_cycle_count": len(sample_batch["snapshots"]),
                        "sample_cycle_offsets": [
                            snapshot["offset"] for snapshot in sample_batch["snapshots"]
                        ],
                        "sample_cycles_passed": len(sample_checks) == 8 and all(sample_checks),
                    }
                )
                assert len(sample_checks) == 8 and all(sample_checks)
                # Deliberately incorrect expectation retained as actual failure evidence.
                manifest.data["checks"].append(
                    {
                        "fixture": "deliberate_wrong_expectation",
                        "expected": {"words": [1] * 48, "readout": [1] * 6, "address": [1] * 3},
                        "observed": result["snapshots"][-1]["signals"],
                        "passed": False,
                    }
                )
                grader._command("setblock 94 64 95 minecraft:air")
                try:
                    grader.timeline([], cycles=1)
                except ValueError:
                    manifest.data["checks"].append({"missing_probe_rejected": True})
                try:
                    grader.timeline([], cycles=1)
                except ActionLimit:
                    manifest.data["checks"].append({"aggregate_budget_stopped": actions.stopped})
            finally:
                with RconClient.dedicated() as cleanup:
                    for pos in reversed(installed):
                        for command in (
                            f"setblock {' '.join(map(str, pos))} minecraft:air",
                            f"execute if block {' '.join(map(str, pos))} minecraft:air",
                        ):
                            event = manifest.attempt("proof_cleanup", {"command": command})
                            manifest.observed(event, {"response": cleanup.command(command)})
    except Exception as error:
        manifest.data["errors"].append({"stage": "timeline_proof", "type": type(error).__name__})
    finally:
        manifest.finish_incomplete(
            [
                "Timeline fixture only; four behavioral graders and "
                "model-built circuit success pending"
            ]
        )
    return manifest
