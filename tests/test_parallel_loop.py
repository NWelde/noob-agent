"""Step 22.E: held-out cells and validation executions run concurrently, with identical records."""

from __future__ import annotations

import asyncio
import itertools
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from fakes.connector import FakeClock, ScriptedConnector, ScriptedStep
from test_loop_bench import CANDIDATE

from noob_agent.models.client import ModelRequest, ModelResponse
from noob_agent.observability.tracing import (
    EPISODE_OP_NAME,
    MODEL_CALL_OP_NAME,
    STEP_OP_NAME,
    WeaveTraceSink,
)
from noob_agent.prompts.builder import BUILDER_SYSTEM
from noob_agent.runtime.sequence import HeldOutCell, LearningSequence
from noob_agent.skills.executor import LocalSubprocessSkillExecutor
from noob_agent.skills.registry import SkillRegistry
from noob_agent.storage import EpisodeStore

STARTED_AT = datetime(2026, 9, 13, 7, 0, tzinfo=UTC)
CELLS = tuple(HeldOutCell(f"held-{index}", 100 + index) for index in range(4))


class SlowRouting:
    """Every Action call yields to the event loop, so concurrent cells really interleave."""

    provider = "scripted"

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []
        self.active = 0
        self.peak = 0

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if request.system == BUILDER_SYSTEM:
            return ModelResponse(text=CANDIDATE, input_tokens=1, output_tokens=1, model_id="f")
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        decision = {
            "subgoal": request.prompt.split("sequence ", 1)[-1][:12],
            "expected_evidence": "e",
            "action": "observe",
            "arguments": {},
            "finding": None,
        }
        return ModelResponse(
            text=json.dumps(decision), input_tokens=5, output_tokens=5, model_id="f"
        )


class Factory:
    def __init__(self) -> None:
        self.ids = itertools.count(1)

    def __call__(self) -> ScriptedConnector:
        # One observe, then terminal: two decisions per episode.
        return ScriptedConnector(
            ScriptedStep(),
            ScriptedStep(terminal=True, terminal_reason="done"),
            episode_id=f"ep_par_{next(self.ids):03d}",
        )


class RecordingWeave:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], object | None]] = []
        self.finished: list[object] = []

    def create_call(self, op: str, inputs: dict[str, Any], parent: object | None = None) -> object:
        handle = (op, len(self.calls))
        self.calls.append((op, inputs, parent))
        return handle

    def finish_call(self, call: object, output: dict[str, Any] | None = None) -> None:
        self.finished.append(call)


async def _run(
    store: EpisodeStore, *, concurrency: int, trace: Any = None
) -> tuple[Any, SlowRouting]:
    client = SlowRouting()
    result = await LearningSequence(
        connector_factory=Factory(),
        client=client,
        model_id="f",
        store=store,
        registry=SkillRegistry(),
        executor=LocalSubprocessSkillExecutor(),
        grade=lambda stored, connector: stored.outcome is not None,
        clock=FakeClock(wall=STARTED_AT),
        trace=trace,
        heldout_concurrency=concurrency,
    ).run(
        sequence_id=f"par-{concurrency}",
        training_scenario_id="train",
        training_seed=1,
        heldout=CELLS,
    )
    return result, client


async def test_concurrent_cells_really_overlap_and_keep_report_order(store: EpisodeStore) -> None:
    result, client = await _run(store, concurrency=4)

    assert client.peak > 1
    assert [(e.scenario_id, e.seed) for e in result.heldout] == [
        (c.scenario_id, c.seed) for c in CELLS
    ]
    assert all(e.grade is True for e in result.heldout)


async def test_every_model_call_is_recorded_against_its_own_episode(store: EpisodeStore) -> None:
    result, _ = await _run(store, concurrency=4)

    for episode in result.heldout:
        stored = store.read_episode(episode.episode_id)
        calls = store.read_model_calls(episode_id=episode.episode_id)
        assert [call.action_id for call in calls] == [
            step.request.action_id for step in stored.steps
        ]
        assert all(call.purpose == "action" for call in calls)


async def test_concurrency_one_records_the_same_as_before(store: EpisodeStore) -> None:
    result, client = await _run(store, concurrency=1)

    assert client.peak == 1
    assert len(result.heldout) == len(CELLS)
    for episode in result.heldout:
        assert len(store.read_model_calls(episode_id=episode.episode_id)) == 2


async def test_concurrent_episodes_nest_their_own_trace_calls(store: EpisodeStore) -> None:
    weave = RecordingWeave()
    result, _ = await _run(store, concurrency=4, trace=WeaveTraceSink(weave))

    handles = {
        inputs["episode_id"]: (op, index)
        for index, (op, inputs, _) in enumerate(weave.calls)
        if op == EPISODE_OP_NAME
    }
    for op, inputs, parent in weave.calls:
        if op in (STEP_OP_NAME, MODEL_CALL_OP_NAME) and inputs.get("purpose", "action") == "action":
            assert parent == handles[inputs["episode_id"]]
    assert set(handles) >= {episode.episode_id for episode in result.heldout}
    assert all(handle in weave.finished for handle in handles.values())


def test_persistence_and_concurrency_are_refused_together(store: EpisodeStore) -> None:
    with pytest.raises(ValueError, match="persistent"):
        LearningSequence(
            connector_factory=Factory(),
            client=SlowRouting(),
            model_id="f",
            store=store,
            registry=SkillRegistry(),
            executor=LocalSubprocessSkillExecutor(),
            grade=lambda stored, connector: None,
            persistent_connector=True,
            heldout_concurrency=2,
        )


def test_concurrency_must_be_positive(store: EpisodeStore) -> None:
    with pytest.raises(ValueError):
        LearningSequence(
            connector_factory=Factory(),
            client=SlowRouting(),
            model_id="f",
            store=store,
            registry=SkillRegistry(),
            executor=LocalSubprocessSkillExecutor(),
            grade=lambda stored, connector: None,
            heldout_concurrency=0,
        )


# --- Validation ------------------------------------------------------------------


MISSING_TARGET_CLAIMS_SUCCESS = """\
from noob_agent.skills.contract import EvidenceRef, SkillContext, SkillResult


async def run(context: SkillContext, inputs: dict[str, object]) -> SkillResult:
    if inputs:
        return SkillResult(status="failed", summary="Unexpected input.", primitive_actions_used=0)
    observation = await context.observe()
    return SkillResult(
        status="succeeded",
        summary="Assumed done.",
        evidence=(EvidenceRef(kind="observation_sequence", value=str(observation.sequence)),),
        primitive_actions_used=0,
    )
"""


@pytest.mark.parametrize(
    "source_name",
    [
        "VALID_SOURCE",
        "NONEXISTENT_API_SOURCE",
        "MISSING_TARGET_CLAIMS_SUCCESS",
    ],
)
async def test_concurrent_validation_reaches_the_same_verdict_and_first_failure(
    episode: Any, step_factory: Any, source_name: str
) -> None:
    import test_skill_validation_pipeline as pipeline

    from noob_agent.domain.records import StoredEpisode
    from noob_agent.skills import SkillValidationError
    from noob_agent.verification.validator import validate_candidate

    source = getattr(pipeline, source_name, None) or globals()[source_name]
    trace = StoredEpisode(episode=episode, steps=(step_factory(1),))

    async def verdict(concurrency: int) -> object:
        try:
            report = await validate_candidate(
                source,
                json.dumps(pipeline.METADATA),
                known_primitive_names={"observe", "use_object"},
                training_trace=trace,
                executor=LocalSubprocessSkillExecutor(),
                concurrency=concurrency,
            )
        except SkillValidationError as error:
            first = error.issues[0]
            return ("rejected", first.check, first.code, first.fixture)
        return ("accepted", report.checks, report.variation_object_ids)

    assert await verdict(1) == await verdict(8)


class FlushWatchingSink:
    """A real Weave client's flush blocks the event loop until in-flight calls finish,
    which deadlocks when another episode's model call is still awaiting its reply."""

    def __init__(self) -> None:
        self.open: set[str] = set()
        self.open_at_flush: list[int] = []

    def record(self, event: Any) -> None:
        episode_id = event.attributes.get("episode_id")
        if event.name == "episode.started":
            self.open.add(episode_id)
        elif event.name == "episode.finished":
            self.open.discard(episode_id)

    def flush(self) -> None:
        self.open_at_flush.append(len(self.open))


async def test_no_trace_flush_happens_while_another_episode_is_running(
    store: EpisodeStore,
) -> None:
    sink = FlushWatchingSink()

    await _run(store, concurrency=4, trace=sink)

    assert sink.open_at_flush and all(count == 0 for count in sink.open_at_flush)
