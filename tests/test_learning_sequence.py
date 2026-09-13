"""One learning sequence: cold training, the Builder, then fresh held-out episodes."""

from __future__ import annotations

import importlib.util
import itertools
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fakes.connector import FakeClock, ScriptedConnector, ScriptedStep

from noob_agent.domain.records import StoredEpisode
from noob_agent.models.client import ModelRequest, ModelResponse
from noob_agent.prompts.builder import BUILDER_SYSTEM
from noob_agent.runtime.heldout import (
    HELD_OUT_DECISION_BUDGET,
    HELD_OUT_PRIMITIVE_BUDGET,
    HELD_OUT_WALL_TIME_MS,
)
from noob_agent.runtime.sequence import (
    TRAINING_DECISION_BUDGET,
    TRAINING_PRIMITIVE_BUDGET,
    TRAINING_WALL_TIME_MS,
    HeldOutCell,
    LearningSequence,
    LearningSequenceResult,
)
from noob_agent.skills.executor import LocalSubprocessSkillExecutor
from noob_agent.skills.registry import SkillRegistry
from noob_agent.storage import EpisodeStore

STARTED_AT = datetime(2026, 9, 12, 22, 30, 0, tzinfo=UTC)
TRAINING = "mc_training_room"
HELDOUT_CELLS = (HeldOutCell("mc_layout_a", 11), HeldOutCell("mc_layout_b", 12))
SKILL_NAME = "record_visible_state"
GRADE_MARK = "PRIVATE-GRADE"

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

METADATA = {
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

ACCEPTABLE_CANDIDATE = (
    f"```python\n{SKILL_SOURCE}```\n\n```json\n{json.dumps(METADATA, indent=2)}\n```\n"
)


class RoutingModelClient:
    """No credentials, no network: Builder requests get scripted candidates and
    Action requests get one `observe` decision whose subgoal carries a unique marker."""

    def __init__(self, *builder_replies: str) -> None:
        self._builder_replies = list(builder_replies)
        self._markers = itertools.count(1)
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if request.system == BUILDER_SYSTEM:
            if not self._builder_replies:
                raise AssertionError("The Builder asked for more replies than were scripted.")
            text = self._builder_replies.pop(0)
        else:
            text = json.dumps(
                {
                    "subgoal": f"marker-{next(self._markers):03d}",
                    "expected_evidence": "A fresh observation.",
                    "tool": "observe",
                    "arguments": {},
                }
            )
        return ModelResponse(text=text, input_tokens=100, output_tokens=40, model_id="fake-model")

    def action_requests(self) -> list[ModelRequest]:
        return [request for request in self.requests if request.system != BUILDER_SYSTEM]

    def builder_requests(self) -> list[ModelRequest]:
        return [request for request in self.requests if request.system == BUILDER_SYSTEM]


class ConnectorFactory:
    """Hands out a fresh scripted connector per episode and keeps every one."""

    def __init__(self) -> None:
        self.created: list[ScriptedConnector] = []

    def __call__(self) -> ScriptedConnector:
        index = len(self.created) + 1
        if index == 1:
            script = (ScriptedStep(), ScriptedStep(terminal=True, terminal_reason="done"))
        else:
            script = (ScriptedStep(terminal=True, terminal_reason="done"),)
        connector = ScriptedConnector(*script, episode_id=f"ep_seq_{index:04d}")
        self.created.append(connector)
        return connector


GradeCall = tuple[str, bool, bool]


def recording_grader(calls: list[GradeCall]) -> Callable[[StoredEpisode, ScriptedConnector], str]:
    def grade(stored: StoredEpisode, connector: ScriptedConnector) -> str:
        calls.append((stored.episode.episode_id, stored.outcome is not None, connector.closed))
        return f"{GRADE_MARK}-{stored.episode.episode_id}"

    return grade


async def run_sequence(
    store: EpisodeStore,
    client: RoutingModelClient,
    factory: ConnectorFactory,
    *,
    grade_calls: list[GradeCall] | None = None,
    registry: SkillRegistry | None = None,
    heldout: tuple[HeldOutCell, ...] = HELDOUT_CELLS,
) -> LearningSequenceResult[str]:
    sequence = LearningSequence(
        connector_factory=factory,
        client=client,
        model_id="fake-model",
        store=store,
        registry=registry if registry is not None else SkillRegistry(),
        executor=LocalSubprocessSkillExecutor(),
        grade=recording_grader(grade_calls if grade_calls is not None else []),
        clock=FakeClock(wall=STARTED_AT),
    )
    return await sequence.run(
        sequence_id="seq_0001", training_scenario_id=TRAINING, training_seed=7, heldout=heldout
    )


async def test_the_builder_sees_only_the_cold_training_episode(store: EpisodeStore) -> None:
    client = RoutingModelClient(ACCEPTABLE_CANDIDATE)
    factory = ConnectorFactory()
    registry = SkillRegistry()

    result = await run_sequence(store, client, factory, registry=registry)

    assert result.builder.accepted is True
    assert result.accepted_version is not None
    assert result.accepted_version.authoring_episode_id == result.training.episode_id
    assert result.accepted_version.authoring_model_id == "fake-model"
    builder_requests = client.builder_requests()
    assert len(builder_requests) == 1
    for cell in HELDOUT_CELLS:
        assert cell.scenario_id not in builder_requests[0].prompt
    first_builder = client.requests.index(builder_requests[0])
    offered = [
        index for index, request in enumerate(client.requests) if SKILL_NAME in request.prompt
    ]
    assert offered and min(offered) > first_builder
    training_requests = client.requests[:first_builder]
    assert training_requests and all(SKILL_NAME not in r.prompt for r in training_requests)


async def test_no_heldout_episode_runs_without_an_accepted_skill(store: EpisodeStore) -> None:
    client = RoutingModelClient("I could not write a skill.")
    factory = ConnectorFactory()

    result = await run_sequence(store, client, factory)

    assert result.builder.accepted is False
    assert result.accepted_version is None
    assert result.heldout == ()
    assert result.heldout_skipped_reason == "unusable_reply"
    assert len(factory.created) == 1


async def test_each_heldout_episode_gets_a_fresh_connector_and_conversation(
    store: EpisodeStore,
) -> None:
    client = RoutingModelClient(ACCEPTABLE_CANDIDATE)
    factory = ConnectorFactory()

    result = await run_sequence(store, client, factory)

    assert len(factory.created) == 1 + len(HELDOUT_CELLS)
    assert factory.created[0].reset_calls == [(TRAINING, 7)]
    for connector, cell in zip(factory.created[1:], HELDOUT_CELLS, strict=True):
        assert connector.reset_calls == [(cell.scenario_id, cell.seed)]
        assert connector.closed
    assert [episode.scenario_id for episode in result.heldout] == [
        cell.scenario_id for cell in HELDOUT_CELLS
    ]
    for episode in result.heldout:
        assert SKILL_NAME in episode.offered_skills

    action_prompts = [request.prompt for request in client.action_requests()]
    earlier_markers: list[str] = []
    for prompt in action_prompts:
        if SKILL_NAME in prompt:
            assert not any(marker in prompt for marker in earlier_markers)
        earlier_markers.append(f"marker-{len(earlier_markers) + 1:03d}")


async def test_grades_follow_each_finished_episode_and_never_reach_the_model(
    store: EpisodeStore,
) -> None:
    client = RoutingModelClient(ACCEPTABLE_CANDIDATE)
    grade_calls: list[GradeCall] = []

    result = await run_sequence(store, client, ConnectorFactory(), grade_calls=grade_calls)

    episode_ids = [result.training.episode_id] + [e.episode_id for e in result.heldout]
    assert [call[0] for call in grade_calls] == episode_ids
    assert all(finished and closed for _, finished, closed in grade_calls)
    assert result.training.grade == f"{GRADE_MARK}-{result.training.episode_id}"
    assert [e.grade for e in result.heldout] == [f"{GRADE_MARK}-{i}" for i in episode_ids[1:]]
    for request in client.requests:
        assert GRADE_MARK not in request.prompt
        assert GRADE_MARK not in request.system


async def test_training_and_heldout_experiments_carry_the_frozen_budgets(
    store: EpisodeStore,
) -> None:
    result = await run_sequence(store, RoutingModelClient(ACCEPTABLE_CANDIDATE), ConnectorFactory())

    training, heldout = result.training_experiment, result.heldout_experiment
    assert (
        training.decision_budget,
        training.primitive_budget,
        training.wall_time_budget_ms,
    ) == (TRAINING_DECISION_BUDGET, TRAINING_PRIMITIVE_BUDGET, TRAINING_WALL_TIME_MS)
    assert (TRAINING_DECISION_BUDGET, TRAINING_PRIMITIVE_BUDGET, TRAINING_WALL_TIME_MS) == (
        20,
        40,
        180_000,
    )
    assert (heldout.decision_budget, heldout.primitive_budget, heldout.wall_time_budget_ms) == (
        HELD_OUT_DECISION_BUDGET,
        HELD_OUT_PRIMITIVE_BUDGET,
        HELD_OUT_WALL_TIME_MS,
    )
    assert "weaker isolation" in result.skill_isolation_note


async def test_a_non_benchmark_condition_labels_both_experiments(store: EpisodeStore) -> None:
    sequence = LearningSequence(
        connector_factory=ConnectorFactory(),
        client=RoutingModelClient(ACCEPTABLE_CANDIDATE),
        model_id="fake-model",
        store=store,
        registry=SkillRegistry(),
        executor=LocalSubprocessSkillExecutor(),
        grade=recording_grader([]),
        clock=FakeClock(wall=STARTED_AT),
        condition="non-benchmark-live-demo",
    )

    result = await sequence.run(
        sequence_id="live-demo-seq",
        training_scenario_id=TRAINING,
        training_seed=7,
        heldout=HELDOUT_CELLS,
    )

    assert result.training_experiment.condition == "non-benchmark-live-demo"
    assert result.heldout_experiment.condition == "non-benchmark-live-demo"


@pytest.mark.parametrize(
    "heldout",
    [
        pytest.param((), id="no-heldout-cells"),
        pytest.param((HeldOutCell(TRAINING, 99),), id="training-scenario-reused"),
    ],
)
async def test_invalid_heldout_cells_are_refused_before_any_episode(
    store: EpisodeStore, heldout: tuple[HeldOutCell, ...]
) -> None:
    factory = ConnectorFactory()

    with pytest.raises(ValueError):
        await run_sequence(store, RoutingModelClient(), factory, heldout=heldout)

    assert factory.created == []


def _load_run_script():
    path = Path("scripts/run_doom_learning_sequence.py")
    spec = importlib.util.spec_from_file_location("run_doom_learning_sequence", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("environ", "named"),
    [
        pytest.param(
            {"NOOB_AGENT_SANDBOX_MODE": "local"}, "NOOB_AGENT_MODEL_PROVIDER", id="no-model"
        ),
        pytest.param(
            {
                "NOOB_AGENT_MODEL_PROVIDER": "wandb-inference",
                "NOOB_AGENT_INFERENCE_MODEL": "some-model",
            },
            "NOOB_AGENT_SANDBOX_MODE",
            id="no-sandbox",
        ),
        pytest.param(
            {"NOOB_AGENT_MODEL_PROVIDER": "wandb-inference", "NOOB_AGENT_SANDBOX_MODE": "local"},
            "NOOB_AGENT_INFERENCE_MODEL",
            id="no-model-id",
        ),
    ],
)
def test_the_run_script_refuses_a_disabled_provider_or_sandbox(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], environ: dict[str, str], named: str
) -> None:
    database = tmp_path / "never-created.sqlite3"

    code = _load_run_script().main(["--database", str(database)], environ=environ)

    assert code == 2
    assert named in capsys.readouterr().err
    assert not database.exists()
