"""Step 22.A: reliable, fast Action decisions through structured replies and thinking controls."""

from __future__ import annotations

import itertools
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fakes.connector import MANIFEST, FakeClock, ScriptedConnector, ScriptedStep
from test_loop_bench import CANDIDATE, METADATA, SKILL_SOURCE

from noob_agent.agents.action import UNUSABLE_REPLY_TOOL, ActionAgent, decision_schema
from noob_agent.connectors.doom import MANIFEST as DOOM
from noob_agent.domain.records import ExperimentRecord
from noob_agent.domain.skills import SkillPackage, SkillVersion
from noob_agent.models.client import ModelRequest, ModelResponse, WandbInferenceClient
from noob_agent.models.recording import RecordingModelClient
from noob_agent.prompts.action import ACTION_SYSTEM
from noob_agent.prompts.builder import BUILDER_SYSTEM
from noob_agent.runtime.runner import EpisodeRunner
from noob_agent.runtime.sequence import HeldOutCell, LearningSequence
from noob_agent.settings import IntegrationSettings, ModelSettings, WandbSettings
from noob_agent.skills.executor import LocalSubprocessSkillExecutor
from noob_agent.skills.registry import SkillRegistry
from noob_agent.skills.runtime import SkillRequest
from noob_agent.storage import EpisodeStore
from noob_agent.storage.schema import SCHEMA_VERSION

STARTED_AT = datetime(2026, 9, 13, 4, 0, tzinfo=UTC)
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"action": {"type": "string", "enum": ["observe"]}},
    "required": ["action"],
}


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


def _completion(content: str = "{}") -> SimpleNamespace:
    return SimpleNamespace(
        model="provider/model",
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=content))],
    )


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
    client, sent = _adapter(_completion())

    await client.complete(
        ModelRequest(system="s", prompt="p", max_output_tokens=8, thinking=thinking)
    )

    assert sent[0].get("extra_body") == expected


async def test_the_adapter_asks_for_a_strict_json_schema_reply() -> None:
    client, sent = _adapter(_completion())

    await client.complete(
        ModelRequest(system="s", prompt="p", max_output_tokens=8, response_schema=SCHEMA)
    )

    assert sent[0]["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "decision", "strict": True, "schema": SCHEMA},
    }


async def test_a_request_without_a_schema_sends_no_response_format() -> None:
    client, sent = _adapter(_completion())

    await client.complete(ModelRequest(system="s", prompt="p", max_output_tokens=8))

    assert "response_format" not in sent[0]


async def test_the_adapter_closes_its_provider_client_after_each_call() -> None:
    client, _ = _adapter(_completion())
    closed: list[bool] = []
    inner = client._client()

    async def close() -> None:
        closed.append(True)

    inner.close = close
    client._client = lambda: inner  # type: ignore[method-assign]

    await client.complete(ModelRequest(system="s", prompt="p", max_output_tokens=8))

    assert closed == [True]


# --- The decision schema ----------------------------------------------------------


def _accepted_skill(input_schema: dict[str, object]) -> SkillVersion:
    registry = SkillRegistry()
    metadata = {**METADATA, "input_schema": input_schema}
    proposed = registry.propose(
        SkillPackage(name=str(metadata["name"]), source=SKILL_SOURCE, metadata=metadata),
        authoring_episode_id="ep",
        authoring_model_id="f",
        created_at=STARTED_AT,
    )
    registry.begin_validation(proposed.name, proposed.version, reason="test")
    return registry.accept(proposed.name, proposed.version, reason="test")


def test_the_schema_allows_only_offered_primitives_and_skills() -> None:
    skill = _accepted_skill({"properties": {}})

    schema = decision_schema(MANIFEST, (skill,))

    properties = schema["properties"]
    assert isinstance(properties, dict)
    action = properties["action"]
    assert isinstance(action, dict)
    assert action["enum"] == [*[tool.name for tool in MANIFEST.tools], skill.name]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {  # type: ignore[arg-type]
        "subgoal",
        "expected_evidence",
        "action",
        "arguments",
        "finding",
    }
    assert properties["finding"] == {"anyOf": [{"type": "object"}, {"type": "null"}]}


def test_a_skill_named_like_a_primitive_is_offered_once() -> None:
    skill = _accepted_skill({"properties": {}}).model_copy(update={"name": "observe"})

    schema = decision_schema(MANIFEST, (skill,))

    action = schema["properties"]["action"]  # type: ignore[index]
    assert action["enum"].count("observe") == 1  # type: ignore[index]


# --- The Action agent -----------------------------------------------------------


class ScriptedClient:
    provider = "scripted"

    def __init__(self, *responses: ModelResponse) -> None:
        self._responses = list(responses)
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return self._responses.pop(0)


def _reply(
    action: str,
    arguments: dict[str, object] | None = None,
    *,
    finding: object = None,
    finish_reason: str = "stop",
) -> ModelResponse:
    text = json.dumps(
        {
            "subgoal": "Find the post.",
            "expected_evidence": "Lantern visible.",
            "action": action,
            "arguments": arguments or {},
            "finding": finding,
        }
    )
    return ModelResponse(
        text=text, input_tokens=10, output_tokens=5, model_id="f", finish_reason=finish_reason
    )


async def _observation() -> Any:
    return await ScriptedConnector().reset("mc_signal_post_a", 7)


async def test_the_agent_asks_for_the_decision_schema_with_its_thinking_setting() -> None:
    client = ScriptedClient(_reply("observe"))
    agent = ActionAgent(client, manifest=MANIFEST, thinking=False)

    await agent.choose(await _observation())

    request = client.requests[0]
    assert request.system == ACTION_SYSTEM
    assert request.thinking is False
    assert request.response_schema == decision_schema(MANIFEST, ())


async def test_a_structured_primitive_reply_becomes_a_tool_request() -> None:
    agent = ActionAgent(ScriptedClient(_reply("observe", {"radius": 8})), manifest=MANIFEST)

    request = await agent.choose(await _observation())

    assert not isinstance(request, SkillRequest)
    assert (request.tool_name, request.arguments) == ("observe", {"radius": 8})
    decision = agent.decisions[0]
    assert (decision.kind, decision.subgoal, decision.expected_evidence) == (
        "primitive",
        "Find the post.",
        "Lantern visible.",
    )


async def test_a_structured_skill_reply_becomes_a_skill_request() -> None:
    skill = _accepted_skill({"properties": {"radius": {"type": "integer"}}})
    agent = ActionAgent(
        ScriptedClient(_reply(skill.name, {"radius": 4})), manifest=MANIFEST, skills=(skill,)
    )

    request = await agent.choose(await _observation())

    assert isinstance(request, SkillRequest)
    assert (request.skill_name, request.inputs) == (skill.name, {"radius": 4})
    assert agent.decisions[0].kind == "skill"


async def test_a_finding_in_a_structured_reply_is_kept_on_the_decision() -> None:
    finding = {
        "expected_behavior": "One output.",
        "expected_basis": "Earlier use produced one.",
        "actual_behavior": "Two outputs.",
        "expected_count": 1,
        "actual_count": 2,
        "evidence": [{"kind": "action_id", "value": "a_0001"}],
    }
    agent = ActionAgent(ScriptedClient(_reply("observe", finding=finding)), manifest=MANIFEST)

    await agent.choose(await _observation())

    assert agent.decisions[0].finding is not None


async def test_the_older_tool_and_skill_reply_shapes_still_work() -> None:
    text = json.dumps(
        {"subgoal": "s", "expected_evidence": "e", "tool": "observe", "arguments": {"radius": 3}}
    )
    agent = ActionAgent(
        ScriptedClient(ModelResponse(text=text, input_tokens=1, output_tokens=1, model_id="f")),
        manifest=MANIFEST,
    )

    request = await agent.choose(await _observation())

    assert not isinstance(request, SkillRequest)
    assert (request.tool_name, request.arguments) == ("observe", {"radius": 3})


async def test_a_reply_cut_off_at_its_cap_is_unusable_and_marked_truncated() -> None:
    capped = ModelResponse(
        text='{"subgoal": "s", "act',
        input_tokens=1,
        output_tokens=64,
        model_id="f",
        finish_reason="length",
    )
    finished = ModelResponse(text="no decision", input_tokens=1, output_tokens=2, model_id="f")
    agent = ActionAgent(ScriptedClient(capped, finished), manifest=MANIFEST)
    observation = await _observation()

    first = await agent.choose(observation)
    second = await agent.choose(observation)

    assert not isinstance(first, SkillRequest) and not isinstance(second, SkillRequest)
    assert first.tool_name == second.tool_name == UNUSABLE_REPLY_TOOL
    assert [decision.truncated for decision in agent.decisions] == [True, False]


# --- Keeping each decision within the protocol's token budget ---------------------


async def test_the_prompt_lists_tools_with_compact_argument_hints() -> None:
    client = ScriptedClient(_reply("observe"))
    agent = ActionAgent(client, manifest=DOOM)

    await agent.choose(await _observation())

    prompt = client.requests[0].prompt
    assert "- observe(): Read the visible HUD and objects." in prompt
    assert "- turn_left(degrees: integer 1-90): Turn left by a degree amount." in prompt
    assert "- attack(ticks?: integer 1-35): Fire or attack for a number of ticks." in prompt
    assert '"additionalProperties"' not in prompt


async def test_the_observation_omits_values_the_prompt_already_carries() -> None:
    client = ScriptedClient(_reply("observe"))
    agent = ActionAgent(client, manifest=MANIFEST)
    observation = await _observation()

    await agent.choose(observation)

    prompt = client.requests[0].prompt
    assert prompt.count(observation.public_goal) == 1
    assert '"visible_objects"' in prompt and '"status"' in prompt


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


async def test_request_options_are_recorded_with_each_call(
    store: EpisodeStore, experiment: ExperimentRecord
) -> None:
    short = experiment.model_copy(update={"decision_budget": 1})
    store.create_experiment(short)
    recorder = RecordingModelClient(
        ScriptedClient(_reply("observe")),
        store=store,
        experiment_id=short.experiment_id,
        role="action",
        model_id="fake",
        clock=FakeClock(wall=STARTED_AT),
    )
    runner = EpisodeRunner(
        connector=ScriptedConnector(ScriptedStep()),
        store=store,
        policy=ActionAgent(recorder, manifest=MANIFEST, thinking=False),
        clock=FakeClock(wall=STARTED_AT),
    )

    result = await runner.run(experiment=short, scenario_id="mc_signal_post_a", seed=7)

    (record,) = store.read_model_calls(episode_id=result.episode_id)
    assert record.request_options == {
        "thinking": False,
        "response_schema": decision_schema(MANIFEST, ()),
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
def test_an_older_database_gains_the_new_column_and_keeps_its_calls(
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
    assert old.request_options is None
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(model_call)")}
        (version,) = connection.execute("SELECT version FROM schema_version").fetchone()
    assert "request_options_json" in columns
    assert version == 5


# --- The learning sequence --------------------------------------------------------


async def test_the_sequence_sends_the_action_thinking_setting_on_every_action_call(
    store: EpisodeStore,
) -> None:
    class Routing:
        provider = "scripted"

        def __init__(self) -> None:
            self.requests: list[ModelRequest] = []

        async def complete(self, request: ModelRequest) -> ModelResponse:
            self.requests.append(request)
            if request.system == BUILDER_SYSTEM:
                return ModelResponse(text=CANDIDATE, input_tokens=1, output_tokens=1, model_id="f")
            return _reply("observe")

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
    assert all(request.response_schema is not None for request in actions)
    heldout_enum = actions[-1].response_schema["properties"]["action"]["enum"]  # type: ignore[index]
    assert METADATA["name"] in heldout_enum


# --- Compact observation (section 24c) -------------------------------------------


def _minecraft_sized_observation() -> Any:
    from noob_agent.domain.model import (
        Observation,
        PublicMessage,
        PublicPlayerState,
        PublicPosition,
        VisibleObject,
    )

    return Observation(
        episode_id="ep_mc",
        sequence=4,
        game_id="minecraft",
        scenario_id="resonator-training-v1",
        public_goal="Open the gateway.",
        status={"health": 20.0, "food": 20, "held_item": None, "effects": []},
        player=PublicPlayerState(
            position=PublicPosition(x=0.51234, y=100.0, z=0.5),
            orientation={"yaw": 1.570796, "pitch": 0.0},
            properties={"sneaking": False, "mount": None},
        ),
        visible_objects=tuple(
            VisibleObject(
                object_id=f"obj_{index:04d}",
                label="Barrel" if index % 2 else "Hopper",
                position=PublicPosition(x=-4.0 + index, y=100.0, z=float(index)),
                distance=4.512345 + index,
                properties={
                    "enabled": None,
                    "facing": "east",
                    "kind": "block",
                    "open": False,
                    "powered": None,
                    "contents": [],
                },
            )
            for index in range(17)
        ),
        messages=(PublicMessage(kind="observation", text="Nearby state refreshed."),),
        last_action_id=None,
        terminal=False,
        terminal_reason=None,
        logical_time=1200,
    )


def _compact_expected(value: Any) -> Any:
    if isinstance(value, dict):
        kept = {k: _compact_expected(v) for k, v in value.items()}
        return {k: v for k, v in kept.items() if v is not None and v != [] and v != {}}
    if isinstance(value, list):
        return [_compact_expected(v) for v in value]
    if isinstance(value, float):
        return round(value, 2)
    return value


def _rendered_observation(prompt: str) -> Any:
    marker = prompt.index("Current observation")
    start = prompt.index("{", marker)
    decoded, _ = json.JSONDecoder().raw_decode(prompt[start:])
    return decoded


def test_the_observation_is_rendered_compactly_without_losing_a_value() -> None:
    from noob_agent.prompts.action import render_action_prompt

    observation = _minecraft_sized_observation()
    prompt = render_action_prompt(
        public_goal=observation.public_goal,
        observation=observation,
        tools=MANIFEST.tools,
        skills=(),
        history=(),
    )

    rendered = _rendered_observation(prompt)
    original = observation.model_dump(mode="json", exclude={"public_goal"})
    assert rendered == _compact_expected(original)
    assert len(rendered["visible_objects"]) == 17
    assert '": ' not in prompt[prompt.index("Current observation") :]


def test_a_minecraft_sized_observation_prompt_is_at_least_a_fifth_shorter() -> None:
    from noob_agent.prompts.action import render_action_prompt

    observation = _minecraft_sized_observation()
    prompt = render_action_prompt(
        public_goal=observation.public_goal,
        observation=observation,
        tools=MANIFEST.tools,
        skills=(),
        history=(),
    )
    verbose = json.dumps(
        observation.model_dump(mode="json", exclude={"public_goal"}), sort_keys=True
    )
    compact = prompt[prompt.index("{", prompt.index("Current observation")) :]

    assert len(compact) <= 0.8 * len(verbose)


def test_history_subgoals_are_cut_to_one_hundred_characters() -> None:
    from noob_agent.prompts.action import HistoryEntry, render_action_prompt

    observation = _minecraft_sized_observation()
    long_subgoal = "s" * 300
    prompt = render_action_prompt(
        public_goal=observation.public_goal,
        observation=observation,
        tools=MANIFEST.tools,
        skills=(),
        history=(
            HistoryEntry(
                action_id="a_0001",
                subgoal=long_subgoal,
                chosen="observe({})",
                outcome="succeeded/OK",
            ),
        ),
    )

    assert "s" * 101 not in prompt
    assert "s" * 97 in prompt
