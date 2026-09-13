"""Provider-neutral seam for bounded generated-skill execution.

Candidate code is untrusted, so the harness never imports, `exec`s, or
otherwise loads it into its own process. Everything a running skill can do
reaches the harness through the `SkillHost` bridge, which is the only surface
an executor exposes to candidate code.

`DisabledSkillExecutor` is the default and executes nothing. The local
subprocess executor is the fallback `hackathon_plan.md` section 6.5 allows when
CoreWeave Sandbox is unavailable, and every result it produces carries its
weaker-isolation label so a report cannot mistake it for a sandbox run.
"""

from __future__ import annotations

import asyncio
import json
import sys
from asyncio.subprocess import Process
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from noob_agent.domain.model import Observation, StepResult
from noob_agent.settings import SandboxSettings
from noob_agent.skills.contract import SkillBudget, SkillResult

SkillIsolation = Literal["coreweave-sandbox", "local-subprocess", "none"]
SkillExecutionStatus = Literal["completed", "failed", "timed_out", "unavailable"]

LOCAL_ISOLATION_NOTE = (
    "Restricted local subprocess: weaker isolation than CoreWeave Sandbox. The "
    "candidate runs in a separate Python process with guarded imports and "
    "builtins, but no network or memory limit is enforced."
)

# Everything the worker may write to its log stream, per skill_contract.md.
MAX_LOG_BYTES = 64 * 1024
# How long the parent waits for a killed worker to release its pipes.
_REAP_TIMEOUT_SECONDS = 5.0


class SandboxRequest(BaseModel):
    """A candidate-skill execution request for the non-interactive seam."""

    source: str


class SandboxResult(BaseModel):
    """The normalized result of a non-interactive sandbox invocation."""

    status: Literal["completed", "failed", "unavailable"]
    code: str
    message: str


class SandboxExecutor(Protocol):
    """Executes untrusted code only through an approved bounded backend."""

    async def execute(self, request: SandboxRequest) -> SandboxResult:
        """Run one sandbox request."""


class DisabledSandboxExecutor:
    """Safe default that never evaluates supplied source code."""

    async def execute(self, request: SandboxRequest) -> SandboxResult:
        del request
        return SandboxResult(
            status="unavailable",
            code="SANDBOX_DISABLED",
            message="Sandbox execution is disabled in the current configuration.",
        )


class SkillLimits(BaseModel):
    """The limits one invocation runs under: the stricter of skill and episode."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    primitive_actions: int = Field(ge=0)
    wall_time_seconds: float = Field(gt=0)
    log_bytes: int = Field(default=MAX_LOG_BYTES, gt=0)


class SkillHost(Protocol):
    """The harness side of the bridge a running skill talks to.

    This is the `SkillContext` capability surface from `skill_contract.md`,
    seen from the harness: the executor forwards each request a candidate makes
    to exactly one of these methods and relays the answer back.
    """

    async def observe(self) -> Observation:
        """Return the latest complete public snapshot without charging a primitive."""
        ...

    async def call(self, tool_name: str, arguments: dict[str, JsonValue]) -> StepResult:
        """Perform one manifest primitive, record it, and charge it."""
        ...

    def remaining_budget(self) -> SkillBudget:
        """Return what the invocation may still spend."""
        ...

    def log(self, event: str, fields: dict[str, JsonValue]) -> None:
        """Record one bounded public skill event."""
        ...


class SkillHostHalted(RuntimeError):
    """The host cannot continue the invocation: the episode itself has ended.

    Raised from inside a host method (connector lost, result not durably
    recorded). An executor must stop the candidate and report `code`.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SkillExecution(BaseModel):
    """What one executor run produced, labeled with how isolated it was."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: SkillExecutionStatus
    code: str = Field(min_length=1)
    message: str
    result: SkillResult | None = None
    isolation: SkillIsolation
    isolation_note: str = Field(min_length=1)
    logs: str = ""
    worker_pid: int | None = None


class SkillExecutor(Protocol):
    """Runs one candidate against a host, outside the harness process."""

    @property
    def isolation(self) -> SkillIsolation: ...

    @property
    def isolation_note(self) -> str: ...

    async def run(
        self,
        *,
        source: str,
        inputs: dict[str, JsonValue],
        host: SkillHost,
        limits: SkillLimits,
    ) -> SkillExecution:
        """Execute `source`'s entry point with `inputs`, bridging to `host`."""
        ...


class DisabledSkillExecutor:
    """Safe default that never executes a candidate."""

    isolation: SkillIsolation = "none"
    isolation_note = "Skill execution is disabled; no candidate code was run."

    async def run(
        self,
        *,
        source: str,
        inputs: dict[str, JsonValue],
        host: SkillHost,
        limits: SkillLimits,
    ) -> SkillExecution:
        del source, inputs, host, limits
        return SkillExecution(
            status="unavailable",
            code="SANDBOX_DISABLED",
            message=(
                "Skill execution is disabled in the current configuration. Set "
                "NOOB_AGENT_SANDBOX_MODE to enable an executor."
            ),
            isolation=self.isolation,
            isolation_note=self.isolation_note,
        )


class LocalSubprocessSkillExecutor:
    """Runs the candidate in a restricted local Python subprocess.

    The worker (`noob_agent.skills.worker`) loads the candidate with guarded
    builtins and imports, then speaks JSON Lines over its stdio: every
    `observe`, `call`, `remaining_budget`, and `log` the candidate makes is
    forwarded here and answered from the host. Wall time is enforced by this
    process; when it runs out the worker is killed and the run is `timed_out`.
    """

    isolation: SkillIsolation = "local-subprocess"
    isolation_note = LOCAL_ISOLATION_NOTE

    def __init__(self, *, python: str | None = None) -> None:
        self._python = python if python is not None else sys.executable

    async def run(
        self,
        *,
        source: str,
        inputs: dict[str, JsonValue],
        host: SkillHost,
        limits: SkillLimits,
    ) -> SkillExecution:
        worker = Path(__file__).with_name("worker.py")
        src_root = Path(__file__).resolve().parents[2]
        process = await asyncio.create_subprocess_exec(
            self._python,
            "-I",
            str(worker),
            str(src_root),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stderr is not None
        # Drain stderr concurrently so a chatty worker can never block on a full
        # pipe; the worker itself caps what it writes there.
        stderr_task = asyncio.create_task(process.stderr.read())

        try:
            try:
                execution = await asyncio.wait_for(
                    self._converse(process, source, inputs, host, limits),
                    timeout=limits.wall_time_seconds,
                )
            except TimeoutError:
                execution = SkillExecution(
                    status="timed_out",
                    code="WALL_TIME_EXCEEDED",
                    message=(
                        f"The skill did not return within {limits.wall_time_seconds:g} "
                        "seconds and was stopped."
                    ),
                    isolation=self.isolation,
                    isolation_note=self.isolation_note,
                )
            except SkillHostHalted as error:
                # The episode itself ended under the skill; stop the worker.
                execution = self._failed(error.code, str(error))
        finally:
            await _reap(process)

        logs = await stderr_task
        return execution.model_copy(
            update={
                "logs": logs[: limits.log_bytes].decode("utf-8", errors="replace"),
                "worker_pid": process.pid,
            }
        )

    async def _converse(
        self,
        process: Process,
        source: str,
        inputs: dict[str, JsonValue],
        host: SkillHost,
        limits: SkillLimits,
    ) -> SkillExecution:
        assert process.stdin is not None and process.stdout is not None
        await _send(
            process,
            {"type": "start", "source": source, "inputs": inputs, "entry_point": "run"},
        )
        while True:
            line = await process.stdout.readline()
            if not line:
                return self._failed(
                    "WORKER_EXITED",
                    f"The worker exited with code {process.returncode} before returning a result.",
                )
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                return self._failed("PROTOCOL_ERROR", "The worker sent a line that is not JSON.")
            if not isinstance(message, dict):
                return self._failed("PROTOCOL_ERROR", "The worker sent a non-object message.")

            kind = message.get("type")
            if kind == "observe":
                observation = await host.observe()
                await _send(process, {"type": "observation", "observation": _dump(observation)})
            elif kind == "call":
                tool_name = message.get("tool_name")
                arguments = message.get("arguments")
                if not isinstance(tool_name, str) or not isinstance(arguments, dict):
                    return self._failed("PROTOCOL_ERROR", "A call message was malformed.")
                result = await host.call(tool_name, arguments)
                await _send(process, {"type": "step_result", "result": _dump(result)})
            elif kind == "remaining_budget":
                await _send(process, {"type": "budget", "budget": _dump(host.remaining_budget())})
            elif kind == "log":
                event = message.get("event")
                fields = message.get("fields")
                if isinstance(event, str) and isinstance(fields, dict):
                    host.log(event, fields)
                await _send(process, {"type": "ok"})
            elif kind == "result":
                try:
                    returned = SkillResult.model_validate(message.get("result"))
                except ValidationError as error:
                    return self._failed(
                        "SKILL_INVALID_RESULT", f"The skill returned an invalid result: {error}"
                    )
                return SkillExecution(
                    status="completed",
                    code="OK",
                    message="The skill returned a result.",
                    result=returned,
                    isolation=self.isolation,
                    isolation_note=self.isolation_note,
                )
            elif kind == "error":
                code = message.get("code")
                detail = message.get("message")
                return self._failed(
                    code if isinstance(code, str) and code else "SKILL_FAILED",
                    detail if isinstance(detail, str) else "The worker reported an error.",
                )
            else:
                return self._failed("PROTOCOL_ERROR", f"Unknown worker message type {kind!r}.")

    def _failed(self, code: str, message: str) -> SkillExecution:
        return SkillExecution(
            status="failed",
            code=code,
            message=message,
            isolation=self.isolation,
            isolation_note=self.isolation_note,
        )


def _dump(model: BaseModel) -> JsonValue:
    dumped: JsonValue = model.model_dump(mode="json")
    return dumped


async def _send(process: Process, message: dict[str, JsonValue]) -> None:
    assert process.stdin is not None
    process.stdin.write(json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n")
    try:
        await process.stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        # The worker is gone; the next read reports it as exited.
        return


async def _reap(process: Process) -> None:
    """Make sure the worker is gone, whatever state the conversation ended in."""
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(process.wait(), timeout=_REAP_TIMEOUT_SECONDS)
    except TimeoutError:
        pass
    if process.stdin is not None:
        process.stdin.close()


def build_skill_executor(settings: SandboxSettings) -> SkillExecutor:
    """Return the configured executor, or the disabled default.

    CoreWeave Sandbox backends are named in settings but not implemented in
    this milestone; asking for one fails loudly rather than degrading quietly
    to weaker isolation.
    """
    if settings.mode == "disabled":
        return DisabledSkillExecutor()
    if settings.mode == "local":
        return LocalSubprocessSkillExecutor()
    raise NotImplementedError(
        f"Sandbox mode {settings.mode!r} has no skill executor yet; use 'local' for the "
        "labeled weaker-isolation fallback or 'disabled'."
    )
