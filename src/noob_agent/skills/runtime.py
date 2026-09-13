"""Invoking an accepted skill inside a running episode.

One skill invocation is one Action-agent decision. Every primitive the skill
calls is a nested step: it is sent through the public connector, recorded
durably before the skill sees its result, and charged to the same episode
budget the agent is spending. The stricter of the skill's declared limits and
the episode's remaining budget always wins.

The runtime never loads candidate code. It builds the harness side of the
skill bridge (`SkillHost`) and hands it, with the source, to a `SkillExecutor`
that runs the candidate somewhere else.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from noob_agent.connectors import ConnectorLostError, GameConnector
from noob_agent.domain.model import (
    ConnectorManifest,
    Observation,
    PublicModel,
    StepResult,
    ToolRequest,
)
from noob_agent.domain.records import StepRecord
from noob_agent.domain.skills import SkillVersion
from noob_agent.skills.contract import EvidenceRef, SkillBudget, SkillResult
from noob_agent.skills.executor import (
    SkillExecution,
    SkillExecutor,
    SkillHostHalted,
    SkillIsolation,
    SkillLimits,
)
from noob_agent.skills.metadata import SkillMetadata
from noob_agent.storage import StorageError

# Skill log events kept per invocation; later entries are dropped, not stored.
MAX_LOG_ENTRIES = 256

SkillUseStatus = Literal["completed", "failed", "timed_out", "unavailable"]
AgentSkillStatus = Literal["succeeded", "failed", "inconclusive"]


class SkillNotAvailableError(RuntimeError):
    """The version is not `accepted`, so the Action agent may not invoke it."""


class SkillRequest(PublicModel):
    """One Action-agent decision to invoke a learned skill."""

    action_id: str = Field(min_length=1)
    skill_name: str = Field(min_length=1)
    inputs: dict[str, JsonValue] = Field(default_factory=dict)


class SkillUseRecord(BaseModel):
    """The Skill use record from `hackathon_plan.md` section 9.

    It says which frozen artifact ran, what it was given, what came back, what
    it cost, whether its own success check held up, and how isolated the run
    was. It carries no private state.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    episode_id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    skill_name: str = Field(min_length=1)
    skill_version: int = Field(ge=1)
    content_hash: str = Field(min_length=1)
    inputs: dict[str, JsonValue] = Field(default_factory=dict)
    status: SkillUseStatus
    code: str = Field(min_length=1)
    message: str
    result: SkillResult | None = None
    primitive_actions_consumed: int = Field(ge=0)
    nested_sequences: tuple[int, ...] = ()
    success_check_passed: bool | None = None
    isolation: SkillIsolation
    isolation_note: str = Field(min_length=1)
    logs: tuple[dict[str, JsonValue], ...] = ()
    worker_pid: int | None = None


@dataclass(frozen=True)
class SkillInvocation:
    """Everything the runner needs after one skill invocation returns."""

    use: SkillUseRecord
    steps: tuple[StepRecord, ...]
    observation: Observation
    unknown_result: bool
    connector_lost: bool
    record_failed: bool

    @property
    def agent_status(self) -> AgentSkillStatus:
        """What the Action agent is told: the skill's own claim, or why it has none."""
        if self.use.result is not None:
            return self.use.result.status
        if self.use.status == "failed":
            return "failed"
        return "inconclusive"


class _EpisodeSkillHost:
    """The harness side of one invocation's bridge.

    Holds the invocation's budget, forwards primitives to the connector,
    records each nested step before the skill sees the result, and refuses to
    continue once a result is unknown or unrecordable.
    """

    def __init__(
        self,
        *,
        connector: GameConnector,
        tool_names: frozenset[str],
        observation: Observation,
        sequence: int,
        action_prefix: str,
        record_step: Callable[[StepRecord], None],
        primitive_limit: int,
        wall_time_seconds: float,
    ) -> None:
        self._connector = connector
        self._tool_names = tool_names
        self._record_step = record_step
        self._primitive_limit = primitive_limit
        self._action_prefix = action_prefix
        self._deadline = time.monotonic() + wall_time_seconds
        self._calls = 0
        self.observation = observation
        self.sequence = sequence
        self.steps: list[StepRecord] = []
        self.logs: list[dict[str, JsonValue]] = []
        self.primitives_used = 0
        self.unknown = False
        self.lost = False
        self.record_failed = False
        self.in_flight = False

    @property
    def halted(self) -> bool:
        return self.unknown or self.lost or self.record_failed

    async def observe(self) -> Observation:
        return self.observation

    def remaining_budget(self) -> SkillBudget:
        return SkillBudget(
            primitive_actions=max(self._primitive_limit - self.primitives_used, 0),
            wall_time_seconds=max(self._deadline - time.monotonic(), 0.0),
        )

    def log(self, event: str, fields: dict[str, JsonValue]) -> None:
        if len(self.logs) < MAX_LOG_ENTRIES:
            self.logs.append({"event": event, "fields": fields})

    async def call(self, tool_name: str, arguments: dict[str, JsonValue]) -> StepResult:
        self._calls += 1
        action_id = f"{self._action_prefix}.{self._calls}"

        if self.halted:
            return self._refused(
                action_id,
                "PRECONDITION_FAILED",
                "An earlier primitive's result is unresolved; the skill must stop, not retry.",
            )
        if tool_name not in self._tool_names:
            return self._refused(
                action_id,
                "INVALID_TOOL",
                f"{tool_name!r} is not a primitive in this episode's manifest. "
                "A skill may call only manifest primitives, never another skill.",
            )
        if self.primitives_used >= self._primitive_limit:
            return self._refused(
                action_id,
                "BUDGET_EXHAUSTED",
                f"This invocation may use {self._primitive_limit} primitive actions "
                "and has used them all.",
            )

        request = ToolRequest(action_id=action_id, tool_name=tool_name, arguments=arguments)
        self.in_flight = True
        try:
            result = await self._connector.step(request)
        except ConnectorLostError as error:
            self.lost = True
            raise SkillHostHalted("CONNECTOR_LOST", str(error)) from error
        finally:
            self.in_flight = False

        # Recorded before the skill sees it, exactly as the runner records a
        # decision's result before another action is allowed.
        expected = self.sequence + 1
        try:
            if result.sequence != expected:
                raise ValueError(
                    f"The connector returned sequence {result.sequence}, expected {expected}."
                )
            step = StepRecord(
                episode_id=self.observation.episode_id,
                sequence=result.sequence,
                request=request,
                result=result,
            )
            self._record_step(step)
        except (ValidationError, ValueError, StorageError) as error:
            self.record_failed = True
            raise SkillHostHalted(
                "UNRECORDED_RESULT", f"A nested primitive result could not be recorded: {error}"
            ) from error

        self.steps.append(step)
        self.sequence = result.sequence
        self.primitives_used += result.primitive_actions_charged
        self.observation = result.observation
        if result.status == "unknown":
            self.unknown = True
        return result

    def _refused(self, action_id: str, code: str, message: str) -> StepResult:
        """Refuse a call locally: nothing is sent, charged, or recorded."""
        return StepResult(
            action_id=action_id,
            sequence=self.sequence,
            status="rejected",
            code=code,
            message=message,
            observation=self.observation,
            state_changed=False,
            primitive_actions_charged=0,
            logical_duration=0,
            wall_time_ms=0,
        )


class SkillRuntime:
    """Invokes accepted skills for one episode through a configured executor."""

    def __init__(self, executor: SkillExecutor, *, available: Iterable[SkillVersion] = ()) -> None:
        self._executor = executor
        self._available: dict[str, SkillVersion] = {}
        for version in available:
            if version.status != "accepted":
                raise SkillNotAvailableError(
                    f"Skill {version.name!r} version {version.version} is "
                    f"{version.status!r}; only accepted versions may be offered."
                )
            self._available[version.name] = version

    @property
    def available(self) -> tuple[SkillVersion, ...]:
        return tuple(self._available[name] for name in sorted(self._available))

    def lookup(self, name: str) -> SkillVersion | None:
        """The accepted version offered under `name`, or `None`."""
        return self._available.get(name)

    async def invoke(
        self,
        request: SkillRequest,
        version: SkillVersion,
        *,
        connector: GameConnector,
        manifest: ConnectorManifest,
        observation: Observation,
        sequence: int,
        record_step: Callable[[StepRecord], None],
        remaining_primitives: int,
        remaining_wall_ms: int,
    ) -> SkillInvocation:
        """Run one accepted skill within the stricter of its and the episode's limits."""
        if version.status != "accepted":
            raise SkillNotAvailableError(
                f"Skill {version.name!r} version {version.version} is {version.status!r}, "
                "not accepted, and cannot be invoked."
            )
        try:
            metadata = SkillMetadata.model_validate(version.package.metadata)
        except ValidationError as error:
            raise SkillNotAvailableError(
                f"Skill {version.name!r} version {version.version} has unreadable metadata."
            ) from error

        primitive_limit = min(metadata.max_primitive_actions, max(remaining_primitives, 0))
        wall_time_seconds = min(metadata.max_wall_time_seconds, remaining_wall_ms / 1000)

        host = _EpisodeSkillHost(
            connector=connector,
            tool_names=frozenset(tool.name for tool in manifest.tools),
            observation=observation,
            sequence=sequence,
            action_prefix=request.action_id,
            record_step=record_step,
            primitive_limit=primitive_limit,
            wall_time_seconds=max(wall_time_seconds, 0.0),
        )

        if wall_time_seconds <= 0:
            execution = SkillExecution(
                status="timed_out",
                code="WALL_TIME_EXHAUSTED",
                message="No wall time remained in the episode when the skill was invoked.",
                isolation=self._executor.isolation,
                isolation_note=self._executor.isolation_note,
            )
        else:
            execution = await self._executor.run(
                source=version.package.source,
                inputs=request.inputs,
                host=host,
                limits=SkillLimits(
                    primitive_actions=primitive_limit, wall_time_seconds=wall_time_seconds
                ),
            )

        # A wall-time stop that interrupted a primitive in flight leaves that
        # action's effect unconfirmed, which is exactly an unknown result.
        unknown = host.unknown or (execution.status == "timed_out" and host.in_flight)
        steps = tuple(host.steps)
        success, code, message = _judge(execution, steps, host.observation)

        use = SkillUseRecord(
            episode_id=observation.episode_id,
            action_id=request.action_id,
            skill_name=version.name,
            skill_version=version.version,
            content_hash=version.content_hash,
            inputs=request.inputs,
            status=execution.status,
            code=code,
            message=message,
            result=execution.result,
            primitive_actions_consumed=host.primitives_used,
            nested_sequences=tuple(step.sequence for step in steps),
            success_check_passed=success,
            isolation=execution.isolation,
            isolation_note=execution.isolation_note,
            logs=tuple(host.logs),
            worker_pid=execution.worker_pid,
        )
        return SkillInvocation(
            use=use,
            steps=steps,
            observation=host.observation,
            unknown_result=unknown,
            connector_lost=host.lost,
            record_failed=host.record_failed,
        )


def _judge(
    execution: SkillExecution, steps: tuple[StepRecord, ...], latest: Observation
) -> tuple[bool | None, str, str]:
    """Decide whether the skill's own success check held up.

    A `succeeded` result must cite at least one public record captured at or
    after the invocation's last state-changing primitive (`skill_contract.md`,
    Runtime API). A claim citing only earlier evidence is recorded as not
    passing, with the reason, while the skill's result is kept as returned.
    """
    result = execution.result
    if result is None:
        return None, execution.code, execution.message
    if result.status != "succeeded":
        return False, execution.code, execution.message
    if any(_is_fresh(reference, steps, latest) for reference in result.evidence):
        return True, execution.code, execution.message
    return (
        False,
        "STALE_EVIDENCE",
        "The success claim cites no public evidence captured after the skill's last "
        "state-changing primitive.",
    )


def _is_fresh(reference: EvidenceRef, steps: tuple[StepRecord, ...], latest: Observation) -> bool:
    changed = [step for step in steps if step.result.state_changed is True]
    threshold = changed[-1].sequence if changed else (steps[0].sequence - 1 if steps else 0)

    if reference.kind == "action_id":
        return any(
            step.request.action_id == reference.value and step.sequence >= threshold
            for step in steps
        )
    if reference.kind == "observation_sequence":
        try:
            cited = int(reference.value)
        except ValueError:
            return False
        return threshold <= cited <= latest.sequence
    if reference.kind == "object_id":
        return any(visible.object_id == reference.value for visible in latest.visible_objects)
    return any(message.text == reference.value for message in latest.messages)
