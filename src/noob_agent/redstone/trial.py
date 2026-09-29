"""Durable evidence, preflight and explicitly selected fresh connected trials."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import Field, model_validator

from noob_agent.redstone.contract import FrozenModel, load_contract
from noob_agent.redstone.provider_limits import (
    JEV_HTTP_503_RETRIES_PER_SELECTION,
    JEV_MIN_INTERVAL_SECONDS,
    PLANNER_MAX_OUTPUT_TOKENS,
    PLANNER_TIMEOUT_SECONDS,
)
from noob_agent.redstone.rcon import RconClient

CONTRACT_PATH = Path("scenarios/minecraft/redstone-computer-v1/contract.json")
RUN_DIRECTORY = Path(".noob-agent/redstone-trials")
LAMP_MAX_SESSIONS = 4
JOURNAL_SNAPSHOT_INTERVAL = 128


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


def can_auto_continue(data: dict[str, Any], task: str) -> bool:
    continuation = data.get("continuation", {})
    sessions = data.get("sessions", [])
    return (
        task in {"lamp_repair", "computer"}
        and continuation.get("ready") is True
        and continuation.get("session_number") == len(sessions)
        and continuation.get("stop_reason") in {"LoopLimit", "ActionLimit"}
        and data.get("loop", {}).get("status") == "stopped"
        and len(sessions) < LAMP_MAX_SESSIONS
    )


def can_resume_model_failure(data: dict[str, Any]) -> bool:
    """Recover inference or completed-action boundaries; reject world uncertainty."""
    continuation = data.get("continuation", {})
    events = data.get("events", [])
    return (
        data.get("loop", {}).get("status") == "stopped"
        and data.get("loop", {}).get("reason")
        in {"JevError", "CancelledError", "KeyboardInterrupt"}
        and continuation.get("stop_reason") == data.get("loop", {}).get("reason")
        and continuation.get("session_number") == len(data.get("sessions", []))
        and isinstance(continuation.get("state"), dict)
        and bool(events)
        and (
            (
                events[-1].get("kind") in {"jev_call", "planner_call"}
                and events[-1].get("outcome") == "unknown"
                and (data["loop"]["reason"] != "JevError" or events[-1].get("kind") == "jev_call")
            )
            or (
                data["loop"]["reason"] in {"CancelledError", "KeyboardInterrupt"}
                and events[-1].get("kind") == "action_feedback"
                and events[-1].get("outcome") == "observed"
            )
        )
        and all(
            e.get("kind") in {"jev_call", "planner_call"}
            for e in events
            if e.get("outcome") == "unknown"
        )
        and data.get("timeline_resources", {}).get("state", "clean") == "clean"
    )


def verify_continuation(manifest: TrialManifest, task: str, *, actions: Any) -> dict[str, Any]:
    """Compare the retained build with its last journaled state before resuming."""
    if task == "lamp_repair":
        from noob_agent.redstone.demo import LAMP_POSITION, LEVER_POSITION, WIRE_POSITION

        lever, wire, lamp = (
            actions.observe(position) for position in (LEVER_POSITION, WIRE_POSITION, LAMP_POSITION)
        )
        if lever.get("name") != "minecraft:lever" or lamp.get("name") != "minecraft:redstone_lamp":
            raise ValueError("Saved lamp fixture no longer matches the continuation record")
        if wire.get("name") not in {"minecraft:air", "minecraft:redstone_wire"}:
            raise ValueError("Saved lamp connection no longer matches the continuation record")
        return {"resume_check": {"lever": lever, "wire": wire, "lamp": lamp}}

    if task != "computer":
        raise ValueError("Safe continuation verification is not available for this task")
    from noob_agent.redstone.reset import verify_player

    player = actions.read({"op": "player"})
    verify_player(player)
    states: dict[tuple[int, int, int], list[dict[str, Any]]] = {}
    dynamic = {
        "powered",
        "lit",
        "power",
        "signal_strength",
        "north",
        "south",
        "east",
        "west",
    }
    for event in manifest.data.get("events", []):
        if event.get("kind") != "bounded_action":
            continue
        request = event.get("request", {})
        raw_position = request.get("position")
        action = request.get("action")
        if (
            not isinstance(raw_position, list)
            or len(raw_position) != 3
            or action not in {"place", "break", "interact"}
        ):
            continue
        position = tuple(raw_position)
        if not (0 <= position[0] <= 95 and 64 <= position[1] <= 95 and 0 <= position[2] <= 95):
            continue
        prior = states.setdefault(position, [{"name": "minecraft:air", "properties": {}}])
        result = event.get("result")
        after = result.get("after") if isinstance(result, dict) else None
        if event.get("outcome") == "observed" and isinstance(after, dict):
            props = after.get("properties", {})
            states[position] = [
                {
                    "name": after.get("name"),
                    "properties": {k: v for k, v in props.items() if k not in dynamic},
                }
            ]
        elif event.get("outcome") == "unknown":
            target = (
                {"name": request.get("block"), "properties": request.get("properties") or {}}
                if action == "place"
                else {"name": "minecraft:air", "properties": {}}
                if action == "break"
                else None
            )
            if target is not None:
                prior.append(target)
    observations = []
    mismatches = []
    for position, expected_states in sorted(states.items()):
        actual = actions.observe(list(position))
        matched = any(
            actual.get("name") == expected.get("name")
            and all(
                actual.get("properties", {}).get(k) == v for k, v in expected["properties"].items()
            )
            for expected in expected_states
        )
        if not matched:
            mismatches.append(
                {
                    "position": list(position),
                    "expected_states": expected_states,
                    "actual": actual,
                }
            )
        observations.append(actual)
    verification = {
        "player_verified": True,
        "blocks_checked": len(observations),
        "mismatches": mismatches,
        "observed_cells": observations,
    }
    manifest.data["continuation"]["verification"] = verification
    manifest.data.setdefault("continuation_verifications", []).append(verification)
    state = manifest.data["continuation"].get("state", {})
    state["world_states"] = observations
    state["placed_cells"] = [
        {
            "position": cell["position"],
            "block": cell["name"],
            "properties": cell.get("properties", {}),
        }
        for cell in observations
        if cell.get("name") != "minecraft:air"
    ]
    manifest.data["continuation"]["state"] = state
    manifest.save()
    return {
        "resume_check": {
            "player": player,
            "blocks_checked": len(observations),
            "mismatches": mismatches,
        }
    }


class CommandTransport(Protocol):
    def command(self, command: str) -> str: ...

    def close(self) -> None: ...


class TrialManifest:
    """Single-writer journal with unique directories and fsynced atomic updates.

    Record attempts before delivery. A process killed after delivery therefore
    leaves an explicit unknown outcome, never a fabricated successful result.
    The caller must pass only credential-free structured data.
    """

    def __init__(self, root: Path = RUN_DIRECTORY) -> None:
        self.started = time.monotonic()
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex
        directory = root / run_id
        directory.mkdir(parents=True, exist_ok=False)
        parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
        self.path = directory / "manifest.json"
        self.journal_path = directory / "events.jsonl"
        self._journal_records = 0
        self.data: dict[str, Any] = {
            "schema_version": 1,
            "run_id": run_id,
            "kind": "infrastructure_preflight",
            "started_at": timestamp(),
            "target": {"host": "127.0.0.1", "port": 25567, "rcon_port": 25577},
            "contract": None,
            "template": {"sha256": None, "verified": False},
            "initial_conditions": {"verified": False},
            "limits": None,
            "model": {"identity": None, "configuration": None, "calls": 0},
            "public_prompts": [],
            "public_responses": [],
            "planner_intentions": [],
            "jev_actions": [],
            "events": [],
            "checks": [],
            "errors": [],
            "recording": {"status": "missing", "paths": []},
            "completion": {"status": "incomplete", "ended_at": None},
            "final_grade": {"status": "not_evaluated", "model_success": False},
        }
        self.save()

    @classmethod
    def reopen(cls, path: Path) -> TrialManifest:
        """Open an existing campaign journal for a fresh bounded session."""
        data = cls.load_data(path)
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError("Unsupported continuation record")
        manifest = object.__new__(cls)
        manifest.started = time.monotonic()
        manifest.path = path
        manifest.journal_path = path.with_name("events.jsonl")
        manifest._journal_records = cls._count_journal_records(manifest.journal_path)
        manifest.data = data
        manifest.data.setdefault("sessions", []).append(
            {"started_at": timestamp(), "number": len(manifest.data.get("sessions", [])) + 1}
        )
        manifest.data["completion"] = {"status": "incomplete", "ended_at": None}
        manifest.save()
        return manifest

    @staticmethod
    def _count_journal_records(path: Path) -> int:
        if not path.exists():
            return 0
        return sum(1 for line in path.open(encoding="utf-8") if line.endswith("\n"))

    @classmethod
    def load_data(cls, path: Path) -> dict[str, Any]:
        """Load a legacy snapshot plus any durable events newer than it."""
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError("Unsupported continuation record")
        journal_path = path.with_name("events.jsonl")
        if not journal_path.exists():
            return data
        events = data.setdefault("events", [])
        if not isinstance(events, list):
            raise ValueError("Invalid event list in trial snapshot")
        snapshot_records = data.get("event_journal", {}).get("snapshot_records", 0)
        if type(snapshot_records) is not int or snapshot_records < 0:
            raise ValueError("Invalid event journal snapshot watermark")
        lines = journal_path.read_bytes().splitlines(keepends=True)
        for index, raw in enumerate(lines):
            complete = raw.endswith(b"\n")
            if not complete and index == len(lines) - 1:
                # An interrupted append is never a committed record.
                break
            try:
                record = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValueError("Corrupt trial event journal") from error
            sequence = record.get("sequence") if isinstance(record, dict) else None
            event = record.get("event") if isinstance(record, dict) else None
            if type(sequence) is not int or sequence < 0 or not isinstance(event, dict):
                raise ValueError("Invalid trial event journal record")
            if event.get("sequence") != sequence:
                raise ValueError("Trial event journal sequence mismatch")
            if index < snapshot_records:
                continue
            if sequence < len(events):
                events[sequence] = event
                continue
            if sequence != len(events):
                raise ValueError("Trial event journal has a sequence gap")
            events.append(event)
        return data

    def _append_event(self, sequence: int, event: dict[str, Any]) -> None:
        payload = (
            json.dumps(
                {"sequence": sequence, "event": event}, sort_keys=True, allow_nan=False
            ).encode("utf-8")
            + b"\n"
        )
        existed = self.journal_path.exists()
        if existed:
            with self.journal_path.open("r+b") as existing:
                existing.seek(0, os.SEEK_END)
                end = existing.tell()
                if end:
                    existing.seek(-1, os.SEEK_END)
                    if existing.read(1) != b"\n":
                        contents = self.journal_path.read_bytes()
                        boundary = contents.rfind(b"\n") + 1
                        existing.truncate(boundary)
                        existing.flush()
                        os.fsync(existing.fileno())
        descriptor = os.open(self.journal_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            with os.fdopen(descriptor, "ab", closefd=False) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(descriptor)
        if not existed:
            directory = os.open(self.journal_path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        self._journal_records += 1

    def _maybe_snapshot(self) -> None:
        if self._journal_records and self._journal_records % JOURNAL_SNAPSHOT_INTERVAL == 0:
            self.save()

    def save(self) -> None:
        self.data["event_journal"] = {
            "format": "events-jsonl-v1",
            "snapshot_records": self._journal_records,
        }
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(self.data, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)
        directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def attempt(self, kind: str, request: dict[str, Any]) -> int:
        events = self.data["events"]
        sequence = len(events)
        event = {
            "sequence": sequence,
            "kind": kind,
            "at": timestamp(),
            "request": request,
            "outcome": "unknown",
            "result": None,
        }
        self._append_event(sequence, event)
        events.append(event)
        self._maybe_snapshot()
        return sequence

    def observed(self, sequence: int, result: object) -> None:
        event = self.data["events"][sequence]
        updated = {**event, "outcome": "observed", "result": result, "observed_at": timestamp()}
        self._append_event(sequence, updated)
        event.update(updated)
        self._maybe_snapshot()

    def finish_incomplete(self, reasons: list[str]) -> None:
        sessions = self.data.get("sessions", [])
        if sessions:
            sessions[-1]["ended_at"] = timestamp()
        self.data["completion"] = {
            "status": "incomplete",
            "ended_at": timestamp(),
            "elapsed_seconds": time.monotonic() - self.started,
            "reasons": reasons,
        }
        self.save()


# No world mutation, server lifecycle command or command supplied by a planner.
PREFLIGHT_PROBES = (
    "list",
    "seed",
    "difficulty",
    "time query daytime",
    "gamerule doDaylightCycle",
    "gamerule doWeatherCycle",
    "gamerule randomTickSpeed",
    "tick query",
    "execute if block 0 60 0 minecraft:bedrock run seed",
    "execute if block 0 61 0 minecraft:dirt run seed",
    "execute if block 0 62 0 minecraft:dirt run seed",
    "execute if block 0 63 0 minecraft:grass_block run seed",
    "execute if block 0 64 0 minecraft:air run seed",
)


def run_preflight(
    root: Path = RUN_DIRECTORY,
    *,
    connect: Callable[[], CommandTransport] = RconClient.dedicated,
) -> TrialManifest:
    """Persist startup failures and raw direct readbacks, without claiming readiness."""
    manifest = TrialManifest(root)
    transport: CommandTransport | None = None
    stage = "contract"
    reasons = [
        "Read-only preflight; no construction trial was attempted",
        "Hashed template restoration and two verified resets pending",
        "Bot attachment and initial player/inventory verification pending",
        "Bounded actions and live lever/dust/lamp off/on/off smoke pending",
        "Trial recording missing",
    ]
    try:
        contract = load_contract(CONTRACT_PATH)
        manifest.data["contract"] = {
            "version": contract.version,
            "path": str(CONTRACT_PATH),
            "sha256": hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest(),
        }
        manifest.data["limits"] = contract.budgets.model_dump(mode="json")
        manifest.save()
        stage = "connect"
        transport = connect()
        stage = "probe"
        for command in PREFLIGHT_PROBES:
            sequence = manifest.attempt("trusted_probe", {"command": command})
            manifest.observed(sequence, {"response": transport.command(command)})
        manifest.data["checks"].append(
            {
                "name": "rcon_preflight_transport",
                "status": "responses_received",
                "scope": "Raw responses only; effective settings and full baseline not verified",
            }
        )
    except Exception as error:
        # Exception messages from third-party transports may contain credentials.
        manifest.data["errors"].append({"stage": stage, "type": type(error).__name__})
        reasons.insert(0, f"Preflight failed at {stage}; see error type and pending events")
    except BaseException as error:
        manifest.data["errors"].append({"stage": stage, "type": type(error).__name__})
        reasons.insert(0, f"Preflight interrupted at {stage}; pending outcomes remain unknown")
        raise
    finally:
        try:
            if transport is not None:
                transport.close()
        except Exception as error:
            manifest.data["errors"].append({"stage": "close", "type": type(error).__name__})
            reasons.insert(0, "Transport cleanup failed; see error type")
        finally:
            manifest.finish_incomplete(reasons)
    return manifest


# Keep credentials out of this immutable public selection and its serialization.
class TrialConfiguration(FrozenModel):
    mode: Literal["fixture", "provider"]
    task: Literal["computer", "lamp_repair"] = "computer"
    stop_after_module: Literal["register"] | None = None
    register_workshop: bool = False
    keep_agent_connected: bool = False
    session_action_limit: int | None = Field(default=None, gt=0)
    planner_model: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_./-]{1,160}$")
    planner_project: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_./-]{1,160}$")

    @model_validator(mode="after")
    def explicit_selection(self) -> TrialConfiguration:
        if self.stop_after_module is not None and (
            self.mode != "provider" or self.task != "computer"
        ):
            raise ValueError("Register checkpoint requires a provider computer trial")
        if self.stop_after_module is not None and not self.keep_agent_connected:
            raise ValueError("Register checkpoint requires keeping the agent connected")
        if self.register_workshop and (
            self.mode != "provider" or self.task != "computer" or not self.keep_agent_connected
        ):
            raise ValueError("Register workshop requires a connected provider computer trial")
        if self.session_action_limit is not None and (
            self.session_action_limit > load_contract(CONTRACT_PATH).budgets.primitive_actions
        ):
            raise ValueError("Per-session action limit exceeds the frozen contract")
        if self.mode == "provider" and not self.planner_model:
            raise ValueError("Provider mode requires an explicit planner model")
        if self.mode == "fixture" and (self.planner_model or self.planner_project):
            raise ValueError("Fixture mode cannot accept provider configuration")
        if self.mode == "fixture" and self.task != "computer":
            raise ValueError("Lamp repair demo requires an explicit provider")
        return self

    def public(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "task": self.task,
            **({"stop_after_module": self.stop_after_module} if self.stop_after_module else {}),
            **({"register_workshop": True} if self.register_workshop else {}),
            "keep_agent_connected": self.keep_agent_connected,
            "session_action_limit": self.session_action_limit,
            "planner": {
                "provider": "wandb-inference" if self.mode == "provider" else "fixture",
                "model": self.planner_model if self.mode == "provider" else "fixture/missing-dust",
                "base_url": "https://api.inference.wandb.ai/v1"
                if self.mode == "provider"
                else None,
                "project": self.planner_project,
                "max_output_tokens": PLANNER_MAX_OUTPUT_TOKENS,
                "temperature": 0.0,
                "thinking": False,
                "timeout_seconds": PLANNER_TIMEOUT_SECONDS,
                "retries": 0,
            },
            "jev": {
                "provider": "vercel-ai-gateway" if self.mode == "provider" else "fixture",
                "model": "typesafe-ai/jev" if self.mode == "provider" else "fixture/jev",
                "timeout_seconds": 30,
                "retries": 0,
                "charged_http_503_retries_per_selection": JEV_HTTP_503_RETRIES_PER_SELECTION,
                "min_call_interval_seconds": (
                    JEV_MIN_INTERVAL_SECONDS if self.mode == "provider" else 0
                ),
            },
        }


def run_trial(
    config: TrialConfiguration,
    root: Path = RUN_DIRECTORY,
    *,
    resume: Path | None = None,
) -> TrialManifest:
    """Explicit fresh connected trial with trusted resets and honest partial evidence.

    Provider/computer mode has no scripted layout or fixture fallback. The
    lamp_repair task explicitly seeds a small incomplete circuit and is graded
    separately; it cannot establish full-machine success.
    Credentials are checked before touching Minecraft and never serialized.
    """
    import asyncio
    import sys

    from noob_agent.models.client import ModelClient
    from noob_agent.redstone.actions import Actions
    from noob_agent.redstone.jev import JevSubprocess
    from noob_agent.redstone.loop import TrialLoop
    from noob_agent.redstone.planner import TrialWandbClient
    from noob_agent.redstone.reset import TrustedReset
    from noob_agent.redstone.sidecar import Sidecar
    from noob_agent.settings import ModelSettings, WandbSettings

    if resume is not None:
        if config.register_workshop:
            raise ValueError("Register workshop setup is available only for a fresh trial")
        saved = TrialManifest.load_data(resume)
        current_contract_hash = hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest()
        if not isinstance(saved, dict) or saved.get("kind") != "connected_trial":
            raise ValueError("Only a connected trial can be continued")
        if saved.get("configuration") != config.public():
            raise ValueError("Continuation settings must match the saved trial")
        if saved.get("contract", {}).get("sha256") != current_contract_hash:
            raise ValueError("The saved trial uses a different computer contract")
        if saved.get("continuation", {}).get("ready") is not True and not can_resume_model_failure(
            saved
        ):
            raise ValueError("The saved trial has no safe continuation point")
    manifest = TrialManifest.reopen(resume) if resume is not None else TrialManifest(root)
    if resume is None:
        manifest.data["sessions"] = [{"started_at": timestamp(), "number": 1}]
    manifest.data.update(kind="connected_trial", configuration=config.public())
    contract = load_contract(CONTRACT_PATH)
    manifest.data.update(
        contract={
            "version": contract.version,
            "path": str(CONTRACT_PATH),
            "sha256": hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest(),
        },
        limits=contract.budgets.model_dump(mode="json"),
    )
    manifest.save()
    stage = "configuration"
    persistent_connected = False
    try:
        planner: ModelClient
        checker: Callable[[Any], dict[str, Any]] | None = None
        if config.mode == "fixture":
            from noob_agent.redstone.fixtures import MissingDustCheck, MissingDustPlanner

            planner = MissingDustPlanner()
            checker = MissingDustCheck()
            jev = JevSubprocess(command=[sys.executable, "-m", "noob_agent.redstone.fixtures"])
        else:
            if config.task == "lamp_repair":
                from noob_agent.redstone.demo import LampRepairCheck

                checker = LampRepairCheck()
            key = os.environ.get("WANDB_API_KEY", "").strip()
            if not key or not os.environ.get("AI_GATEWAY_API_KEY", "").strip():
                raise ValueError("Provider credentials unavailable")
            planner = TrialWandbClient(
                ModelSettings(
                    provider="wandb-inference",
                    inference_model=config.planner_model,
                    inference_project=config.planner_project,
                ),
                WandbSettings(api_key=key),
            )
            jev = JevSubprocess()
        stage = "connect"
        with (
            RconClient.dedicated() as transport,
            Sidecar(manifest, keep_connected=config.keep_agent_connected) as sidecar,
        ):
            persistent_connected = config.keep_agent_connected
            reset = TrustedReset(manifest, transport, sidecar)
            try:
                actions = Actions(
                    manifest,
                    transport,
                    sidecar,
                    max_actions=config.session_action_limit,
                )
                sidecar.recovery_charge = actions.charge
                stage = "continuation_check" if resume is not None else "initial_reset"
                if resume is None:
                    reset.restore()
                    observation = {"initial_conditions": manifest.data["initial_conditions"]}
                    if config.register_workshop:
                        from noob_agent.redstone.register_workshop import setup_register_workshop

                        stage = "register_workshop_setup"
                        workshop = setup_register_workshop(manifest, transport, actions)
                        observation["setup_profile"] = workshop
                else:
                    observation = verify_continuation(manifest, config.task, actions=actions)
                stage = "trial_loop"
                if config.task == "lamp_repair" and resume is None:
                    from noob_agent.redstone.demo import prepare_lamp_repair

                    stage = "fixture_setup"
                    manifest.data["demo_task"] = {
                        **prepare_lamp_repair(actions),
                        "status": "running",
                    }
                    manifest.save()
                    stage = "trial_loop"
                announce = None
                if config.task == "lamp_repair":
                    from noob_agent.redstone.demo import ActionPing

                    announce = ActionPing(manifest, transport).show
                loop_options: dict[str, Any] = {}
                if announce is not None:
                    loop_options["announce"] = announce
                loop = TrialLoop(
                    manifest,
                    actions,
                    planner,
                    jev,
                    check=checker,
                    require_module_grading=(
                        config.mode == "provider" and config.task == "computer"
                    ),
                    task=config.task,
                    register_workshop=config.register_workshop,
                    stop_after_module=config.stop_after_module,
                    resume_state=manifest.data.get("continuation", {}).get("state")
                    if resume is not None
                    else None,
                    jev_min_interval_seconds=(
                        JEV_MIN_INTERVAL_SECONDS if config.mode == "provider" else 0
                    ),
                    **loop_options,
                )
                asyncio.run(loop.run(observation))
            except Exception as error:
                manifest.data["errors"].append({"stage": stage, "type": type(error).__name__})
                manifest.save()
            finally:
                lamp_task_complete = (
                    config.task == "lamp_repair"
                    and manifest.data.get("loop", {}).get("status") == "checkpoint_complete"
                )
                if persistent_connected and not lamp_task_complete:
                    # Keep the bot and its current build visible. The next run's
                    # trusted initial reset starts clean without disconnecting it.
                    manifest.data["persistent_agent"] = {
                        "status": "connected_after_trial",
                        "world_state": "preserved_until_next_trial_reset",
                    }
                    manifest.save()
                else:
                    # Standard runs restore the baseline before disconnecting.
                    prior_stage = stage
                    stage = "final_reset"
                    reset.restore()
                    stage = prior_stage
                    if persistent_connected:
                        manifest.data["persistent_agent"] = {
                            "status": "connected_after_trial",
                            "world_state": "baseline_restored_after_success",
                        }
                        manifest.save()
        milestone = manifest.data.get("milestone_4")
        resets = manifest.data.get("resets", [])
        if (
            config.mode == "provider"
            and isinstance(milestone, dict)
            and milestone.get("status") == "checks_passed"
            and manifest.data.get("loop", {}).get("status") == "public_modules_passed"
            and len(resets) >= 2
            and resets[0].get("verified") is True
            and resets[-1].get("verified") is True
            and resets[0].get("baseline_sha256") == resets[-1].get("baseline_sha256")
            and not any(error.get("stage") == "trial_loop" for error in manifest.data["errors"])
        ):
            milestone["status"] = "passed"
            milestone["final_reset_sha256"] = resets[-1]["baseline_sha256"]
            manifest.save()
        if config.task == "lamp_repair":
            demo = manifest.data.setdefault(
                "demo_task",
                {"kind": "seeded_lamp_repair", "fixture_created_by_harness": True},
            )
            checks = manifest.data.get("checks", [])
            final_reset_verified = bool(resets) and resets[-1].get("verified") is True
            same_baseline = (
                len(resets) >= 2
                and resets[0].get("verified") is True
                and resets[-1].get("baseline_sha256") == resets[0].get("baseline_sha256")
            )
            if (
                manifest.data.get("loop", {}).get("status") == "checkpoint_complete"
                and checks
                and checks[-1].get("passed") is True
                and checks[-1].get("complete") is True
                and final_reset_verified
                and same_baseline
            ):
                demo["status"] = "passed"
                demo["final_reset_sha256"] = resets[-1]["baseline_sha256"]
            else:
                demo["status"] = "incomplete"
                demo["final_reset_verified"] = final_reset_verified
            manifest.save()
    except BaseException as error:
        manifest.data["errors"].append({"stage": stage, "type": type(error).__name__})
        manifest.save()
        if stage == "final_reset" and isinstance(error, Exception):
            # A process-group interrupt can kill the sidecar before trusted
            # cleanup finishes. Reconnect once and repeat the full verification.
            try:
                with (
                    RconClient.dedicated() as recovery_transport,
                    Sidecar(manifest) as recovery_sidecar,
                ):
                    TrustedReset(manifest, recovery_transport, recovery_sidecar).restore()
                manifest.data["final_reset_recovery"] = {"verified": True}
                manifest.save()
            except BaseException as recovery_error:
                manifest.data["errors"].append(
                    {"stage": "final_reset_recovery", "type": type(recovery_error).__name__}
                )
                manifest.save()
                if not isinstance(recovery_error, Exception):
                    raise
        if not isinstance(error, Exception):
            raise
    finally:
        milestone_passed = manifest.data.get("milestone_4", {}).get("status") == "passed"
        demo_passed = manifest.data.get("demo_task", {}).get("status") == "passed"
        manifest.finish_incomplete(
            [
                "Seeded lamp repair demo passed; full computer success not established"
                if demo_passed
                else "Fixture evidence only"
                if config.mode == "fixture"
                else "Milestone 4 public modules passed; no final machine grader"
                if milestone_passed
                else "Provider trial; no final grader",
                "Recording pending; model_success applies only to the full computer"
                if demo_passed
                else "Full-machine independent grading and recording pending; model success false"
                if milestone_passed
                else "Real-provider, full-machine and recording verification pending; "
                "no milestone acceptance",
                "Persistent agent remains connected; the next trial resets the same session"
                if persistent_connected
                else "Agent disconnected after trial",
            ]
        )
    if can_auto_continue(manifest.data, config.task):
        return run_trial(config, root, resume=manifest.path)
    return manifest
