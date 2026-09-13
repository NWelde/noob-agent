"""The bounded cold-episode runner: budgets, stop reasons, and durable records."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fakes.connector import (
    SCENARIO_ID,
    FakeClock,
    ScriptedConnector,
    ScriptedPolicy,
    ScriptedStep,
)
from pydantic import JsonValue

from noob_agent.connectors import ConnectorLostError
from noob_agent.domain.model import StepResult, ToolRequest
from noob_agent.domain.records import ExperimentRecord
from noob_agent.observability.tracing import (
    EPISODE_OP_NAME,
    STEP_OP_NAME,
    NullTraceSink,
    TraceEvent,
    TraceSink,
    WeaveTraceSink,
    build_trace_sink,
)
from noob_agent.runtime import EpisodeRunner
from noob_agent.settings import TraceSettings
from noob_agent.storage import EpisodeStore

STARTED_AT = datetime(2026, 9, 12, 16, 0, 0, tzinfo=UTC)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(wall=STARTED_AT)


def make_experiment(
    *, decisions: int = 20, primitives: int = 40, wall_ms: int = 180_000
) -> ExperimentRecord:
    return ExperimentRecord(
        experiment_id="exp_0001",
        model_id="scripted-policy",
        condition="cold",
        connector_version="fake-minecraft-0.1.0",
        decision_budget=decisions,
        primitive_budget=primitives,
        wall_time_budget_ms=wall_ms,
        created_at=STARTED_AT,
    )


@pytest.fixture
def prepared_store(store: EpisodeStore) -> EpisodeStore:
    store.create_experiment(make_experiment())
    return store


async def run_episode(
    store: EpisodeStore,
    connector: ScriptedConnector,
    policy: ScriptedPolicy,
    clock: FakeClock,
    experiment: ExperimentRecord | None = None,
    seed: int = 7,
    trace: TraceSink | None = None,
):
    runner = EpisodeRunner(
        connector=connector, store=store, policy=policy, clock=clock, trace=trace
    )
    return await runner.run(
        experiment=experiment or make_experiment(),
        scenario_id=SCENARIO_ID,
        seed=seed,
        split="training",
    )


async def test_a_successful_episode_stops_on_the_terminal_state(
    prepared_store: EpisodeStore, clock: FakeClock
) -> None:
    connector = ScriptedConnector(
        ScriptedStep(),
        ScriptedStep(state_changed=True, terminal=True, terminal_reason="goal_reached"),
    )
    policy = ScriptedPolicy(("observe", {"radius": 8}), ("use_object", {"object_id": "obj_1"}))

    result = await run_episode(prepared_store, connector, policy, clock)

    assert result.stop_reason == "terminal_state"
    assert result.terminal is True
    assert result.decisions_used == 2
    assert result.primitives_used == 2


async def test_every_observation_request_and_result_is_recorded(
    prepared_store: EpisodeStore, clock: FakeClock
) -> None:
    connector = ScriptedConnector(
        ScriptedStep(),
        ScriptedStep(terminal=True, terminal_reason="goal_reached"),
    )
    result = await run_episode(prepared_store, connector, ScriptedPolicy(), clock)

    stored = prepared_store.read_episode(result.episode_id)

    assert stored.episode.scenario_id == SCENARIO_ID
    assert stored.episode.seed == 7
    assert stored.episode.reset_observation.sequence == 0
    assert stored.episode.manifest == await connector.manifest()
    assert [step.sequence for step in stored.steps] == [1, 2]
    assert [step.request.action_id for step in stored.steps] == ["a_0001", "a_0002"]
    assert stored.steps[0].result.observation.last_action_id == "a_0001"
    assert stored.outcome is not None
    assert stored.outcome.stop_reason == "terminal_state"
    assert stored.outcome.total_decisions == 2
    assert stored.outcome.total_primitives == 2


async def test_an_invalid_tool_is_rejected_without_ending_the_episode(
    prepared_store: EpisodeStore, clock: FakeClock
) -> None:
    """A rejected call burns a decision but no primitive, and play continues."""
    connector = ScriptedConnector(ScriptedStep(terminal=True, terminal_reason="goal_reached"))
    policy = ScriptedPolicy(
        ("charge_keystone", {"object_id": "obj_1"}),
        ("observe", {"radius": 8}),
    )

    result = await run_episode(prepared_store, connector, policy, clock)

    assert result.stop_reason == "terminal_state"
    assert result.decisions_used == 2
    assert result.primitives_used == 1

    stored = prepared_store.read_episode(result.episode_id)
    assert stored.steps[0].result.status == "rejected"
    assert stored.steps[0].result.code == "INVALID_TOOL"
    assert stored.steps[0].result.primitive_actions_charged == 0


async def test_the_primitive_budget_stops_the_episode(
    prepared_store: EpisodeStore, clock: FakeClock
) -> None:
    connector = ScriptedConnector(*[ScriptedStep() for _ in range(5)])

    result = await run_episode(
        prepared_store,
        connector,
        ScriptedPolicy(),
        clock,
        experiment=make_experiment(decisions=20, primitives=2),
    )

    assert result.stop_reason == "primitive_limit"
    assert result.primitives_used == 2
    assert len(connector.requests) == 2


async def test_a_primitive_charged_more_than_once_still_respects_the_budget(
    prepared_store: EpisodeStore, clock: FakeClock
) -> None:
    """A skill-like step charging several primitives cannot overrun the budget."""
    connector = ScriptedConnector(ScriptedStep(primitive_actions_charged=3), ScriptedStep())

    result = await run_episode(
        prepared_store,
        connector,
        ScriptedPolicy(),
        clock,
        experiment=make_experiment(decisions=20, primitives=2),
    )

    assert result.stop_reason == "primitive_limit"
    assert result.primitives_used == 3
    assert len(connector.requests) == 1


async def test_the_decision_budget_stops_the_episode(
    prepared_store: EpisodeStore, clock: FakeClock
) -> None:
    connector = ScriptedConnector(*[ScriptedStep(primitive_actions_charged=0) for _ in range(5)])

    result = await run_episode(
        prepared_store,
        connector,
        ScriptedPolicy(),
        clock,
        experiment=make_experiment(decisions=3, primitives=40),
    )

    assert result.stop_reason == "decision_limit"
    assert result.decisions_used == 3
    assert len(connector.requests) == 3


async def test_an_unknown_result_ends_the_episode_and_is_never_retried(
    prepared_store: EpisodeStore, clock: FakeClock
) -> None:
    connector = ScriptedConnector(
        ScriptedStep(),
        ScriptedStep(status="unknown", code="TIMEOUT_UNKNOWN", state_changed=None),
        ScriptedStep(),
    )

    result = await run_episode(prepared_store, connector, ScriptedPolicy(), clock)

    assert result.stop_reason == "unknown_result"
    assert len(connector.requests) == 2, "an unknown action must never be retried"

    stored = prepared_store.read_episode(result.episode_id)
    assert stored.steps[-1].result.status == "unknown"


async def test_three_identical_failures_without_a_state_change_end_the_episode(
    prepared_store: EpisodeStore, clock: FakeClock
) -> None:
    connector = ScriptedConnector(
        *[
            ScriptedStep(status="failed", code="PRECONDITION_FAILED", state_changed=False)
            for _ in range(6)
        ]
    )

    result = await run_episode(prepared_store, connector, ScriptedPolicy(), clock)

    assert result.stop_reason == "repeated_failure"
    assert len(connector.requests) == 3


async def test_a_state_change_between_failures_resets_the_streak(
    prepared_store: EpisodeStore, clock: FakeClock
) -> None:
    """Only failures with no intervening public state change count."""
    failed = ScriptedStep(status="failed", code="PRECONDITION_FAILED", state_changed=False)
    connector = ScriptedConnector(
        failed,
        failed,
        ScriptedStep(status="failed", code="PRECONDITION_FAILED", state_changed=True),
        failed,
        failed,
        ScriptedStep(terminal=True, terminal_reason="goal_reached"),
    )

    result = await run_episode(prepared_store, connector, ScriptedPolicy(), clock)

    assert result.stop_reason == "terminal_state"
    assert len(connector.requests) == 6


async def test_differing_failed_calls_do_not_count_as_repeated(
    prepared_store: EpisodeStore, clock: FakeClock
) -> None:
    failed = ScriptedStep(status="failed", code="PRECONDITION_FAILED", state_changed=False)
    connector = ScriptedConnector(failed, failed, failed, failed)
    policy = ScriptedPolicy(
        ("observe", {"radius": 8}),
        ("observe", {"radius": 4}),
        ("observe", {"radius": 2}),
        ("observe", {"radius": 1}),
    )

    result = await run_episode(
        prepared_store,
        connector,
        policy,
        clock,
        experiment=make_experiment(decisions=4, primitives=40),
    )

    assert result.stop_reason == "decision_limit"


async def test_a_lost_connector_ends_the_episode(
    prepared_store: EpisodeStore, clock: FakeClock
) -> None:
    connector = ScriptedConnector(ScriptedStep(), ScriptedStep(raises=True))

    result = await run_episode(prepared_store, connector, ScriptedPolicy(), clock)

    assert result.stop_reason == "connector_lost"

    stored = prepared_store.read_episode(result.episode_id)
    assert stored.outcome is not None
    assert stored.outcome.stop_reason == "connector_lost"
    assert len(stored.steps) == 1, "a lost step produced no durable result"


async def test_a_connector_lost_result_code_ends_the_episode(
    prepared_store: EpisodeStore, clock: FakeClock
) -> None:
    connector = ScriptedConnector(
        ScriptedStep(status="failed", code="CONNECTOR_LOST", state_changed=None)
    )

    result = await run_episode(prepared_store, connector, ScriptedPolicy(), clock)

    assert result.stop_reason == "connector_lost"


async def test_the_wall_time_budget_stops_the_episode(prepared_store: EpisodeStore) -> None:
    clock = FakeClock(wall=STARTED_AT, increments=[0, 500, 5_000])
    connector = ScriptedConnector(*[ScriptedStep() for _ in range(5)])

    result = await run_episode(
        prepared_store,
        connector,
        ScriptedPolicy(),
        clock,
        experiment=make_experiment(wall_ms=1_000),
    )

    assert result.stop_reason == "wall_time_limit"


async def test_the_connector_is_closed_even_when_it_is_lost(
    prepared_store: EpisodeStore, clock: FakeClock
) -> None:
    connector = ScriptedConnector(ScriptedStep(raises=True))

    await run_episode(prepared_store, connector, ScriptedPolicy(), clock)

    assert connector.closed is True


async def test_the_same_seed_resets_to_the_same_public_world(
    database_path: Path, clock: FakeClock
) -> None:
    """Reset determinism: identical seeds give identical recorded resets."""
    with EpisodeStore.open(database_path) as setup:
        setup.create_experiment(make_experiment())

    observations = []
    for episode_id in ("ep_a", "ep_b"):
        with EpisodeStore.open(database_path) as store:
            connector = ScriptedConnector(
                ScriptedStep(terminal=True, terminal_reason="goal_reached"),
                episode_id=episode_id,
            )
            result = await run_episode(store, connector, ScriptedPolicy(), clock, seed=7)
            stored = store.read_episode(result.episode_id)
            observations.append(stored.episode.reset_observation.model_dump(exclude={"episode_id"}))

    assert observations[0] == observations[1]


async def test_a_different_seed_resets_to_a_different_public_world(
    prepared_store: EpisodeStore, clock: FakeClock
) -> None:
    connector_a = ScriptedConnector(ScriptedStep(terminal=True), episode_id="ep_a")
    connector_b = ScriptedConnector(ScriptedStep(terminal=True), episode_id="ep_b")

    first = await run_episode(prepared_store, connector_a, ScriptedPolicy(), clock, seed=7)
    second = await run_episode(prepared_store, connector_b, ScriptedPolicy(), clock, seed=8)

    reset_a = prepared_store.read_episode(first.episode_id).episode.reset_observation
    reset_b = prepared_store.read_episode(second.episode_id).episode.reset_observation
    assert reset_a.model_dump(exclude={"episode_id"}) != reset_b.model_dump(exclude={"episode_id"})


async def test_the_runner_reads_the_episode_id_from_the_reset_observation(
    prepared_store: EpisodeStore, clock: FakeClock
) -> None:
    """The connector owns episode identity, exactly as the contract says."""
    connector = ScriptedConnector(ScriptedStep(terminal=True), episode_id="ep_from_connector")

    result = await run_episode(prepared_store, connector, ScriptedPolicy(), clock)

    assert result.episode_id == "ep_from_connector"
    assert connector.reset_calls == [(SCENARIO_ID, 7)]


async def test_the_recorded_episode_carries_no_private_state(
    prepared_store: EpisodeStore, clock: FakeClock
) -> None:
    """The runner only ever sees the public connector surface, so the record is public."""
    connector = ScriptedConnector(
        ScriptedStep(),
        ScriptedStep(terminal=True, terminal_reason="goal_reached"),
    )

    result = await run_episode(prepared_store, connector, ScriptedPolicy(), clock)

    stored = prepared_store.read_episode(result.episode_id)
    recorded = stored.model_dump_json().lower()
    for token in ("grader", "faulty", "held_out", "ground_truth", "secret"):
        assert token not in recorded


async def test_a_non_connector_error_propagates(
    prepared_store: EpisodeStore, clock: FakeClock
) -> None:
    """Only connector loss is absorbed; every other fault must surface."""

    class BrokenConnector(ScriptedConnector):
        async def step(self, request: object) -> object:
            raise ValueError("something else went wrong")

    connector = BrokenConnector(ScriptedStep())

    with pytest.raises(ValueError):
        await run_episode(prepared_store, connector, ScriptedPolicy(), clock)

    assert connector.closed is True, "the connector must still be closed"


def test_connector_loss_is_a_distinct_error_type() -> None:
    assert issubclass(ConnectorLostError, Exception)
    assert not issubclass(ConnectorLostError, ValueError)


class MismatchedActionIdConnector(ScriptedConnector):
    """Violates the contract: the result's action_id is not the request's."""

    async def step(self, request: ToolRequest) -> StepResult:
        return await super().step(
            ToolRequest(action_id="a_bogus", tool_name=request.tool_name, arguments={})
        )


class RepeatedSequenceConnector(ScriptedConnector):
    """Violates the contract: reuses a sequence number."""

    async def step(self, request: ToolRequest) -> StepResult:
        result = await super().step(request)
        self._sequence = 1
        return result


class SkippedSequenceConnector(ScriptedConnector):
    """Violates the contract: jumps the sequence forward."""

    async def step(self, request: ToolRequest) -> StepResult:
        self._sequence += 10
        return await super().step(request)


@pytest.mark.parametrize(
    "connector_class",
    [MismatchedActionIdConnector, RepeatedSequenceConnector, SkippedSequenceConnector],
)
async def test_a_contract_violating_connector_still_finalizes_the_episode(
    connector_class: type[ScriptedConnector], prepared_store: EpisodeStore, clock: FakeClock
) -> None:
    """A result that cannot be durably recorded ends the episode, it does not crash it.

    The runner's whole promise is a bounded episode that always carries a stop
    reason. A misbehaving connector must not be able to leave an episode row
    with no outcome in the source of truth.
    """
    connector = connector_class(*[ScriptedStep() for _ in range(4)])

    result = await run_episode(prepared_store, connector, ScriptedPolicy(), clock)

    assert result.stop_reason == "unknown_result"

    stored = prepared_store.read_episode(result.episode_id)
    assert stored.outcome is not None, "the episode was left unfinalized"
    assert stored.outcome.stop_reason == "unknown_result"


async def test_a_failing_close_does_not_mask_the_original_error(
    prepared_store: EpisodeStore, clock: FakeClock
) -> None:
    """The cause must survive a connector whose teardown also fails."""

    class DoublyBrokenConnector(ScriptedConnector):
        async def step(self, request: ToolRequest) -> StepResult:
            raise ValueError("the real cause")

        async def close(self) -> None:
            raise RuntimeError("teardown also failed")

    connector = DoublyBrokenConnector(ScriptedStep())

    with pytest.raises(ValueError, match="the real cause"):
        await run_episode(prepared_store, connector, ScriptedPolicy(), clock)


async def test_a_failing_close_surfaces_when_nothing_else_went_wrong(
    prepared_store: EpisodeStore, clock: FakeClock
) -> None:
    """A close failure is only swallowed to protect a real cause, never hidden outright."""

    class CloseFailsConnector(ScriptedConnector):
        async def close(self) -> None:
            raise RuntimeError("teardown failed")

    connector = CloseFailsConnector(ScriptedStep(terminal=True, terminal_reason="goal_reached"))

    with pytest.raises(RuntimeError, match="teardown failed"):
        await run_episode(prepared_store, connector, ScriptedPolicy(), clock)


async def test_an_explicit_external_owner_can_keep_the_connector_open(
    prepared_store: EpisodeStore, clock: FakeClock
) -> None:
    connector = ScriptedConnector(
        ScriptedStep(terminal=True, terminal_reason="goal_reached")
    )
    runner = EpisodeRunner(
        connector=connector,
        store=prepared_store,
        policy=ScriptedPolicy(),
        clock=clock,
        close_connector=False,
    )

    result = await runner.run(
        experiment=make_experiment(), scenario_id=SCENARIO_ID, seed=7
    )

    assert result.stop_reason == "terminal_state"
    assert connector.closed is False
    await connector.close()


async def test_cancellation_finalizes_an_open_episode_before_it_propagates(
    prepared_store: EpisodeStore, clock: FakeClock
) -> None:
    started = asyncio.Event()
    never = asyncio.Event()

    class BlockingPolicy:
        async def choose(self, observation: object) -> ToolRequest:
            del observation
            started.set()
            await never.wait()
            raise AssertionError("the blocking policy unexpectedly resumed")

    connector = ScriptedConnector(episode_id="ep_cancelled")
    runner = EpisodeRunner(
        connector=connector,
        store=prepared_store,
        policy=BlockingPolicy(),
        clock=clock,
    )
    task = asyncio.create_task(
        runner.run(experiment=make_experiment(), scenario_id=SCENARIO_ID, seed=7)
    )
    await started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    stored = prepared_store.read_episode("ep_cancelled")
    assert stored.outcome is not None
    assert stored.outcome.stop_reason == "unknown_result"
    assert (stored.outcome.total_decisions, stored.outcome.total_primitives) == (0, 0)
    assert connector.closed is True


class RecordingTraceSink:
    """Captures the events the runner mirrors, in the order it mirrors them."""

    def __init__(self) -> None:
        self.events: list[TraceEvent] = []
        self.flushed = 0

    def record(self, event: TraceEvent) -> None:
        self.events.append(event)

    def flush(self) -> None:
        self.flushed += 1

    @property
    def names(self) -> list[str]:
        return [event.name for event in self.events]


class BrokenTraceSink:
    """A mirror that always fails. Tracing is best effort and must never win."""

    def record(self, event: TraceEvent) -> None:
        raise RuntimeError("the trace backend is unreachable")

    def flush(self) -> None:
        raise RuntimeError("the trace backend is unreachable")


class DurabilityCheckingTraceSink:
    """Asserts, for every event, that the local record is already durable."""

    def __init__(self, store: EpisodeStore) -> None:
        self._store = store
        self.durable_at_event: list[tuple[str, bool]] = []

    def record(self, event: TraceEvent) -> None:
        episode_id = event.attributes["episode_id"]
        assert isinstance(episode_id, str)
        try:
            stored = self._store.read_episode(episode_id)
        except Exception:  # pragma: no cover - a missing episode is the failure
            self.durable_at_event.append((event.name, False))
            return
        if event.name == "episode.step":
            sequence = event.attributes["sequence"]
            durable = any(step.sequence == sequence for step in stored.steps)
        elif event.name == "episode.finished":
            durable = stored.outcome is not None
        else:
            durable = True
        self.durable_at_event.append((event.name, durable))

    def flush(self) -> None:
        return None


@dataclass
class FakeWeaveCall:
    """Stands in for the call object a Weave client hands back."""

    op_name: str
    inputs: dict[str, JsonValue]
    parent: FakeWeaveCall | None
    output: dict[str, JsonValue] | None = None


@dataclass
class FakeWeaveClient:
    """A Weave client shaped like the real one, with no network and no import."""

    calls: list[FakeWeaveCall] = field(default_factory=list)

    def create_call(
        self,
        op: str,
        inputs: dict[str, JsonValue],
        parent: object | None = None,
        /,
    ) -> FakeWeaveCall:
        assert parent is None or isinstance(parent, FakeWeaveCall)
        call = FakeWeaveCall(op_name=op, inputs=dict(inputs), parent=parent)
        self.calls.append(call)
        return call

    def finish_call(self, call: object, output: dict[str, JsonValue] | None = None, /) -> None:
        assert isinstance(call, FakeWeaveCall)
        call.output = None if output is None else dict(output)


def test_tracing_is_disabled_by_default_and_never_imports_weave() -> None:
    """The default configuration mirrors nothing and requires no optional package."""
    sink = build_trace_sink(TraceSettings())

    assert isinstance(sink, NullTraceSink)
    assert "weave" not in sys.modules


def test_an_enabled_trace_setting_builds_a_weave_mirror() -> None:
    client = FakeWeaveClient()
    sink = build_trace_sink(
        TraceSettings(mode="weave", weave_disabled=False),
        client_factory=lambda project: client,
    )

    sink.record(TraceEvent(name="episode.started", attributes={"episode_id": "ep_0001"}))

    assert client.calls[0].op_name == EPISODE_OP_NAME


async def test_the_episode_and_every_step_are_mirrored_in_order(
    prepared_store: EpisodeStore, clock: FakeClock
) -> None:
    connector = ScriptedConnector(
        ScriptedStep(),
        ScriptedStep(state_changed=True, terminal=True, terminal_reason="goal_reached"),
    )
    sink = RecordingTraceSink()

    result = await run_episode(prepared_store, connector, ScriptedPolicy(), clock, trace=sink)

    assert sink.names == [
        "episode.started",
        "episode.step",
        "episode.step",
        "episode.finished",
    ]
    assert sink.flushed == 1
    assert [event.attributes["episode_id"] for event in sink.events] == [result.episode_id] * 4
    assert [event.attributes["action_id"] for event in sink.events[1:3]] == ["a_0001", "a_0002"]
    assert sink.events[-1].attributes["stop_reason"] == "terminal_state"
    assert sink.events[-1].attributes["total_decisions"] == 2


async def test_every_mirrored_event_follows_its_durable_local_record(
    prepared_store: EpisodeStore, clock: FakeClock
) -> None:
    """SQLite is authoritative: nothing is mirrored before the row is committed."""
    connector = ScriptedConnector(
        ScriptedStep(),
        ScriptedStep(terminal=True, terminal_reason="goal_reached"),
    )
    sink = DurabilityCheckingTraceSink(prepared_store)

    await run_episode(prepared_store, connector, ScriptedPolicy(), clock, trace=sink)

    assert sink.durable_at_event == [
        ("episode.started", True),
        ("episode.step", True),
        ("episode.step", True),
        ("episode.finished", True),
    ]


async def test_a_step_the_store_refuses_is_never_mirrored(
    prepared_store: EpisodeStore, clock: FakeClock
) -> None:
    connector = MismatchedActionIdConnector(*[ScriptedStep() for _ in range(2)])
    sink = RecordingTraceSink()

    result = await run_episode(prepared_store, connector, ScriptedPolicy(), clock, trace=sink)

    assert result.stop_reason == "unknown_result"
    assert sink.names == ["episode.started", "episode.finished"]


async def test_a_failing_trace_mirror_does_not_affect_the_episode(
    prepared_store: EpisodeStore, clock: FakeClock
) -> None:
    """Tracing is best effort: a broken mirror cannot change the recorded attempt."""
    connector = ScriptedConnector(ScriptedStep(terminal=True, terminal_reason="goal_reached"))

    result = await run_episode(
        prepared_store, connector, ScriptedPolicy(), clock, trace=BrokenTraceSink()
    )

    assert result.stop_reason == "terminal_state"
    stored = prepared_store.read_episode(result.episode_id)
    assert stored.outcome is not None
    assert len(stored.steps) == 1


async def test_the_weave_mirror_nests_steps_under_one_episode_call(
    prepared_store: EpisodeStore, clock: FakeClock
) -> None:
    """BDP criterion 4: the attempt reads as one nested trace, not a flat log."""
    connector = ScriptedConnector(
        ScriptedStep(),
        ScriptedStep(terminal=True, terminal_reason="goal_reached"),
    )
    client = FakeWeaveClient()
    sink = build_trace_sink(
        TraceSettings(mode="weave", weave_disabled=False),
        client_factory=lambda project: client,
    )

    result = await run_episode(prepared_store, connector, ScriptedPolicy(), clock, trace=sink)

    episode_call, *step_calls = client.calls
    assert episode_call.op_name == EPISODE_OP_NAME
    assert episode_call.parent is None
    assert episode_call.inputs["episode_id"] == result.episode_id
    assert [call.op_name for call in step_calls] == [STEP_OP_NAME, STEP_OP_NAME]
    assert all(call.parent is episode_call for call in step_calls)
    assert [call.output is not None for call in step_calls] == [True, True]
    assert episode_call.output is not None
    assert episode_call.output["stop_reason"] == "terminal_state"


def test_a_weave_mirror_survives_a_client_that_misbehaves() -> None:
    """A client whose API does not match degrades to no tracing, not a crash."""

    class HostileClient:
        def create_call(self, *args: object, **kwargs: object) -> object:
            raise TypeError("unexpected signature")

        def finish_call(self, *args: object, **kwargs: object) -> None:
            raise TypeError("unexpected signature")

    sink = WeaveTraceSink(HostileClient())

    sink.record(TraceEvent(name="episode.started", attributes={"episode_id": "ep_0001"}))
    sink.record(TraceEvent(name="episode.step", attributes={"episode_id": "ep_0001"}))
    sink.record(TraceEvent(name="episode.finished", attributes={"episode_id": "ep_0001"}))
    sink.flush()
