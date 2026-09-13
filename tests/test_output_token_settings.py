"""Per-role model output caps for the step 18b learning sequence."""

from __future__ import annotations

import importlib.util
import itertools
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fakes.connector import FakeClock, ScriptedConnector, ScriptedStep

from noob_agent.agents.action import DEFAULT_MAX_OUTPUT_TOKENS as LEGACY_ACTION_CAP
from noob_agent.domain.records import StoredEpisode
from noob_agent.models.client import ModelRequest, ModelResponse
from noob_agent.prompts.builder import BUILDER_SYSTEM
from noob_agent.runtime.heldout import HeldOutRunner, heldout_experiment
from noob_agent.runtime.sequence import HeldOutCell, LearningSequence, LearningSequenceResult
from noob_agent.settings import IntegrationSettings, ModelSettings
from noob_agent.skills.executor import LocalSubprocessSkillExecutor
from noob_agent.skills.registry import SkillRegistry
from noob_agent.storage import EpisodeStore

STARTED_AT = datetime(2026, 9, 12, 23, 30, tzinfo=UTC)
ACTION_CAP = 1_500
BUILDER_CAP = 7_000

SKILL_SOURCE = '''"""Record one fresh public observation."""

from noob_agent.skills.contract import EvidenceRef, SkillContext, SkillResult


async def run(context: SkillContext, inputs: dict[str, object]) -> SkillResult:
    """Record the current public state."""
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
        "name": "record_visible_state",
        "version": 1,
        "parent_version": None,
        "purpose": "Record one fresh public observation.",
        "input_schema": {"properties": {}},
        "required_tools": ["observe"],
        "max_primitive_actions": 1,
        "max_wall_time_seconds": 5,
        "success_claim": "The result cites the fresh observation.",
        "api_version": "noob-agent.skill.v1",
    }
    return f"```python\n{source}```\n\n```json\n{json.dumps(metadata)}\n```\n"


class RoutingClient:
    provider = "fake-provider"

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []
        self._builder_replies = [_candidate("import os\n" + SKILL_SOURCE), _candidate()]

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
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
        return ModelResponse(text=text, input_tokens=100, output_tokens=40, model_id="fake")


class ConnectorFactory:
    def __init__(self) -> None:
        self._ids = itertools.count(1)

    def __call__(self) -> ScriptedConnector:
        return ScriptedConnector(
            ScriptedStep(terminal=True, terminal_reason="done"),
            episode_id=f"ep_caps_{next(self._ids):04d}",
        )


@dataclass(frozen=True)
class Grade:
    goal_completed: bool = True


def _grade(stored: StoredEpisode, connector: ScriptedConnector) -> Grade:
    del stored, connector
    return Grade()


def _load_run_script():
    path = Path("scripts/run_doom_learning_sequence.py")
    spec = importlib.util.spec_from_file_location("run_doom_learning_sequence_caps", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_output_cap_settings_parse_the_approved_defaults() -> None:
    settings = IntegrationSettings.from_environ({})

    assert settings.model.action_max_output_tokens == 1_024
    assert settings.model.builder_max_output_tokens == 6_000


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("NOOB_AGENT_ACTION_MAX_OUTPUT_TOKENS", "not-an-integer"),
        ("NOOB_AGENT_ACTION_MAX_OUTPUT_TOKENS", "0"),
        ("NOOB_AGENT_ACTION_MAX_OUTPUT_TOKENS", "-1"),
        ("NOOB_AGENT_ACTION_MAX_OUTPUT_TOKENS", "2001"),
        ("NOOB_AGENT_BUILDER_MAX_OUTPUT_TOKENS", "not-an-integer"),
        ("NOOB_AGENT_BUILDER_MAX_OUTPUT_TOKENS", "0"),
        ("NOOB_AGENT_BUILDER_MAX_OUTPUT_TOKENS", "-1"),
        ("NOOB_AGENT_BUILDER_MAX_OUTPUT_TOKENS", "8001"),
    ],
)
def test_invalid_or_over_ceiling_output_caps_are_refused(name: str, value: str) -> None:
    with pytest.raises(ValueError):
        IntegrationSettings.from_environ({name: value})


def test_protocol_ceiling_values_are_accepted() -> None:
    settings = IntegrationSettings.from_environ(
        {
            "NOOB_AGENT_ACTION_MAX_OUTPUT_TOKENS": "2000",
            "NOOB_AGENT_BUILDER_MAX_OUTPUT_TOKENS": "8000",
        }
    )

    assert settings.model.action_max_output_tokens == 2_000
    assert settings.model.builder_max_output_tokens == 8_000


async def test_sequence_applies_caps_to_cold_build_repair_and_heldout_calls(
    store: EpisodeStore,
) -> None:
    client = RoutingClient()
    sequence = LearningSequence(
        connector_factory=ConnectorFactory(),
        client=client,
        model_id="fake",
        store=store,
        registry=SkillRegistry(),
        executor=LocalSubprocessSkillExecutor(),
        grade=_grade,
        clock=FakeClock(wall=STARTED_AT),
        action_max_output_tokens=ACTION_CAP,
        builder_max_output_tokens=BUILDER_CAP,
    )

    result = await sequence.run(
        sequence_id="seq_caps",
        training_scenario_id="training",
        training_seed=1,
        heldout=(HeldOutCell("heldout", 2),),
    )

    assert result.builder.accepted is True
    action_requests = [request for request in client.requests if request.system != BUILDER_SYSTEM]
    builder_requests = [request for request in client.requests if request.system == BUILDER_SYSTEM]
    assert [request.max_output_tokens for request in action_requests] == [ACTION_CAP, ACTION_CAP]
    assert [request.max_output_tokens for request in builder_requests] == [BUILDER_CAP, BUILDER_CAP]
    records = store.read_model_calls()
    assert [(record.purpose, record.max_output_tokens) for record in records] == [
        ("action", ACTION_CAP),
        ("build", BUILDER_CAP),
        ("repair", BUILDER_CAP),
        ("action", ACTION_CAP),
    ]


async def test_heldout_runner_keeps_the_legacy_action_cap_when_none_is_passed(
    store: EpisodeStore,
) -> None:
    client = RoutingClient()
    record = heldout_experiment(
        experiment_id="exp_legacy_cap",
        model_id="fake",
        connector_version="fake-connector-1",
        created_at=STARTED_AT,
    )
    store.create_experiment(record)
    runner = HeldOutRunner(
        connector=ConnectorFactory()(),
        store=store,
        registry=SkillRegistry(),
        client=client,
        executor=LocalSubprocessSkillExecutor(),
        clock=FakeClock(wall=STARTED_AT),
    )

    await runner.run(experiment=record, scenario_id="heldout", seed=2)

    assert client.requests[0].max_output_tokens == LEGACY_ACTION_CAP == 512


async def test_run_summary_reports_both_configured_caps(store: EpisodeStore) -> None:
    client = RoutingClient()
    result: LearningSequenceResult[Grade] = await LearningSequence(
        connector_factory=ConnectorFactory(),
        client=client,
        model_id="fake",
        store=store,
        registry=SkillRegistry(),
        executor=LocalSubprocessSkillExecutor(),
        grade=_grade,
        clock=FakeClock(wall=STARTED_AT),
        action_max_output_tokens=ACTION_CAP,
        builder_max_output_tokens=BUILDER_CAP,
    ).run(
        sequence_id="seq_summary",
        training_scenario_id="training",
        training_seed=1,
        heldout=(HeldOutCell("heldout", 2),),
    )

    summary = _load_run_script()._summary(
        result,
        action_max_output_tokens=ACTION_CAP,
        builder_max_output_tokens=BUILDER_CAP,
    )

    assert summary["max_output_tokens"] == {
        "action": ACTION_CAP,
        "builder": BUILDER_CAP,
    }


def test_model_settings_constructor_enforces_the_same_ceilings() -> None:
    with pytest.raises(ValueError):
        ModelSettings(action_max_output_tokens=2_001)
    with pytest.raises(ValueError):
        ModelSettings(builder_max_output_tokens=8_001)
