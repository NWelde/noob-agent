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
from noob_agent.redstone.rcon import RconClient

CONTRACT_PATH = Path("scenarios/minecraft/redstone-computer-v1/contract.json")
RUN_DIRECTORY = Path(".noob-agent/redstone-trials")


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


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

    def save(self) -> None:
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
        events.append(
            {
                "sequence": sequence,
                "kind": kind,
                "at": timestamp(),
                "request": request,
                "outcome": "unknown",
                "result": None,
            }
        )
        self.save()
        return sequence

    def observed(self, sequence: int, result: object) -> None:
        event = self.data["events"][sequence]
        event.update(outcome="observed", result=result, observed_at=timestamp())
        self.save()

    def finish_incomplete(self, reasons: list[str]) -> None:
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
    planner_model: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_./-]{1,160}$")
    planner_project: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_./-]{1,160}$")

    @model_validator(mode="after")
    def explicit_selection(self) -> TrialConfiguration:
        if self.mode == "provider" and not self.planner_model:
            raise ValueError("Provider mode requires an explicit planner model")
        if self.mode == "fixture" and (self.planner_model or self.planner_project):
            raise ValueError("Fixture mode cannot accept provider configuration")
        return self

    def public(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "planner": {
                "provider": "wandb-inference" if self.mode == "provider" else "fixture",
                "model": self.planner_model if self.mode == "provider" else "fixture/missing-dust",
                "base_url": "https://api.inference.wandb.ai/v1"
                if self.mode == "provider"
                else None,
                "project": self.planner_project,
                "max_output_tokens": 4096,
                "temperature": 0.0,
                "thinking": None,
                "timeout_seconds": 30,
                "retries": 0,
            },
            "jev": {
                "provider": "vercel-ai-gateway" if self.mode == "provider" else "fixture",
                "model": "typesafe-ai/jev" if self.mode == "provider" else "fixture/jev",
                "timeout_seconds": 30,
                "retries": 0,
            },
        }


def run_trial(config: TrialConfiguration, root: Path = RUN_DIRECTORY) -> TrialManifest:
    """Explicit fresh connected trial with trusted resets and honest partial evidence.

    Provider mode has no scripted layout/checker or fixture fallback. Until module
    graders exist it runs to a declared limit; it cannot establish machine success.
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

    manifest = TrialManifest(root)
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
    try:
        planner: ModelClient
        checker = None
        if config.mode == "fixture":
            from noob_agent.redstone.fixtures import MissingDustCheck, MissingDustPlanner

            planner = MissingDustPlanner()
            checker = MissingDustCheck()
            jev = JevSubprocess(command=[sys.executable, "-m", "noob_agent.redstone.fixtures"])
        else:
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
        with RconClient.dedicated() as transport, Sidecar(manifest) as sidecar:
            reset = TrustedReset(manifest, transport, sidecar)
            stage = "initial_reset"
            reset.restore()
            try:
                stage = "trial_loop"
                actions = Actions(manifest, transport, sidecar)
                loop = TrialLoop(manifest, actions, planner, jev, check=checker)
                asyncio.run(loop.run({"initial_conditions": manifest.data["initial_conditions"]}))
            except Exception as error:
                manifest.data["errors"].append({"stage": stage, "type": type(error).__name__})
                manifest.save()
            finally:
                # Restoration is trusted cleanup, independent of exhausted model/action budgets.
                prior_stage = stage
                stage = "final_reset"
                reset.restore()
                stage = prior_stage
    except BaseException as error:
        manifest.data["errors"].append({"stage": stage, "type": type(error).__name__})
        if not isinstance(error, Exception):
            raise
    finally:
        manifest.finish_incomplete(
            [
                "Fixture evidence only"
                if config.mode == "fixture"
                else "Provider trial; no final grader",
                "Real-provider, full-machine and recording verification pending; "
                "no milestone acceptance",
            ]
        )
    return manifest
