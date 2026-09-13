"""Model call records: every Action, Build, and Repair call, persisted and traced.

This is step 18a of `hackathon_plan.md` section 18. A record is written once,
after its call returns or fails, and it never reaches a prompt, an
observation, evidence selection, or a grade. Nothing here reaches a network
service or reads a credential from the environment.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fakes.connector import FakeClock, ScriptedConnector, ScriptedStep
from pydantic import ValidationError

from noob_agent.agents.action import UNUSABLE_REPLY_TOOL, ActionAgent
from noob_agent.domain.records import (
    EpisodeRecord,
    ExperimentRecord,
    ModelCallRecord,
    StoredEpisode,
)
from noob_agent.models.client import ModelRequest, ModelResponse, WandbInferenceClient
from noob_agent.models.recording import RecordingModelClient
from noob_agent.observability.tracing import (
    MODEL_CALL,
    MODEL_CALL_OP_NAME,
    TraceEvent,
    WeaveTraceSink,
    model_call_event,
)
from noob_agent.prompts.builder import BUILDER_SYSTEM
from noob_agent.runtime.runner import EpisodeRunner
from noob_agent.runtime.sequence import HeldOutCell, LearningSequence, LearningSequenceResult
from noob_agent.settings import ModelSettings, WandbSettings
from noob_agent.skills.executor import LocalSubprocessSkillExecutor
from noob_agent.skills.registry import SkillRegistry
from noob_agent.storage import EpisodeStore
from noob_agent.storage.schema import SCHEMA_VERSION

STARTED_AT = datetime(2026, 9, 12, 23, 0, 0, tzinfo=UTC)
TRAINING = "mc_training_room"
HELDOUT_CELLS = (HeldOutCell("mc_layout_a", 11), HeldOutCell("mc_layout_b", 12))
SKILL_NAME = "record_visible_state"
REASONING_MARK = "PRIVATE-REASONING"
SECRET = "wandb-test-secret-0123456789"

SKILL_SOURCE = '''"""Record one fresh public observation."""

from noob_agent.skills.contract import EvidenceRef, SkillContext, SkillResult


async def run(context: SkillContext, inputs: dict[str, object]) -> SkillResult:
    """Record one fresh public observation without changing the environment."""
    observation = await context.observe()
    return SkillResult(
        status="inconclusive",
        summary="Recorded the current public state.",
        evidence=(EvidenceRef(kind="observation_sequence", value=str(observation.sequence)),),
        outputs={"terminal": observation.terminal},
        primitive_actions_used=0,
    )
'''


def _candidate(source: str = SKILL_SOURCE) -> str:
    metadata = {
        "name": SKILL_NAME,
        "version": 1,
        "parent_version": None,
        "purpose": "Record one fresh public observation without changing the environment.",
        "input_schema": {"properties": {}},
        "required_tools": ["observe"],
        "max_primitive_actions": 1,
        "max_wall_time_seconds": 5,
        "success_claim": "The result cites the newly observed public sequence.",
        "api_version": "noob-agent.skill.v1",
    }
    return f"```python\n{source}```\n\n```json\n{json.dumps(metadata, indent=2)}\n```\n"


# Parses, but imports a forbidden module, so the static policy rejects it and
# the Builder spends its one repair.
REJECTED_CANDIDATE = _candidate("import os\n" + SKILL_SOURCE)
ACCEPTABLE_CANDIDATE = _candidate()


class RoutingModelClient:
    """Scripted Builder replies; every Action reply is one `observe` decision.

    Each reply carries provider reasoning with a unique marker, which exists only
    in the call record and must never reach a later prompt.
    """

    provider = "fake-provider"

    def __init__(self, *builder_replies: str) -> None:
        self._builder_replies = list(builder_replies)
        self._counter = itertools.count(1)
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        index = next(self._counter)
        if request.system == BUILDER_SYSTEM:
            text = self._builder_replies.pop(0)
        else:
            text = json.dumps(
                {
                    "subgoal": "Look around.",
                    "expected_evidence": "A fresh observation.",
                    "tool": "observe",
                    "arguments": {},
                }
            )
        return ModelResponse(
            text=text,
            input_tokens=100 + index,
            output_tokens=40 + index,
            model_id="fake-model-exact",
            finish_reason="stop",
            reasoning=f"{REASONING_MARK}-{index:03d}",
            usage_reported=True,
        )


class ScriptedResponseClient:
    """Returns prepared responses in order, or raises a prepared error."""

    provider = "fake-provider"

    def __init__(self, *responses: ModelResponse | Exception) -> None:
        self._responses = list(responses)
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        item = self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
        if isinstance(item, Exception):
            raise item
        return item


class CapturingSink:
    """Records mirrored events, checking the store already holds each call."""

    def __init__(self, store: EpisodeStore | None = None) -> None:
        self._store = store
        self.events: list[TraceEvent] = []
        self.durable_when_mirrored: list[bool] = []

    def record(self, event: TraceEvent) -> None:
        self.events.append(event)
        if self._store is not None and event.name == MODEL_CALL:
            call_ids = {record.call_id for record in self._store.read_model_calls()}
            self.durable_when_mirrored.append(event.attributes["call_id"] in call_ids)

    def flush(self) -> None:
        return None


class FailingSink:
    def record(self, event: TraceEvent) -> None:
        raise RuntimeError("The trace backend is unreachable.")

    def flush(self) -> None:
        raise RuntimeError("The trace backend is unreachable.")


class ConnectorFactory:
    def __init__(self) -> None:
        self.created: list[ScriptedConnector] = []

    def __call__(self) -> ScriptedConnector:
        index = len(self.created) + 1
        if index == 1:
            script = (ScriptedStep(), ScriptedStep(terminal=True, terminal_reason="done"))
        else:
            script = (ScriptedStep(terminal=True, terminal_reason="done"),)
        connector = ScriptedConnector(*script, episode_id=f"ep_calls_{index:04d}")
        self.created.append(connector)
        return connector


def _grade(stored: StoredEpisode, connector: ScriptedConnector) -> str:
    del connector
    return stored.episode.episode_id


async def _run_sequence(
    store: EpisodeStore, client: RoutingModelClient, trace: CapturingSink | None = None
) -> LearningSequenceResult[str]:
    sequence = LearningSequence(
        connector_factory=ConnectorFactory(),
        client=client,
        model_id="fake-model",
        store=store,
        registry=SkillRegistry(),
        executor=LocalSubprocessSkillExecutor(),
        grade=_grade,
        clock=FakeClock(wall=STARTED_AT),
        trace=trace,
    )
    return await sequence.run(
        sequence_id="seq_calls",
        training_scenario_id=TRAINING,
        training_seed=7,
        heldout=HELDOUT_CELLS,
    )


def _response(**overrides: Any) -> ModelResponse:
    values: dict[str, Any] = {
        "text": "a reply",
        "input_tokens": 120,
        "output_tokens": 30,
        "model_id": "fake-model-exact",
        "finish_reason": "stop",
        "reasoning": None,
        "usage_reported": True,
    }
    values.update(overrides)
    return ModelResponse(**values)


BUILD_REQUEST = ModelRequest(system=BUILDER_SYSTEM, prompt="Write a skill.", max_output_tokens=64)


def _builder_recorder(
    store: EpisodeStore, inner: Any, *, trace: Any = None
) -> RecordingModelClient:
    return RecordingModelClient(
        inner,
        store=store,
        experiment_id="exp_0001",
        role="builder",
        model_id="fake-model",
        episode_id="ep_0001",
        clock=FakeClock(wall=STARTED_AT, increments=[5, 42]),
        trace=trace,
    )


# --- Every call in a sequence writes exactly one linked record -----------------


async def test_every_action_build_and_repair_call_writes_one_linked_record(
    store: EpisodeStore,
) -> None:
    client = RoutingModelClient(REJECTED_CANDIDATE, ACCEPTABLE_CANDIDATE)

    result = await _run_sequence(store, client)

    assert result.builder.accepted is True
    records = store.read_model_calls()
    assert len(records) == len(client.requests)
    assert len({record.call_id for record in records}) == len(records)

    training_id = result.training_experiment.experiment_id
    heldout_id = result.heldout_experiment.experiment_id
    training_episode = result.training.episode_id
    heldout_episodes = [episode.episode_id for episode in result.heldout]

    purposes = [(r.purpose, r.experiment_id, r.episode_id) for r in records]
    assert purposes == [
        ("action", training_id, training_episode),
        ("action", training_id, training_episode),
        ("build", training_id, training_episode),
        ("repair", training_id, training_episode),
        ("action", heldout_id, heldout_episodes[0]),
        ("action", heldout_id, heldout_episodes[1]),
    ]

    for episode_id in [training_episode, *heldout_episodes]:
        stored = store.read_episode(episode_id)
        action_records = [
            r for r in store.read_model_calls(episode_id=episode_id) if r.purpose == "action"
        ]
        assert [r.action_id for r in action_records] == [
            step.request.action_id for step in stored.steps
        ]
    assert all(r.action_id is None for r in records if r.purpose != "action")

    for record, request in zip(records, client.requests, strict=True):
        assert record.provider == "fake-provider"
        assert record.model_id == "fake-model-exact"
        assert record.system == request.system
        assert record.prompt == request.prompt
        assert record.max_output_tokens == request.max_output_tokens
        assert record.temperature == request.temperature
        assert record.finish_reason == "stop"
        assert record.reasoning is not None and record.reasoning.startswith(REASONING_MARK)
        assert record.input_tokens is not None and record.output_tokens is not None
        assert record.error is None


async def test_calls_can_be_read_back_by_experiment_and_by_episode(store: EpisodeStore) -> None:
    result = await _run_sequence(store, RoutingModelClient(ACCEPTABLE_CANDIDATE))

    heldout = store.read_model_calls(experiment_id=result.heldout_experiment.experiment_id)
    assert [r.episode_id for r in heldout] == [e.episode_id for e in result.heldout]
    training = store.read_model_calls(episode_id=result.training.episode_id)
    assert [r.purpose for r in training] == ["action", "action", "build"]


# --- An empty reply cut off by the cap ---------------------------------------


async def test_a_length_stopped_empty_reply_is_recorded_and_charged_as_before(
    store: EpisodeStore, experiment: ExperimentRecord
) -> None:
    short = experiment.model_copy(update={"decision_budget": 2})
    store.create_experiment(short)
    inner = ScriptedResponseClient(
        _response(text="", finish_reason="length", reasoning="Thinking about the lantern...")
    )
    connector = ScriptedConnector()
    recorder = RecordingModelClient(
        inner,
        store=store,
        experiment_id=short.experiment_id,
        role="action",
        model_id="fake-model",
        clock=FakeClock(wall=STARTED_AT),
    )
    runner = EpisodeRunner(
        connector=connector,
        store=store,
        policy=ActionAgent(recorder, manifest=await connector.manifest()),
        clock=FakeClock(wall=STARTED_AT),
    )

    result = await runner.run(experiment=short, scenario_id="mc_signal_post_a", seed=7)

    assert result.stop_reason == "decision_limit"
    assert (result.decisions_used, result.primitives_used) == (2, 0)
    stored = store.read_episode(result.episode_id)
    assert [step.request.tool_name for step in stored.steps] == [UNUSABLE_REPLY_TOOL] * 2
    assert [step.result.code for step in stored.steps] == ["INVALID_TOOL"] * 2

    records = store.read_model_calls(episode_id=result.episode_id)
    assert [r.finish_reason for r in records] == ["length", "length"]
    assert [r.response_text for r in records] == ["", ""]
    assert all(r.reasoning == "Thinking about the lantern..." for r in records)
    assert [r.action_id for r in records] == [step.request.action_id for step in stored.steps]


# --- Usage, failures, and the provider adapter -------------------------------


async def test_usage_the_provider_did_not_report_is_stored_as_unknown(
    store: EpisodeStore, experiment: ExperimentRecord, episode: EpisodeRecord
) -> None:
    store.create_experiment(experiment)
    store.create_episode(episode)
    inner = ScriptedResponseClient(
        _response(input_tokens=0, output_tokens=0, usage_reported=False),
        _response(input_tokens=0, output_tokens=7, usage_reported=True),
    )
    recorder = _builder_recorder(store, inner)

    await recorder.complete(BUILD_REQUEST)
    await recorder.complete(BUILD_REQUEST)

    unreported, reported = store.read_model_calls()
    assert (unreported.input_tokens, unreported.output_tokens) == (None, None)
    assert (reported.input_tokens, reported.output_tokens) == (0, 7)
    assert (unreported.purpose, reported.purpose) == ("build", "repair")


async def test_a_failing_provider_call_is_recorded_with_its_error_and_still_raises(
    store: EpisodeStore, experiment: ExperimentRecord, episode: EpisodeRecord
) -> None:
    store.create_experiment(experiment)
    store.create_episode(episode)
    recorder = _builder_recorder(
        store, ScriptedResponseClient(ConnectionError("The provider timed out."))
    )

    with pytest.raises(ConnectionError, match="timed out"):
        await recorder.complete(BUILD_REQUEST)

    (record,) = store.read_model_calls()
    assert record.error is not None and "The provider timed out." in record.error
    assert record.response_text is None
    assert (record.finish_reason, record.input_tokens, record.output_tokens) == (None, None, None)
    assert record.model_id == "fake-model"
    assert record.latency_ms == 42


async def test_a_cancelled_provider_call_is_recorded_before_cancellation_propagates(
    store: EpisodeStore, experiment: ExperimentRecord, episode: EpisodeRecord
) -> None:
    store.create_experiment(experiment)
    store.create_episode(episode)

    class CancelledClient:
        provider = "fake-provider"

        async def complete(self, request: ModelRequest) -> ModelResponse:
            del request
            raise asyncio.CancelledError

    recorder = _builder_recorder(store, CancelledClient())

    with pytest.raises(asyncio.CancelledError):
        await recorder.complete(BUILD_REQUEST)

    (record,) = store.read_model_calls()
    assert record.error is not None and "CancelledError" in record.error
    assert record.response_text is None
    assert (record.finish_reason, record.input_tokens, record.output_tokens) == (None, None, None)


def _completion(
    *,
    content: str | None,
    finish_reason: str | None,
    usage: object | None,
    **message_fields: object,
) -> SimpleNamespace:
    return SimpleNamespace(
        model="provider/exact-model-0731",
        usage=usage,
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(content=content, **message_fields),
            )
        ],
    )


def _wandb_client(completion: SimpleNamespace) -> WandbInferenceClient:
    client = WandbInferenceClient(
        ModelSettings(provider="wandb-inference", inference_model="provider/exact-model"),
        WandbSettings(api_key=SECRET),
    )

    async def create(**kwargs: object) -> SimpleNamespace:
        return completion

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    client._client = lambda: fake  # type: ignore[method-assign]
    return client


@pytest.mark.parametrize(
    ("completion", "expected"),
    [
        pytest.param(
            _completion(
                content=None,
                finish_reason="length",
                usage=None,
                reasoning="I should look first.",
            ),
            ("", "length", "I should look first.", False, 0, 0),
            id="empty-length-no-usage",
        ),
        pytest.param(
            _completion(
                content='{"tool": "observe"}',
                finish_reason="stop",
                usage=SimpleNamespace(prompt_tokens=980, completion_tokens=392),
                reasoning_content="Alternate reasoning field.",
            ),
            ('{"tool": "observe"}', "stop", "Alternate reasoning field.", True, 980, 392),
            id="content-with-usage",
        ),
        pytest.param(
            _completion(
                content="hi",
                finish_reason=None,
                usage=SimpleNamespace(prompt_tokens=None, completion_tokens=None),
            ),
            ("hi", None, None, False, 0, 0),
            id="usage-object-without-counts",
        ),
    ],
)
async def test_the_wandb_client_keeps_finish_reason_reasoning_and_usage_reported(
    completion: SimpleNamespace, expected: tuple[object, ...]
) -> None:
    response = await _wandb_client(completion).complete(BUILD_REQUEST)

    assert (
        response.text,
        response.finish_reason,
        response.reasoning,
        response.usage_reported,
        response.input_tokens,
        response.output_tokens,
    ) == expected
    assert response.model_id == "provider/exact-model-0731"


def test_the_wandb_client_names_its_provider() -> None:
    client = _wandb_client(_completion(content="x", finish_reason="stop", usage=None))
    assert client.provider == "wandb-inference"


def test_model_response_defaults_keep_existing_constructors_working() -> None:
    response = ModelResponse(text="x", input_tokens=1, output_tokens=2, model_id="m")
    assert (response.finish_reason, response.reasoning, response.usage_reported) == (
        None,
        None,
        True,
    )


# --- Record invariants -------------------------------------------------------


def _record(**overrides: Any) -> ModelCallRecord:
    values: dict[str, Any] = {
        "call_id": "mc_0001",
        "experiment_id": "exp_0001",
        "purpose": "action",
        "episode_id": "ep_0001",
        "action_id": "a_0001",
        "provider": "fake-provider",
        "model_id": "fake-model",
        "system": "system",
        "prompt": "prompt",
        "max_output_tokens": 512,
        "temperature": 0.0,
        "response_text": "reply",
        "reasoning": None,
        "finish_reason": "stop",
        "input_tokens": 10,
        "output_tokens": 5,
        "latency_ms": 12,
        "error": None,
        "started_at": STARTED_AT,
    }
    values.update(overrides)
    return ModelCallRecord(**values)


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"purpose": "build"}, id="action-id-on-a-build"),
        pytest.param({"purpose": "chat"}, id="unknown-purpose"),
        pytest.param({"response_text": None}, id="no-reply-and-no-error"),
        pytest.param({"error": "boom"}, id="reply-and-error"),
        pytest.param({"episode_id": None}, id="action-without-episode"),
        pytest.param({"input_tokens": -1}, id="negative-usage"),
        pytest.param({"api_key": SECRET}, id="undeclared-credential-field"),
    ],
)
def test_a_model_call_record_refuses_inconsistent_values(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _record(**overrides)


def test_a_failed_action_call_carries_no_action_id() -> None:
    record = _record(action_id=None, response_text=None, finish_reason=None, error="boom")
    assert record.action_id is None


# --- Storage upgrade ---------------------------------------------------------


def test_a_version_2_database_upgrades_to_version_3_with_its_episodes_intact(
    database_path: Path,
    experiment: ExperimentRecord,
    episode: EpisodeRecord,
    step_factory: Callable[..., Any],
) -> None:
    with EpisodeStore.open(database_path) as store:
        store.create_experiment(experiment)
        store.create_episode(episode)
        store.append_step(step_factory(1))
        before = store.read_episode(episode.episode_id)

    # Reproduce a database written by the version-2 code.
    connection = sqlite3.connect(database_path)
    connection.execute("DROP TABLE model_call")
    connection.execute("UPDATE schema_version SET version = 2")
    connection.commit()
    connection.close()

    with EpisodeStore.open(database_path) as upgraded:
        assert SCHEMA_VERSION == 3
        assert upgraded.read_episode(episode.episode_id) == before
        assert upgraded.read_model_calls() == ()
        version = upgraded._connection.execute("SELECT version FROM schema_version").fetchone()
        assert version["version"] == 3
        upgraded.record_model_call(_record())
        assert [r.call_id for r in upgraded.read_model_calls()] == ["mc_0001"]


def test_a_model_call_is_written_once(
    store: EpisodeStore, experiment: ExperimentRecord, episode: EpisodeRecord
) -> None:
    from noob_agent.storage import DuplicateRecordError

    store.create_experiment(experiment)
    store.create_episode(episode)
    store.record_model_call(_record())

    with pytest.raises(DuplicateRecordError):
        store.record_model_call(_record())


# --- Separation: records never reach a prompt --------------------------------


async def test_no_record_text_reaches_a_later_prompt(store: EpisodeStore) -> None:
    client = RoutingModelClient(REJECTED_CANDIDATE, ACCEPTABLE_CANDIDATE)

    await _run_sequence(store, client)

    records = store.read_model_calls()
    record_only = [r.call_id for r in records] + [r.reasoning or "" for r in records]
    assert all(record_only)
    for request in client.requests:
        for text in record_only:
            assert text not in request.prompt
            assert text not in request.system
        assert REASONING_MARK not in request.prompt


# --- Tracing -----------------------------------------------------------------


async def test_every_record_is_mirrored_after_it_is_durable(store: EpisodeStore) -> None:
    sink = CapturingSink(store)
    client = RoutingModelClient(ACCEPTABLE_CANDIDATE)

    await _run_sequence(store, client, trace=sink)

    mirrored = [event for event in sink.events if event.name == MODEL_CALL]
    records = store.read_model_calls()
    assert [event.attributes["call_id"] for event in mirrored] == [r.call_id for r in records]
    assert sink.durable_when_mirrored == [True] * len(records)
    assert mirrored[0].attributes["finish_reason"] == "stop"
    assert mirrored[0].attributes["purpose"] == "action"


async def test_a_failing_trace_mirror_never_changes_the_call(
    store: EpisodeStore, experiment: ExperimentRecord, episode: EpisodeRecord
) -> None:
    store.create_experiment(experiment)
    store.create_episode(episode)
    recorder = _builder_recorder(store, ScriptedResponseClient(_response()), trace=FailingSink())

    response = await recorder.complete(BUILD_REQUEST)

    assert response.text == "a reply"
    assert len(store.read_model_calls()) == 1


class FakeWeave:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object | None]] = []
        self.finished: list[object] = []

    def create_call(self, op: str, inputs: dict[str, Any], parent: object | None = None) -> object:
        handle = f"call-{len(self.calls)}"
        self.calls.append((op, parent))
        return handle

    def finish_call(self, call: object, output: dict[str, Any] | None = None) -> None:
        self.finished.append(call)


def test_the_weave_mirror_nests_action_calls_under_their_episode() -> None:
    weave = FakeWeave()
    sink = WeaveTraceSink(weave)
    started = TraceEvent(name="episode.started", attributes={"episode_id": "ep_0001"})

    sink.record(started)
    sink.record(model_call_event(_record()))
    sink.flush()
    sink.record(model_call_event(_record(call_id="mc_0002", purpose="build", action_id=None)))

    assert weave.calls == [
        ("noob_agent.episode", None),
        (MODEL_CALL_OP_NAME, "call-0"),
        (MODEL_CALL_OP_NAME, None),
    ]
    assert "call-1" in weave.finished and "call-2" in weave.finished


# --- Credentials -------------------------------------------------------------


async def test_no_credential_appears_in_a_record_repr_row_or_trace_attribute(
    store: EpisodeStore, experiment: ExperimentRecord, episode: EpisodeRecord
) -> None:
    store.create_experiment(experiment)
    store.create_episode(episode)
    sink = CapturingSink()
    inner = _wandb_client(
        _completion(
            content="reply",
            finish_reason="stop",
            usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3),
        )
    )
    recorder = _builder_recorder(store, inner, trace=sink)

    await recorder.complete(BUILD_REQUEST)

    (record,) = store.read_model_calls()
    assert record.provider == "wandb-inference"
    raw_rows = store._connection.execute("SELECT * FROM model_call").fetchall()
    surfaces = [
        record.model_dump_json(),
        repr(record),
        repr(recorder),
        json.dumps([dict(row) for row in raw_rows]),
        json.dumps([event.attributes for event in sink.events]),
    ]
    for surface in surfaces:
        assert SECRET not in surface
