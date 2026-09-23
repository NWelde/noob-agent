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

TEMPLATES = Path("scenarios/minecraft/redstone-grader")


class GraderControl:
    def __init__(
        self,
        actions: Actions,
        declaration: ModuleDeclaration,
        *,
        max_ticks: int = 1600,
        max_commands: int = 4096,
    ):
        if type(max_ticks) is not int or not 1 <= max_ticks <= 96000:
            raise ValueError("Invalid grading tick budget")
        if type(max_commands) is not int or not 1 <= max_commands <= 1000000:
            raise ValueError("Invalid grading command budget")
        if actions.manifest.data.get("timeline_resources", {}).get("state", "clean") != "clean":
            raise ValueError("Pending timeline resources require explicit recovery")
        self.actions = actions
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
        self.actions.manifest.data["grading_budget"] = {
            "operations": self.operations,
            "commands": self.commands,
            "command_limit": self.max_commands,
            "stopped": self.actions.stopped,
            "scheduled_ticks": self.ticks,
            "tick_limit": self.max_ticks,
            "shared_action_limit": self.actions.maximum,
            "namespace": self.namespace,
        }
        self.actions.manifest.save()

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
        raise ValueError("Declared control is not a lever")

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

    def _reserve_ticks(self, ticks: int) -> None:
        if type(ticks) is not int or not 1 <= ticks <= 200:
            raise ValueError("Wait must be 1..200 server ticks")
        if self.ticks + ticks > self.max_ticks:
            self.actions.stopped = True
            self._save_budget()
            raise ActionLimit("Grading tick budget exhausted")
        self.ticks += ticks
        self._save_budget()

    def _schedule(self, start: str, finish: str, ticks: int) -> dict[str, Any]:
        # Unique pack avoids replacing any existing pack or pending schedule.
        directory = SERVER_DIRECTORY / "redstone-trials/datapacks" / self.namespace
        functions = directory / "data" / self.namespace / "function"
        functions.mkdir(parents=True, exist_ok=True)
        (directory / "pack.mcmeta").write_text(
            json.dumps({"pack": {"pack_format": 48, "description": "Harness timing only"}})
        )
        (functions / "start.mcfunction").write_text(start)
        (functions / "finish.mcfunction").write_text(finish)
        evidence = self.actions.manifest.path.parent / self.namespace / str(self.operations)
        evidence.mkdir(parents=True, exist_ok=False)
        (evidence / "start.mcfunction").write_text(start)
        (evidence / "finish.mcfunction").write_text(finish)
        self._command("reload", reload_connection=True)
        self._command(f"scoreboard objectives add {self.objective} dummy")
        self._command(f"function {self.namespace}:start")
        deadline = time.monotonic() + 20
        try:
            while self._score("done") != 1:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Server schedule deadline exceeded")
                time.sleep(0.05)  # Polling only; never the stimulus clock.
            result = {name: self._score(name) for name in ("start", "end")}
            result["elapsed_server_ticks"] = result["end"] - result["start"]
            if result["elapsed_server_ticks"] != ticks:
                raise RuntimeError("Server tick interval mismatch")
            return result
        except BaseException:
            self.actions.stop_unknown()
            raise

    def _score(self, name: str) -> int:
        response = self._command(f"scoreboard players get {name} {self.objective}")
        match = re.fullmatch(rf"{name} has (-?\d+) \[{self.objective}\]", response)
        if not match:
            raise RuntimeError("Missing server timing evidence")
        return int(match[1])

    def pulse_step(self) -> dict[str, Any]:
        def pulse() -> dict[str, Any]:
            control_id = next(c.id for c in self.declaration.controls if c.role == "step")
            control, geometry, powered = self._control(control_id)
            if powered:
                raise ValueError("STEP must begin low")
            self._reserve_ticks(2)
            fields = {
                "position": " ".join(map(str, control.position)),
                "geometry": geometry,
                "namespace": self.namespace,
                "objective": self.objective,
            }
            result = self._schedule(
                (TEMPLATES / "start.mcfunction.template").read_text().format(**fields),
                (TEMPLATES / "finish.mcfunction.template").read_text().format(**fields),
                2,
            )
            result.update(on=self._score("on"), off=self._score("off"))
            if (
                result["on"] != 1
                or result["off"] != 1
                or not self._matches(control.position, f"minecraft:lever[{geometry},powered=false]")
            ):
                self.actions.stop_unknown()
                raise RuntimeError("STEP effect unverified")
            return result

        return self._run("pulse_step", {}, pulse)

    def timeline(
        self,
        recipe: object,
        *,
        cycles: int = 1,
        sample_only: bool = False,
        program_id: str | None = None,
    ) -> dict[str, Any]:
        """One server timeline: declarative preparation then 1..8 exact cycles.

        Snapshots are raw observed wire powers/lamp levels, never expected results.
        Preparation and sample-only holds consume the shared aggregate allowance.
        Optional program IDs also retain eight-step/1600-tick/120-second deadlines
        across calls and instances; preparation is outside program execution ticks.
        """

        def run() -> dict[str, Any]:
            if (
                self.actions.manifest.data.get("timeline_resources", {}).get("state", "clean")
                != "clean"
            ):
                raise ValueError("Pending timeline resources require explicit recovery")
            if type(sample_only) is not bool:
                raise ValueError("Invalid sample mode")
            if sample_only and program_id is not None:
                raise ValueError("Program timelines require clock cycles")
            if type(cycles) is not int or not 1 <= cycles <= 8:
                raise ValueError("Cycles must be 1..8")
            if type(recipe) is not list or len(recipe) > 128:
                raise ValueError("Recipe must contain at most 128 operations")
            controls = {c.id: c for c in self.declaration.controls}
            duration = 0 if sample_only else 200 * cycles
            for operation in recipe:
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
            used = {step_id} | {o["control"] for o in recipe if "control" in o}
            geometry = {}
            for control_id in sorted(used):
                _, shape, powered = self._control(control_id)
                if control_id == step_id and powered:
                    raise ValueError("STEP must begin low")
                geometry[control_id] = shape
            frames: dict[int, list[str]] = {0: []}
            scores: list[str] = []
            snapshots: list[dict[str, Any]] = []
            offset = 0

            def frame(tick: int) -> list[str]:
                return frames.setdefault(tick, [])

            def level(tick: int, control_id: str, powered: bool) -> None:
                control = controls[control_id]
                pos = " ".join(map(str, control.position))
                state = f"minecraft:lever[{geometry[control_id]},powered={str(powered).lower()}]"
                name = f"x{len(scores)}"
                scores.append(name)
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

            for operation in recipe:
                if "wait" in operation:
                    offset += operation["wait"]
                    frame(offset)
                else:
                    level(offset, operation["control"], operation["level"])
            if sample_only:
                snapshot(offset, "settled")
            for _ in range(0 if sample_only else cycles):
                level(offset, step_id, True)
                snapshot(offset + 2, "pulse_end")
                level(offset + 2, step_id, False)
                snapshot(offset + 200, "settled")
                offset += 200
            functions: dict[str, str] = {}
            ticks = sorted(frames)
            for index, tick in enumerate(ticks):
                name = f"s{tick}"
                scores.append(name)
                lines = [
                    f"execute store result score {name} {self.objective} run time query gametime"
                ]
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
            step_pos = " ".join(map(str, controls[step_id].position))
            functions["abort"] = (
                f"execute if block {step_pos} minecraft:lever[{geometry[step_id]}] "
                f"run setblock {step_pos} minecraft:lever[{geometry[step_id]},powered=false]\n"
            )
            values = self._execute_timeline(functions, duration, scores)
            if program_id is not None and time.time() - budget["started"] > 120:
                self.actions.stop_unknown()
                raise ActionLimit("Program wall deadline exhausted")
            return {
                "duration": duration,
                "cycles": 0 if sample_only else cycles,
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
            "cleanup_command_limit": len(functions) + 3,
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
            deadline = time.monotonic() + duration / 20 + 15
            while self._score("done") != 1:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Server timeline deadline exceeded")
                time.sleep(0.1)
            values = {name: self._score(name) for name in scores}
            origin = values["s0"]
            for name, value in values.items():
                if name.startswith("s") and value != origin + int(name[1:]):
                    raise RuntimeError("Server timeline interval mismatch")
                if name.startswith("x") and value != 1:
                    raise RuntimeError("Timeline control effect unverified")
                if name.startswith("b") and value < 0:
                    raise ValueError("Timeline probe missing or mismatched")
            return values
        except BaseException:
            self.actions.stop_unknown()
            raise
        finally:
            cleanup_timeline(self.actions.manifest, self.actions.transport)

    def wait_ticks(self, ticks: int) -> dict[str, Any]:
        def wait() -> dict[str, Any]:
            self._reserve_ticks(ticks)
            return self._schedule(
                f"scoreboard players set done {self.objective} 0\n"
                f"execute store result score start {self.objective} run time query gametime\n"
                f"schedule function {self.namespace}:finish {ticks}t replace\n",
                f"execute store result score end {self.objective} run time query gametime\n"
                f"scoreboard players set done {self.objective} 1\n",
                ticks,
            )

        return self._run("wait_ticks", {"ticks": ticks}, wait)


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
                checks["settle"] = grader.wait_ticks(198)
                checks["final"] = [grader.probe("a", i) for i in range(4)]
                grader._command("setblock 92 64 95 minecraft:air")
                checks["rejections"] = []
                failures: tuple[tuple[str, Callable[[], Any]], ...] = (
                    ("undeclared", lambda: grader.set_control("unknown", True)),
                    ("step_level", lambda: grader.set_control("step", True)),
                    ("oversize_wait", lambda: grader.wait_ticks(201)),
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

    Recovery only cancels schedules and removes this timeline's objective/pack.
    It does not resume grading or replenish any budget. The retained abort
    function returns the declared STEP low before its pack is removed.
    """
    resource = manifest.data.get("timeline_resources")
    if not resource or resource["state"] == "clean":
        return
    namespace, functions = resource["namespace"], resource["functions"]
    if not re.fullmatch(r"ng[0-9a-f]{12}", namespace) or (
        type(functions) is not list
        or len(functions) > 153
        or any(
            type(n) is not str or not re.fullmatch(r"(?:t[0-9]{1,5}|abort)", n) for n in functions
        )
    ):
        raise ValueError("Invalid timeline recovery resource")

    def command(client: CommandTransport, text: str) -> None:
        event = manifest.attempt("timeline_cleanup", {"command": text})
        manifest.observed(event, {"response": client.command(text)})

    resource["state"] = "cleanup_pending"
    manifest.save()
    try:
        # New connection for recovery from a possibly broken runtime connection.
        if isinstance(transport, RconClient):
            with RconClient.dedicated() as client:
                for name in functions:
                    command(client, f"schedule clear {namespace}:{name}")
                command(client, f"function {namespace}:abort")
                command(client, f"scoreboard objectives remove {namespace}")
        else:
            for name in functions:
                command(transport, f"schedule clear {namespace}:{name}")
            command(transport, f"function {namespace}:abort")
            command(transport, f"scoreboard objectives remove {namespace}")
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
    manifest.data = json.loads(path.read_text())
    manifest.started = time.monotonic()
    with RconClient.dedicated() as transport:
        cleanup_timeline(manifest, transport)
    return manifest


def run_timeline_proof() -> TrialManifest:
    """Temporary dust/lever timing fixture, not a module or model success."""
    from noob_agent.redstone.trial import RUN_DIRECTORY

    manifest = TrialManifest(RUN_DIRECTORY)
    manifest.data["kind"] = "server_timeline_proof"
    positions = [[94, 64, 95], [93, 64, 95], [1, 64, 0], [2, 64, 0]]
    value = {
        "module": "register",
        "probes": {"a": [{"position": p, "block": "minecraft:redstone_wire"} for p in positions]},
        "controls": [
            {"id": "step", "role": "step", "position": [95, 64, 95]},
            {"id": "reset", "role": "reset", "position": [0, 64, 0]},
        ],
    }
    cells = positions + [[95, 64, 95], [0, 64, 0]]
    installed = []
    try:
        with RconClient.dedicated() as transport:
            actions = Actions(manifest, transport, None, max_actions=100)  # type: ignore[arg-type]
            grader = GraderControl(
                actions, validate_declaration(value, actions.contract), max_ticks=604
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
                    [
                        {"control": "reset", "level": True},
                        {"wait": 4},
                        {"control": "reset", "level": False},
                    ],
                    cycles=2,
                )
                manifest.data["checks"].append(result)
                manifest.save()
                # Independent Python expectations; never embedded in functions.
                for snapshot in result["snapshots"]:
                    observed = snapshot["signals"]["a"]
                    expected = [15, 14, 0, 0] if snapshot["phase"] == "pulse_end" else [0, 0, 0, 0]
                    check = {
                        "phase": snapshot["phase"],
                        "observed": observed,
                        "expected": expected,
                        "passed": observed == expected,
                    }
                    manifest.data["checks"].append(check)
                    assert check["passed"]
                # Deliberately incorrect expectation retained as actual failure evidence.
                manifest.data["checks"].append(
                    {
                        "fixture": "deliberate_wrong_expectation",
                        "expected": [1, 1, 1, 1],
                        "observed": result["snapshots"][-1]["signals"]["a"],
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
