"""Step 22.A: reliable, fast Action decisions through native tool calls and thinking controls."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fakes.connector import MANIFEST, FakeClock, ScriptedConnector, ScriptedStep

from noob_agent.agents.action import UNUSABLE_REPLY_TOOL, ActionAgent
from noob_agent.domain.records import ExperimentRecord
from noob_agent.models.client import (
    ModelRequest,
    ModelResponse,
    ToolCall,
    ToolSpec,
    WandbInferenceClient,
)
from noob_agent.models.recording import RecordingModelClient
from noob_agent.prompts.action import ACTION_SYSTEM
from noob_agent.runtime.runner import EpisodeRunner
from noob_agent.settings import IntegrationSettings, ModelSettings, WandbSettings
from noob_agent.skills.runtime import SkillRequest
from noob_agent.storage import EpisodeStore
from noob_agent.storage.schema import SCHEMA_VERSION

STARTED_AT = datetime(2026, 9, 13, 4, 0, tzinfo=UTC)
TOOL_REQUEST = ModelRequest(
    system="system",
    prompt="prompt",
    max_output_tokens=64,
    tools=(
        ToolSpec(
            name="observe",
            description="Look.",
            parameters={"type": "object", "properties": {}},
        ),
    ),
)


# --- The provider adapter ------------------------------------------------------


def _adapter(completion: SimpleNamespace) -> tuple[WandbInferenceClient, list[dict[str, Any]]]:
    client = WandbInferenceClient(
        ModelSettings(provider="wandb-inference", inference_model="provider/model"),
        WandbSettings(api_key="test-key"),
    )
    sent: list[dict[str, Any]] = []

    async def create(**kwargs: Any) -> SimpleNamespace:
        sent.append(kwargs)
        return completion

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    client._client = lambda: fake  # type: ignore[method-assign]
    return client, sent


def _completion(
    *,
    content: str | None = "",
    tool_calls: list[SimpleNamespace] | None = None,
    finish_reason: str = "stop",
) -> SimpleNamespace:
    return SimpleNamespace(
        model="provider/model",
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(content=content, tool_calls=tool_calls),
            )
        ],
    )


def _tool_call(name: str, arguments: str) -> SimpleNamespace:
    return SimpleNamespace(function=SimpleNamespace(name=name, arguments=arguments))


@pytest.mark.parametrize(
    ("thinking", "expected"),
    [
        (None, None),
        (False, {"chat_template_kwargs": {"thinking": False}}),
        (True, {"chat_template_kwargs": {"thinking": True}}),
    ],
)
async def test_the_adapter_sends_the_thinking_setting_only_when_it_is_set(
    thinking: bool | None, expected: dict[str, object] | None
) -> None:
    client, sent = _adapter(_completion(content="{}"))

    await client.complete(
        ModelRequest(system="s", prompt="p", max_output_tokens=8, thinking=thinking)
    )

    assert sent[0].get("extra_body") == expected


async def test_the_adapter_offers_tools_and_requires_one_call() -> None:
    client, sent = _adapter(_completion(tool_calls=[_tool_call("observe", "{}")]))

    await client.complete(TOOL_REQUEST)

    assert sent[0]["tool_choice"] == "required"
    assert sent[0]["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "observe",
                "description": "Look.",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]


async def test_a_request_without_tools_sends_no_tool_fields() -> None:
    client, sent = _adapter(_completion(content="{}"))

    await client.complete(ModelRequest(system="s", prompt="p", max_output_tokens=8))

    assert "tools" not in sent[0] and "tool_choice" not in sent[0]


async def test_the_adapter_returns_the_first_tool_call() -> None:
    client, _ = _adapter(
        _completion(
            content=None,
            tool_calls=[_tool_call("turn_left", '{"degrees": 5}'), _tool_call("attack", "{}")],
            finish_reason="tool_calls",
        )
    )

    response = await client.complete(TOOL_REQUEST)

    assert response.tool_call == ToolCall(name="turn_left", arguments='{"degrees": 5}')
    assert response.text == ""


# --- The Action agent -----------------------------------------------------------


class OneReplyClient:
    provider = "scripted"

    def __init__(self, *responses: ModelResponse) -> None:
        self._responses = list(responses)
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return self._responses.pop(0)


def _call_reply(name: str, arguments: dict[str, object] | str) -> ModelResponse:
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return ModelResponse(
        text="",
        input_tokens=10,
        output_tokens=5,
        model_id="fake",
        finish_reason="tool_calls",
        tool_call=ToolCall(name=name, arguments=raw),
    )


async def _observation() -> Any:
    return await ScriptedConnector().reset("mc_signal_post_a", 7)


async def test_every_primitive_is_offered_as_a_tool_with_required_intent_fields() -> None:
    client = OneReplyClient(_call_reply("observe", {"subgoal": "s", "expected_evidence": "e"}))
    agent = ActionAgent(client, manifest=MANIFEST, thinking=False)

    await agent.choose(await _observation())

    request = client.requests[0]
    assert request.system == ACTION_SYSTEM
    assert request.thinking is False
    assert [tool.name for tool in request.tools] == [tool.name for tool in MANIFEST.tools]
    for spec, tool in zip(request.tools, MANIFEST.tools, strict=True):
        parameters = spec.parameters
        assert parameters["type"] == "object"
        required = parameters["required"]
        assert isinstance(required, list)
        assert {"subgoal", "expected_evidence"} <= set(required)
        properties = parameters["properties"]
        assert isinstance(properties, dict)
        assert "finding" in properties
        for name in dict(tool.argument_schema.get("properties", {}) or {}):
            assert name in properties


async def test_a_tool_call_becomes_a_request_without_the_intent_fields() -> None:
    client = OneReplyClient(
        _call_reply(
            "observe",
            {"subgoal": "Find the post.", "expected_evidence": "Lantern visible.", "radius": 8},
        )
    )
    agent = ActionAgent(client, manifest=MANIFEST)

    request = await agent.choose(await _observation())

    assert not isinstance(request, SkillRequest)
    assert (request.tool_name, request.arguments) == ("observe", {"radius": 8})
    decision = agent.decisions[0]
    assert (decision.kind, decision.subgoal, decision.expected_evidence) == (
        "primitive",
        "Find the post.",
        "Lantern visible.",
    )


async def test_a_finding_in_a_tool_call_is_kept_on_the_decision() -> None:
    finding = {
        "expected_behavior": "One output.",
        "expected_basis": "Earlier use produced one.",
        "actual_behavior": "Two outputs.",
        "expected_count": 1,
        "actual_count": 2,
        "evidence": [{"kind": "action_id", "value": "a_0001"}],
    }
    client = OneReplyClient(
        _call_reply("observe", {"subgoal": "s", "expected_evidence": "e", "finding": finding})
    )
    agent = ActionAgent(client, manifest=MANIFEST)

    request = await agent.choose(await _observation())

    assert not isinstance(request, SkillRequest)
    assert "finding" not in request.arguments
    assert agent.decisions[0].finding is not None


@pytest.mark.parametrize("arguments", ["not json", "[1, 2]"])
async def test_tool_call_arguments_that_are_not_an_object_are_unusable(arguments: str) -> None:
    agent = ActionAgent(OneReplyClient(_call_reply("observe", arguments)), manifest=MANIFEST)

    request = await agent.choose(await _observation())

    assert not isinstance(request, SkillRequest)
    assert request.tool_name == UNUSABLE_REPLY_TOOL
    assert agent.decisions[0].kind == "unusable"


async def test_an_undeclared_tool_call_is_sent_so_the_connector_refuses_it() -> None:
    agent = ActionAgent(
        OneReplyClient(_call_reply("teleport", {"subgoal": "s", "expected_evidence": "e"})),
        manifest=MANIFEST,
    )

    request = await agent.choose(await _observation())

    assert not isinstance(request, SkillRequest)
    assert request.tool_name == "teleport"


async def test_a_reply_without_a_tool_call_falls_back_to_the_json_reply() -> None:
    text = json.dumps(
        {"subgoal": "s", "expected_evidence": "e", "tool": "observe", "arguments": {"radius": 3}}
    )
    agent = ActionAgent(
        OneReplyClient(ModelResponse(text=text, input_tokens=1, output_tokens=1, model_id="f")),
        manifest=MANIFEST,
    )

    request = await agent.choose(await _observation())

    assert not isinstance(request, SkillRequest)
    assert (request.tool_name, request.arguments) == ("observe", {"radius": 3})


async def test_a_reply_cut_off_at_its_cap_is_unusable_and_marked_truncated() -> None:
    capped = ModelResponse(
        text="", input_tokens=1, output_tokens=64, model_id="f", finish_reason="length"
    )
    finished = ModelResponse(text="no decision", input_tokens=1, output_tokens=2, model_id="f")
    agent = ActionAgent(OneReplyClient(capped, finished), manifest=MANIFEST)
    observation = await _observation()

    first = await agent.choose(observation)
    second = await agent.choose(observation)

    assert not isinstance(first, SkillRequest) and not isinstance(second, SkillRequest)
    assert first.tool_name == second.tool_name == UNUSABLE_REPLY_TOOL
    assert [decision.truncated for decision in agent.decisions] == [True, False]


# --- Settings ---------------------------------------------------------------------


def test_thinking_is_off_for_both_roles_by_default() -> None:
    settings = IntegrationSettings.from_environ({})

    assert settings.model.action_thinking is False
    assert settings.model.builder_thinking is False


def test_thinking_settings_are_read_from_the_environment() -> None:
    settings = IntegrationSettings.from_environ(
        {"NOOB_AGENT_ACTION_THINKING": "true", "NOOB_AGENT_BUILDER_THINKING": "on"}
    )

    assert settings.model.action_thinking is True
    assert settings.model.builder_thinking is True


def test_an_invalid_thinking_setting_is_refused() -> None:
    with pytest.raises(ValueError):
        IntegrationSettings.from_environ({"NOOB_AGENT_ACTION_THINKING": "sometimes"})


# --- Recording and storage --------------------------------------------------------


async def test_request_options_and_the_tool_call_are_recorded(
    store: EpisodeStore, experiment: ExperimentRecord
) -> None:
    short = experiment.model_copy(update={"decision_budget": 1})
    store.create_experiment(short)
    connector = ScriptedConnector(ScriptedStep())
    recorder = RecordingModelClient(
        OneReplyClient(
            _call_reply("observe", {"subgoal": "s", "expected_evidence": "e", "radius": 2})
        ),
        store=store,
        experiment_id=short.experiment_id,
        role="action",
        model_id="fake",
        clock=FakeClock(wall=STARTED_AT),
    )
    runner = EpisodeRunner(
        connector=connector,
        store=store,
        policy=ActionAgent(recorder, manifest=MANIFEST, thinking=False),
        clock=FakeClock(wall=STARTED_AT),
    )

    result = await runner.run(experiment=short, scenario_id="mc_signal_post_a", seed=7)

    (record,) = store.read_model_calls(episode_id=result.episode_id)
    assert record.request_options == {
        "thinking": False,
        "tools": [tool.name for tool in MANIFEST.tools],
        "tool_choice": "required",
    }
    assert record.tool_call == {
        "name": "observe",
        "arguments": json.dumps({"subgoal": "s", "expected_evidence": "e", "radius": 2}),
    }


def test_the_schema_is_version_five() -> None:
    assert SCHEMA_VERSION == 5


V3_MODEL_CALL = """
CREATE TABLE model_call (
    call_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL, purpose TEXT NOT NULL,
    episode_id TEXT, action_id TEXT, provider TEXT NOT NULL, model_id TEXT NOT NULL,
    system_text TEXT NOT NULL, prompt_text TEXT NOT NULL, max_output_tokens INTEGER NOT NULL,
    temperature REAL NOT NULL, response_text TEXT, reasoning TEXT, finish_reason TEXT,
    input_tokens INTEGER, output_tokens INTEGER, latency_ms INTEGER NOT NULL, error TEXT,
    started_at TEXT NOT NULL
)
"""


@pytest.mark.parametrize("old_version", [3, 4])
def test_an_older_database_gains_the_new_columns_and_keeps_its_calls(
    tmp_path: Path, experiment: ExperimentRecord, old_version: int
) -> None:
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE experiment (experiment_id TEXT PRIMARY KEY, model_id TEXT NOT NULL,"
            " condition TEXT NOT NULL, connector_version TEXT NOT NULL,"
            " decision_budget INTEGER NOT NULL, primitive_budget INTEGER NOT NULL,"
            " wall_time_budget_ms INTEGER NOT NULL, created_at TEXT NOT NULL)"
        )
        connection.execute(V3_MODEL_CALL)
        connection.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        connection.execute("INSERT INTO schema_version VALUES (?)", (old_version,))
        connection.execute(
            "INSERT INTO experiment VALUES (?, 'm', 'c', 'v', 1, 1, 1, ?)",
            (experiment.experiment_id, STARTED_AT.isoformat()),
        )
        connection.execute(
            "INSERT INTO model_call VALUES ('mc_old', ?, 'build', NULL, NULL, 'p', 'm', 's', 'p',"
            " 10, 0.0, 'reply', NULL, 'stop', 1, 1, 5, NULL, ?)",
            (experiment.experiment_id, STARTED_AT.isoformat()),
        )

    with EpisodeStore.open(path) as store:
        (old,) = store.read_model_calls()

    assert old.response_text == "reply"
    assert old.request_options is None and old.tool_call is None
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(model_call)")}
        (version,) = connection.execute("SELECT version FROM schema_version").fetchone()
    assert {"request_options_json", "tool_call_json"} <= columns
    assert version == 5


# --- The learning sequence --------------------------------------------------------


async def test_the_sequence_sends_the_action_thinking_setting_on_every_action_call(
    store: EpisodeStore,
) -> None:
    import itertools

    from test_loop_bench import CANDIDATE

    from noob_agent.prompts.builder import BUILDER_SYSTEM
    from noob_agent.runtime.sequence import HeldOutCell, LearningSequence
    from noob_agent.skills.executor import LocalSubprocessSkillExecutor
    from noob_agent.skills.registry import SkillRegistry

    class Routing:
        provider = "scripted"

        def __init__(self) -> None:
            self.requests: list[ModelRequest] = []

        async def complete(self, request: ModelRequest) -> ModelResponse:
            self.requests.append(request)
            if request.system == BUILDER_SYSTEM:
                return ModelResponse(text=CANDIDATE, input_tokens=1, output_tokens=1, model_id="f")
            return _call_reply("observe", {"subgoal": "s", "expected_evidence": "e"})

    ids = itertools.count(1)
    client = Routing()
    sequence = LearningSequence(
        connector_factory=lambda: ScriptedConnector(
            ScriptedStep(),
            ScriptedStep(terminal=True, terminal_reason="done"),
            episode_id=f"ep_think_{next(ids):03d}",
        ),
        client=client,
        model_id="f",
        store=store,
        registry=SkillRegistry(),
        executor=LocalSubprocessSkillExecutor(),
        grade=lambda stored, connector: None,
        clock=FakeClock(wall=STARTED_AT),
        action_thinking=False,
    )

    result = await sequence.run(
        sequence_id="think",
        training_scenario_id="train",
        training_seed=1,
        heldout=(HeldOutCell("held", 2),),
    )

    assert result.builder.accepted is True and len(result.heldout) == 1
    actions = [request for request in client.requests if request.system == ACTION_SYSTEM]
    assert len(actions) >= 3
    assert all(request.thinking is False for request in actions)
    assert all(request.tools for request in actions)


async def test_an_accepted_skill_is_offered_as_a_tool_and_called_as_a_skill() -> None:
    from test_loop_bench import METADATA, SKILL_SOURCE

    from noob_agent.domain.skills import SkillPackage
    from noob_agent.skills.registry import SkillRegistry

    registry = SkillRegistry()
    metadata = {**METADATA, "input_schema": {"properties": {"radius": {"type": "integer"}}}}
    proposed = registry.propose(
        SkillPackage(name=str(metadata["name"]), source=SKILL_SOURCE, metadata=metadata),
        authoring_episode_id="ep",
        authoring_model_id="f",
        created_at=STARTED_AT,
        reason="test",
    )
    registry.begin_validation(proposed.name, proposed.version, reason="test")
    registry.accept(proposed.name, proposed.version, reason="test")
    skill_name = proposed.name
    client = OneReplyClient(
        _call_reply(skill_name, {"subgoal": "s", "expected_evidence": "e", "radius": 4})
    )
    agent = ActionAgent(client, manifest=MANIFEST, skills=registry.available_skills())

    request = await agent.choose(await _observation())

    offered = {spec.name: spec for spec in client.requests[0].tools}
    assert skill_name in offered
    properties = offered[skill_name].parameters["properties"]
    assert isinstance(properties, dict) and "radius" in properties
    assert isinstance(request, SkillRequest)
    assert (request.skill_name, request.inputs) == (skill_name, {"radius": 4})
    assert agent.decisions[0].kind == "skill"
