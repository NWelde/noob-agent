"""Invoking an accepted skill: nested primitives, budgets, and the process boundary."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import pytest
from fakes.connector import SCENARIO_ID, ScriptedConnector, ScriptedStep
from pydantic import JsonValue

from noob_agent.domain.model import Observation, StepResult
from noob_agent.domain.records import EpisodeRecord, ExperimentRecord, StepRecord
from noob_agent.domain.skills import SkillPackage, SkillVersion
from noob_agent.settings import SandboxSettings
from noob_agent.skills import SkillRegistry
from noob_agent.skills.contract import EvidenceRef, SkillBudget, SkillResult
from noob_agent.skills.executor import (
    DisabledSkillExecutor,
    LocalSubprocessSkillExecutor,
    SkillExecution,
    SkillHost,
    SkillLimits,
    build_skill_executor,
)
from noob_agent.skills.runtime import (
    SkillInvocation,
    SkillNotAvailableError,
    SkillRequest,
    SkillRuntime,
)
from noob_agent.storage import EpisodeStore

AUTHORED_AT = datetime(2026, 9, 12, 18, 0, 0, tzinfo=UTC)
SKILL_NAME = "activate_visible_lantern"

METADATA: dict[str, JsonValue] = {
    "name": SKILL_NAME,
    "version": 1,
    "parent_version": None,
    "purpose": "Use the visible lantern and confirm it lit.",
    "input_schema": {"properties": {"object_id": {"type": "string"}}},
    "required_tools": ["observe", "use_object"],
    "max_primitive_actions": 8,
    "max_wall_time_seconds": 30,
    "success_claim": "The lantern reports lit after use.",
    "api_version": "noob-agent.skill.v1",
}

# A skill that uses the requested object and cites the action that did it.
USE_OBJECT_SOURCE = '''"""Use one visible object and report what happened."""

from noob_agent.skills.contract import EvidenceRef, SkillContext, SkillResult


async def run(context: SkillContext, inputs: dict[str, object]) -> SkillResult:
    """Use the object named by `object_id` and check the visible result."""
    object_id = inputs.get("object_id")
    if not isinstance(object_id, str):
        return SkillResult(status="failed", summary="object_id is required.",
                           primitive_actions_used=0)
    result = await context.call("use_object", object_id=object_id)
    used = result.primitive_actions_charged
    if result.status != "succeeded":
        return SkillResult(status="failed", summary=f"use_object {result.status}.",
                           primitive_actions_used=used)
    after = await context.observe()
    context.log("used", {"object_id": object_id, "sequence": after.sequence})
    return SkillResult(
        status="succeeded",
        summary="Used the object.",
        evidence=(EvidenceRef(kind="action_id", value=result.action_id),),
        outputs={"terminal": after.terminal},
        primitive_actions_used=used,
    )
'''

# A skill that never returns; only a wall-time limit can stop it.
SPINNING_SOURCE = '''"""Never return."""

from noob_agent.skills.contract import SkillContext, SkillResult


async def run(context: SkillContext, inputs: dict[str, object]) -> SkillResult:
    """Spin until the harness stops us."""
    total = 0
    while True:
        total += 1
    return SkillResult(status="failed", summary="unreachable", primitive_actions_used=0)
'''

# A skill that claims success without any evidence, which the contract refuses.
BARE_SUCCESS_SOURCE = '''"""Claim success with nothing to back it."""

from noob_agent.skills.contract import SkillContext, SkillResult


async def run(context: SkillContext, inputs: dict[str, object]) -> SkillResult:
    """Return an unsupported success claim."""
    return SkillResult(status="succeeded", summary="Trust me.", primitive_actions_used=0)
'''


class ScriptedSkillExecutor:
    """Drives the host directly from a test coroutine: no candidate source involved.

    This stands in for a sandbox in tests about *host* behavior. It never
    executes `source`, so it says so in its isolation label.
    """

    isolation = "none"
    isolation_note = "Test double: runs a test-supplied coroutine against the host."

    def __init__(
        self, script: Callable[[SkillHost, dict[str, JsonValue]], Awaitable[SkillResult]]
    ) -> None:
        self._script = script
        self.runs = 0

    async def run(
        self,
        *,
        source: str,
        inputs: dict[str, JsonValue],
        host: SkillHost,
        limits: SkillLimits,
    ) -> SkillExecution:
        self.runs += 1
        result = await self._script(host, inputs)
        return SkillExecution(
            status="completed",
            code="OK",
            message="Scripted run finished.",
            result=result,
            isolation="none",
            isolation_note=self.isolation_note,
        )


def accepted_version(
    registry: SkillRegistry, source: str = USE_OBJECT_SOURCE, **overrides: JsonValue
) -> SkillVersion:
    package = SkillPackage(name=SKILL_NAME, source=source, metadata={**METADATA, **overrides})
    proposed = registry.propose(
        package,
        authoring_episode_id="ep_training_0001",
        authoring_model_id="fake-model-a",
        created_at=AUTHORED_AT,
    )
    registry.begin_validation(SKILL_NAME, proposed.version, reason="test")
    return registry.accept(SKILL_NAME, proposed.version, reason="test")


@pytest.fixture
def registry() -> SkillRegistry:
    return SkillRegistry()


@pytest.fixture
def recording_store(
    store: EpisodeStore, experiment: ExperimentRecord, episode: EpisodeRecord
) -> EpisodeStore:
    store.create_experiment(experiment)
    store.create_episode(episode)
    return store


async def invoke(
    runtime: SkillRuntime,
    version: SkillVersion,
    connector: ScriptedConnector,
    store: EpisodeStore,
    *,
    inputs: dict[str, JsonValue] | None = None,
    remaining_primitives: int = 24,
    remaining_wall_ms: int = 90_000,
    sequence: int = 0,
) -> SkillInvocation:
    observation = await connector.reset(SCENARIO_ID, 7)
    return await runtime.invoke(
        SkillRequest(
            action_id="a_0001",
            skill_name=SKILL_NAME,
            inputs={"object_id": "obj_1"} if inputs is None else inputs,
        ),
        version,
        connector=connector,
        manifest=await connector.manifest(),
        observation=observation,
        sequence=sequence,
        record_step=store.append_step,
        remaining_primitives=remaining_primitives,
        remaining_wall_ms=remaining_wall_ms,
    )


async def _never(host: SkillHost, inputs: dict[str, JsonValue]) -> SkillResult:
    raise AssertionError("The executor must not run for an unavailable version.")


async def test_only_an_accepted_version_can_be_invoked(
    registry: SkillRegistry, recording_store: EpisodeStore
) -> None:
    """Proposed, validating, rejected, and retired versions are never executed."""
    package = SkillPackage(name=SKILL_NAME, source=USE_OBJECT_SOURCE, metadata=METADATA)
    proposed = registry.propose(
        package,
        authoring_episode_id="ep_training_0001",
        authoring_model_id="fake-model-a",
        created_at=AUTHORED_AT,
    )
    executor = ScriptedSkillExecutor(_never)
    runtime = SkillRuntime(executor, available=registry.available_skills())

    with pytest.raises(SkillNotAvailableError):
        await invoke(runtime, proposed, ScriptedConnector(ScriptedStep()), recording_store)

    validating = registry.begin_validation(SKILL_NAME, proposed.version, reason="test")
    with pytest.raises(SkillNotAvailableError):
        await invoke(runtime, validating, ScriptedConnector(ScriptedStep()), recording_store)

    rejected = registry.reject(SKILL_NAME, proposed.version, reason="test")
    with pytest.raises(SkillNotAvailableError):
        await invoke(runtime, rejected, ScriptedConnector(ScriptedStep()), recording_store)

    accepted = accepted_version(registry)
    retired = registry.retire(SKILL_NAME, accepted.version, reason="test")
    with pytest.raises(SkillNotAvailableError):
        await invoke(runtime, retired, ScriptedConnector(ScriptedStep()), recording_store)

    assert executor.runs == 0
    assert runtime.lookup(SKILL_NAME) is None


async def test_nested_primitives_are_recorded_and_charged(
    registry: SkillRegistry, recording_store: EpisodeStore
) -> None:
    version = accepted_version(registry)
    connector = ScriptedConnector(
        ScriptedStep(state_changed=True),
        ScriptedStep(),
        ScriptedStep(state_changed=True, terminal=True, terminal_reason="goal_reached"),
    )

    async def script(host: SkillHost, inputs: dict[str, JsonValue]) -> SkillResult:
        first = await host.call("use_object", {"object_id": "obj_1"})
        await host.call("observe", {"radius": 4})
        last = await host.call("use_object", {"object_id": "obj_1"})
        assert host.remaining_budget().primitive_actions == 5
        return SkillResult(
            status="succeeded",
            summary="Used it twice.",
            evidence=(EvidenceRef(kind="action_id", value=last.action_id),),
            primitive_actions_used=first.primitive_actions_charged + 2,
        )

    runtime = SkillRuntime(ScriptedSkillExecutor(script), available=(version,))
    invocation = await invoke(runtime, version, connector, recording_store)

    stored = recording_store.read_episode("ep_0001")
    assert [step.sequence for step in stored.steps] == [1, 2, 3]
    assert [step.request.action_id for step in stored.steps] == [
        "a_0001.1",
        "a_0001.2",
        "a_0001.3",
    ]
    assert [step.request.tool_name for step in stored.steps] == [
        "use_object",
        "observe",
        "use_object",
    ]
    assert invocation.use.primitive_actions_consumed == 3
    assert invocation.use.nested_sequences == (1, 2, 3)
    assert invocation.use.status == "completed"
    assert invocation.use.success_check_passed is True
    assert invocation.use.skill_version == version.version
    assert invocation.use.content_hash == version.content_hash
    assert invocation.observation.terminal is True
    assert invocation.steps == stored.steps


async def test_a_skill_cannot_call_another_generated_skill(
    registry: SkillRegistry, recording_store: EpisodeStore
) -> None:
    """Only manifest primitives are callable; the skill's own name is refused."""
    version = accepted_version(registry)
    connector = ScriptedConnector(ScriptedStep())
    seen: list[StepResult] = []

    async def script(host: SkillHost, inputs: dict[str, JsonValue]) -> SkillResult:
        seen.append(await host.call(SKILL_NAME, {"object_id": "obj_1"}))
        seen.append(await host.call("record_visible_state", {}))
        return SkillResult(status="failed", summary="Could not delegate.", primitive_actions_used=0)

    runtime = SkillRuntime(ScriptedSkillExecutor(script), available=(version,))
    invocation = await invoke(runtime, version, connector, recording_store)

    assert [result.status for result in seen] == ["rejected", "rejected"]
    assert [result.code for result in seen] == ["INVALID_TOOL", "INVALID_TOOL"]
    assert invocation.use.primitive_actions_consumed == 0
    assert connector.requests == []
    assert invocation.use.status == "completed"
    assert invocation.use.success_check_passed is False


async def test_the_stricter_of_skill_and_episode_budget_wins(
    registry: SkillRegistry, recording_store: EpisodeStore
) -> None:
    version = accepted_version(registry)  # declares up to 8 primitives
    connector = ScriptedConnector(ScriptedStep(), ScriptedStep(), ScriptedStep())
    seen: list[StepResult] = []

    async def script(host: SkillHost, inputs: dict[str, JsonValue]) -> SkillResult:
        assert host.remaining_budget().primitive_actions == 2
        for _ in range(3):
            seen.append(await host.call("observe", {"radius": 2}))
        assert host.remaining_budget().primitive_actions == 0
        return SkillResult(status="inconclusive", summary="Out of budget.", primitive_actions_used=2)

    runtime = SkillRuntime(ScriptedSkillExecutor(script), available=(version,))
    invocation = await invoke(runtime, version, connector, recording_store, remaining_primitives=2)

    assert [result.status for result in seen] == ["succeeded", "succeeded", "rejected"]
    assert seen[2].code == "BUDGET_EXHAUSTED"
    assert len(connector.requests) == 2
    assert invocation.use.primitive_actions_consumed == 2
    assert len(recording_store.read_episode("ep_0001").steps) == 2


async def test_the_skill_declaration_caps_the_budget_below_the_episode(
    registry: SkillRegistry, recording_store: EpisodeStore
) -> None:
    version = accepted_version(registry, max_primitive_actions=1)
    connector = ScriptedConnector(ScriptedStep(), ScriptedStep())
    seen: list[StepResult] = []

    async def script(host: SkillHost, inputs: dict[str, JsonValue]) -> SkillResult:
        seen.append(await host.call("observe", {"radius": 2}))
        seen.append(await host.call("observe", {"radius": 2}))
        return SkillResult(status="inconclusive", summary="Capped.", primitive_actions_used=1)

    runtime = SkillRuntime(ScriptedSkillExecutor(script), available=(version,))
    await invoke(runtime, version, connector, recording_store, remaining_primitives=24)

    assert [result.code for result in seen] == ["OK", "BUDGET_EXHAUSTED"]
    assert len(connector.requests) == 1


async def test_an_unknown_result_halts_the_skill_without_a_retry(
    registry: SkillRegistry, recording_store: EpisodeStore
) -> None:
    version = accepted_version(registry)
    connector = ScriptedConnector(
        ScriptedStep(status="unknown", code="TIMEOUT_UNKNOWN", state_changed=None),
        ScriptedStep(),
    )
    seen: list[StepResult] = []

    async def script(host: SkillHost, inputs: dict[str, JsonValue]) -> SkillResult:
        seen.append(await host.call("use_object", {"object_id": "obj_1"}))
        # A retry after an unknown result is exactly what the contract forbids.
        seen.append(await host.call("use_object", {"object_id": "obj_1"}))
        return SkillResult(status="inconclusive", summary="Ambiguous.", primitive_actions_used=1)

    runtime = SkillRuntime(ScriptedSkillExecutor(script), available=(version,))
    invocation = await invoke(runtime, version, connector, recording_store)

    assert seen[0].status == "unknown"
    assert seen[1].status == "rejected"
    assert seen[1].code == "PRECONDITION_FAILED"
    assert len(connector.requests) == 1
    assert invocation.unknown_result is True
    assert invocation.use.primitive_actions_consumed == 1
    assert len(recording_store.read_episode("ep_0001").steps) == 1


async def test_a_skill_receives_the_current_observation_without_a_charge(
    registry: SkillRegistry, recording_store: EpisodeStore
) -> None:
    version = accepted_version(registry)
    connector = ScriptedConnector()
    observed: list[Observation] = []

    async def script(host: SkillHost, inputs: dict[str, JsonValue]) -> SkillResult:
        observed.append(await host.observe())
        host.log("looked", {"objects": len(observed[0].visible_objects)})
        return SkillResult(status="inconclusive", summary="Looked only.", primitive_actions_used=0)

    runtime = SkillRuntime(ScriptedSkillExecutor(script), available=(version,))
    invocation = await invoke(runtime, version, connector, recording_store)

    assert observed[0].sequence == 0
    assert connector.requests == []
    assert invocation.use.primitive_actions_consumed == 0
    assert invocation.use.logs == ({"event": "looked", "fields": {"objects": 1}},)


async def test_a_success_claim_needs_evidence_from_after_the_last_state_change(
    registry: SkillRegistry, recording_store: EpisodeStore
) -> None:
    version = accepted_version(registry)
    connector = ScriptedConnector(ScriptedStep(), ScriptedStep(state_changed=True))

    async def script(host: SkillHost, inputs: dict[str, JsonValue]) -> SkillResult:
        before = await host.call("observe", {"radius": 2})
        await host.call("use_object", {"object_id": "obj_1"})
        return SkillResult(
            status="succeeded",
            summary="Cites the observation taken before acting.",
            evidence=(EvidenceRef(kind="observation_sequence", value=str(before.sequence)),),
            primitive_actions_used=2,
        )

    runtime = SkillRuntime(ScriptedSkillExecutor(script), available=(version,))
    invocation = await invoke(runtime, version, connector, recording_store)

    assert invocation.use.status == "completed"
    assert invocation.use.result is not None
    assert invocation.use.result.status == "succeeded"
    assert invocation.use.success_check_passed is False
    assert invocation.use.code == "STALE_EVIDENCE"


async def test_the_disabled_executor_never_runs_the_candidate(
    registry: SkillRegistry, recording_store: EpisodeStore
) -> None:
    version = accepted_version(registry)
    connector = ScriptedConnector(ScriptedStep())
    runtime = SkillRuntime(DisabledSkillExecutor(), available=(version,))

    invocation = await invoke(runtime, version, connector, recording_store)

    assert invocation.use.status == "unavailable"
    assert invocation.use.code == "SANDBOX_DISABLED"
    assert invocation.use.result is None
    assert invocation.use.isolation == "none"
    assert connector.requests == []


def test_the_executor_follows_the_sandbox_setting() -> None:
    assert isinstance(build_skill_executor(SandboxSettings()), DisabledSkillExecutor)
    local = build_skill_executor(SandboxSettings(mode="local"))
    assert isinstance(local, LocalSubprocessSkillExecutor)
    assert local.isolation == "local-subprocess"
    assert "weaker" in local.isolation_note.lower()
    with pytest.raises(NotImplementedError):
        build_skill_executor(SandboxSettings(mode="serverless"))


# --- The process boundary: candidate source runs only in a separate process. ---


async def test_candidate_source_runs_outside_the_harness_process(
    registry: SkillRegistry, recording_store: EpisodeStore
) -> None:
    version = accepted_version(registry)
    connector = ScriptedConnector(
        ScriptedStep(state_changed=True, terminal=True, terminal_reason="goal_reached"),
    )
    runtime = SkillRuntime(LocalSubprocessSkillExecutor(), available=(version,))

    invocation = await invoke(runtime, version, connector, recording_store)

    assert invocation.use.status == "completed", invocation.use.message
    assert invocation.use.isolation == "local-subprocess"
    assert invocation.use.worker_pid is not None
    assert invocation.use.worker_pid != os.getpid()
    assert invocation.use.result is not None
    assert invocation.use.result.status == "succeeded"
    assert invocation.use.result.outputs == {"terminal": True}
    assert invocation.use.success_check_passed is True
    assert invocation.use.primitive_actions_consumed == 1
    assert invocation.use.logs == (
        {"event": "used", "fields": {"object_id": "obj_1", "sequence": 1}},
    )
    assert [step.request.tool_name for step in invocation.steps] == ["use_object"]


async def test_a_skill_that_overruns_its_wall_time_is_inconclusive(
    registry: SkillRegistry, recording_store: EpisodeStore
) -> None:
    version = accepted_version(registry, source=SPINNING_SOURCE, max_wall_time_seconds=1)
    connector = ScriptedConnector()
    runtime = SkillRuntime(LocalSubprocessSkillExecutor(), available=(version,))

    invocation = await invoke(runtime, version, connector, recording_store)

    assert invocation.use.status == "timed_out"
    assert invocation.use.result is None
    assert invocation.use.success_check_passed is None
    assert invocation.use.primitive_actions_consumed == 0
    assert invocation.agent_status == "inconclusive"


async def test_an_unsupported_success_claim_fails_in_the_worker(
    registry: SkillRegistry, recording_store: EpisodeStore
) -> None:
    version = accepted_version(registry, source=BARE_SUCCESS_SOURCE)
    runtime = SkillRuntime(LocalSubprocessSkillExecutor(), available=(version,))

    invocation = await invoke(runtime, version, ScriptedConnector(), recording_store)

    assert invocation.use.status == "failed"
    assert invocation.use.code == "SKILL_INVALID_RESULT"
    assert invocation.use.result is None
    assert invocation.agent_status == "failed"


async def test_the_worker_refuses_imports_outside_the_allowlist(
    registry: SkillRegistry, recording_store: EpisodeStore
) -> None:
    """Runtime import guarding backs up the static policy inside the worker."""
    source = USE_OBJECT_SOURCE.replace(
        "from noob_agent.skills.contract import",
        "import noob_agent.connectors.minecraft\nfrom noob_agent.skills.contract import",
    )
    version = accepted_version(registry, source=source)
    runtime = SkillRuntime(LocalSubprocessSkillExecutor(), available=(version,))

    invocation = await invoke(runtime, version, ScriptedConnector(), recording_store)

    assert invocation.use.status == "failed"
    assert invocation.use.code == "SKILL_LOAD_FAILED"
    assert "not permitted" in invocation.use.message


def test_skill_limits_and_budget_are_public_records() -> None:
    limits = SkillLimits(primitive_actions=3, wall_time_seconds=2.5)
    assert limits.primitive_actions == 3
    assert SkillBudget(primitive_actions=0, wall_time_seconds=0).wall_time_seconds == 0
    execution = SkillExecution(
        status="unavailable",
        code="SANDBOX_DISABLED",
        message="off",
        isolation="none",
        isolation_note="none",
    )
    assert execution.result is None
    assert StepRecord.model_fields  # the nested step type the runtime records
