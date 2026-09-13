"""A held-out episode: fresh reset, fresh conversation, and the accepted skill in play."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fakes.connector import FakeClock, ScriptedConnector, ScriptedStep
from pydantic import JsonValue

from noob_agent.agents.action import ActionAgent, parse_decision
from noob_agent.domain.records import ExperimentRecord
from noob_agent.domain.skills import SkillPackage, SkillVersion
from noob_agent.models.client import ModelRequest, ModelResponse
from noob_agent.prompts.action import ACTION_SYSTEM, render_action_prompt
from noob_agent.runtime.heldout import (
    HELD_OUT_DECISION_BUDGET,
    HELD_OUT_PRIMITIVE_BUDGET,
    HELD_OUT_WALL_TIME_MS,
    HeldOutRunner,
    heldout_experiment,
)
from noob_agent.skills import SkillRegistry
from noob_agent.skills.executor import LocalSubprocessSkillExecutor
from noob_agent.storage import EpisodeStore

STARTED_AT = datetime(2026, 9, 12, 19, 0, 0, tzinfo=UTC)
LAYOUT_A = "mc_resonator_layout_a"
LAYOUT_B = "mc_resonator_layout_b"

ACCEPTED = "activate_visible_lantern"
PROPOSED = "count_visible_objects"
REJECTED = "read_the_environment"
RETIRED = "older_lantern_routine"

SKILL_SOURCE = '''"""Use one visible object and report what happened."""

from noob_agent.skills.contract import EvidenceRef, SkillContext, SkillResult


async def run(context: SkillContext, inputs: dict[str, object]) -> SkillResult:
    """Use the object named by `object_id` and check the visible result."""
    object_id = inputs.get("object_id")
    if not isinstance(object_id, str):
        return SkillResult(status="failed", summary="object_id is required.",
                           primitive_actions_used=0)
    result = await context.call("use_object", object_id=object_id)
    if result.status != "succeeded":
        return SkillResult(status="failed", summary=f"use_object was {result.status}.",
                           primitive_actions_used=result.primitive_actions_charged)
    return SkillResult(
        status="succeeded",
        summary="Used the object.",
        evidence=(EvidenceRef(kind="action_id", value=result.action_id),),
        outputs={"terminal": result.observation.terminal},
        primitive_actions_used=result.primitive_actions_charged,
    )
'''

# Calls observe three times, which is more than a two-primitive episode allows.
HUNGRY_SOURCE = '''"""Observe repeatedly."""

from noob_agent.skills.contract import SkillContext, SkillResult


async def run(context: SkillContext, inputs: dict[str, object]) -> SkillResult:
    """Spend primitives on observation until refused."""
    codes = []
    for _ in range(3):
        result = await context.call("observe", radius=3)
        codes.append(result.code)
    return SkillResult(status="inconclusive", summary=" ".join(codes),
                       primitive_actions_used=2)
'''


def metadata(name: str, purpose: str) -> dict[str, JsonValue]:
    return {
        "name": name,
        "version": 1,
        "parent_version": None,
        "purpose": purpose,
        "input_schema": {"properties": {"object_id": {"type": "string"}}},
        "required_tools": ["observe", "use_object"],
        "max_primitive_actions": 8,
        "max_wall_time_seconds": 30,
        "success_claim": "The used object reports a visible change.",
        "api_version": "noob-agent.skill.v1",
    }


def propose(registry: SkillRegistry, name: str, purpose: str, source: str) -> SkillVersion:
    return registry.propose(
        SkillPackage(name=name, source=source, metadata=metadata(name, purpose)),
        authoring_episode_id="ep_training_0001",
        authoring_model_id="fake-model-a",
        created_at=STARTED_AT,
    )


def accept(registry: SkillRegistry, name: str, purpose: str, source: str) -> SkillVersion:
    proposed = propose(registry, name, purpose, source)
    registry.begin_validation(name, proposed.version, reason="test")
    return registry.accept(name, proposed.version, reason="test")


@pytest.fixture
def registry() -> SkillRegistry:
    """One accepted skill among versions in every other state."""
    registry = SkillRegistry()
    accept(registry, ACCEPTED, "Use the visible lantern and confirm it lit.", SKILL_SOURCE)
    propose(registry, PROPOSED, "Count what is nearby.", SKILL_SOURCE)
    rejected = propose(registry, REJECTED, "Read the environment.", SKILL_SOURCE)
    registry.begin_validation(REJECTED, rejected.version, reason="test")
    registry.reject(REJECTED, rejected.version, reason="forbidden import")
    retired = accept(registry, RETIRED, "An older routine.", SKILL_SOURCE)
    registry.retire(RETIRED, retired.version, reason="superseded")
    return registry


class ScriptedModelClient:
    """A deterministic stand-in for a provider: no credentials, no network."""

    def __init__(self, *replies: str) -> None:
        self._replies = list(replies)
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._replies:
            raise AssertionError("The agent asked for more replies than were scripted.")
        return ModelResponse(
            text=self._replies.pop(0), input_tokens=200, output_tokens=60, model_id="fake-model-a"
        )


def primitive(tool: str, **arguments: JsonValue) -> str:
    return json.dumps(
        {
            "subgoal": f"Try {tool}.",
            "expected_evidence": "A fresh observation.",
            "tool": tool,
            "arguments": arguments,
        }
    )


def skill(name: str, **inputs: JsonValue) -> str:
    return (
        "Invoking the learned skill.\n```json\n"
        + json.dumps(
            {
                "subgoal": "Light the lantern with the learned routine.",
                "expected_evidence": "The lantern reports lit.",
                "skill": name,
                "inputs": inputs,
            }
        )
        + "\n```"
    )


def experiment(**overrides: int) -> ExperimentRecord:
    budgets = {
        "decision_budget": HELD_OUT_DECISION_BUDGET,
        "primitive_budget": HELD_OUT_PRIMITIVE_BUDGET,
        "wall_time_budget_ms": HELD_OUT_WALL_TIME_MS,
    } | overrides
    return ExperimentRecord(
        experiment_id="exp_heldout_0001",
        model_id="fake-model-a",
        condition="self-improving",
        connector_version="fake-minecraft-0.1.0",
        created_at=STARTED_AT,
        **budgets,
    )


async def run_heldout(
    store: EpisodeStore,
    registry: SkillRegistry,
    connector: ScriptedConnector,
    client: ScriptedModelClient,
    *,
    scenario_id: str = LAYOUT_A,
    experiment_record: ExperimentRecord | None = None,
):
    record = experiment_record or experiment()
    store.create_experiment(record)
    runner = HeldOutRunner(
        connector=connector,
        store=store,
        registry=registry,
        client=client,
        executor=LocalSubprocessSkillExecutor(),
        clock=FakeClock(wall=STARTED_AT),
    )
    return await runner.run(experiment=record, scenario_id=scenario_id, seed=11)


async def test_only_accepted_skills_are_offered(
    store: EpisodeStore, registry: SkillRegistry
) -> None:
    connector = ScriptedConnector(ScriptedStep(terminal=True, terminal_reason="goal_reached"))
    client = ScriptedModelClient(primitive("observe", radius=8))

    result = await run_heldout(store, registry, connector, client)

    assert [offered.name for offered in result.offered_skills] == [ACCEPTED]
    prompt = client.requests[0].prompt
    assert ACCEPTED in prompt
    assert "Use the visible lantern and confirm it lit." in prompt
    for hidden in (PROPOSED, REJECTED, RETIRED, "forbidden import", "superseded"):
        assert hidden not in prompt
    assert client.requests[0].system == ACTION_SYSTEM


async def test_the_agent_invokes_the_skill_and_the_episode_records_it(
    store: EpisodeStore, registry: SkillRegistry
) -> None:
    connector = ScriptedConnector(
        ScriptedStep(state_changed=True, terminal=True, terminal_reason="goal_reached")
    )
    client = ScriptedModelClient(skill(ACCEPTED, object_id="obj_1"))

    result = await run_heldout(store, registry, connector, client)

    assert result.episode.stop_reason == "terminal_state"
    assert result.episode.terminal is True
    assert result.episode.decisions_used == 1
    assert result.episode.primitives_used == 1
    assert connector.reset_calls == [(LAYOUT_A, 11)]

    stored = store.read_episode(result.episode.episode_id)
    assert stored.episode.split == "held-out"
    assert [step.request.action_id for step in stored.steps] == ["a_0001.1"]
    assert stored.steps[0].request.tool_name == "use_object"
    assert stored.outcome is not None
    assert stored.outcome.total_decisions == 1
    assert stored.outcome.total_primitives == 1

    (use,) = result.skill_uses
    assert use.skill_name == ACCEPTED
    assert use.status == "completed"
    assert use.success_check_passed is True
    assert use.isolation == "local-subprocess"
    assert use.inputs == {"object_id": "obj_1"}

    (decision,) = result.decisions
    assert decision.subgoal == "Light the lantern with the learned routine."
    assert decision.expected_evidence == "The lantern reports lit."
    assert decision.kind == "skill"


async def test_reset_clears_the_model_conversation(
    store: EpisodeStore, registry: SkillRegistry
) -> None:
    """Nothing from one episode's conversation survives into the next."""
    first = ScriptedConnector(
        ScriptedStep(),
        ScriptedStep(terminal=True, terminal_reason="goal_reached"),
        episode_id="ep_layout_a",
    )
    client = ScriptedModelClient(
        primitive("observe", radius=8),
        primitive("observe", radius=2),
        primitive("observe", radius=8),
    )
    runner_store = store
    runner_store.create_experiment(experiment())
    runner = HeldOutRunner(
        connector=first,
        store=runner_store,
        registry=registry,
        client=client,
        executor=LocalSubprocessSkillExecutor(),
        clock=FakeClock(wall=STARTED_AT),
    )
    await runner.run(experiment=experiment(), scenario_id=LAYOUT_A, seed=11)
    second_prompt_of_first_episode = client.requests[1].prompt
    assert LAYOUT_A in second_prompt_of_first_episode
    assert "a_0001" in second_prompt_of_first_episode

    second = ScriptedConnector(
        ScriptedStep(terminal=True, terminal_reason="goal_reached"), episode_id="ep_layout_b"
    )
    runner = HeldOutRunner(
        connector=second,
        store=runner_store,
        registry=registry,
        client=client,
        executor=LocalSubprocessSkillExecutor(),
        clock=FakeClock(wall=STARTED_AT),
    )
    await runner.run(experiment=experiment(), scenario_id=LAYOUT_B, seed=12)

    fresh_prompt = client.requests[2].prompt
    assert LAYOUT_B in fresh_prompt
    assert LAYOUT_A not in fresh_prompt
    assert "ep_layout_a" not in fresh_prompt
    assert "a_0001" not in fresh_prompt
    assert "a_0002" not in fresh_prompt


async def test_an_agent_reset_forgets_its_own_history() -> None:
    connector = ScriptedConnector(ScriptedStep(), ScriptedStep())
    client = ScriptedModelClient(primitive("observe", radius=8), primitive("observe", radius=8))
    agent = ActionAgent(client, manifest=await connector.manifest())
    observation = await connector.reset(LAYOUT_A, 1)

    request = await agent.choose(observation)
    result = await connector.step(request)
    agent.notice(request, result)
    assert len(agent.decisions) == 1

    agent.reset()

    assert agent.decisions == ()
    await agent.choose(result.observation)
    assert "a_0001" in client.requests[1].prompt  # fresh numbering, no history
    assert "History" not in client.requests[1].prompt or "no earlier" in client.requests[1].prompt


async def test_budgets_still_stop_the_episode_when_a_skill_is_in_play(
    store: EpisodeStore,
) -> None:
    registry = SkillRegistry()
    accept(registry, ACCEPTED, "Observe repeatedly.", HUNGRY_SOURCE)
    connector = ScriptedConnector(ScriptedStep(), ScriptedStep(), ScriptedStep())
    client = ScriptedModelClient(skill(ACCEPTED, object_id="obj_1"))

    result = await run_heldout(
        store, registry, connector, client, experiment_record=experiment(primitive_budget=2)
    )

    assert result.episode.stop_reason == "primitive_limit"
    assert result.episode.decisions_used == 1
    assert result.episode.primitives_used == 2
    assert len(connector.requests) == 2
    stored = store.read_episode(result.episode.episode_id)
    assert stored.outcome is not None
    assert stored.outcome.stop_reason == "primitive_limit"
    assert stored.outcome.total_primitives == 2
    (use,) = result.skill_uses
    assert use.result is not None
    assert use.result.summary == "OK OK BUDGET_EXHAUSTED"


async def test_a_held_out_scenario_is_never_handed_to_validation(
    store: EpisodeStore, registry: SkillRegistry
) -> None:
    """A held-out failure neither repairs, re-validates, nor re-records the skill."""
    before = {
        name: tuple((v.version, v.status, v.content_hash) for v in registry.versions(name))
        for name in (ACCEPTED, PROPOSED, REJECTED, RETIRED)
    }
    connector = ScriptedConnector(
        ScriptedStep(status="failed", code="UNREACHABLE", state_changed=False),
        ScriptedStep(terminal=True, terminal_reason="goal_reached"),
    )
    client = ScriptedModelClient(skill(ACCEPTED, object_id="obj_1"), primitive("observe", radius=8))

    result = await run_heldout(store, registry, connector, client)

    (use,) = result.skill_uses
    assert use.result is not None
    assert use.result.status == "failed"
    after = {
        name: tuple((v.version, v.status, v.content_hash) for v in registry.versions(name))
        for name in (ACCEPTED, PROPOSED, REJECTED, RETIRED)
    }
    assert after == before
    assert store.read_episode(result.episode.episode_id).episode.split == "held-out"
    # The failure is reported back to the agent, not to a Builder.
    assert "use_object was failed." in client.requests[1].prompt


async def test_an_unusable_reply_costs_a_decision_but_no_primitive(
    store: EpisodeStore, registry: SkillRegistry
) -> None:
    connector = ScriptedConnector(ScriptedStep(terminal=True, terminal_reason="goal_reached"))
    client = ScriptedModelClient(
        "I would rather think about this some more.",
        primitive("observe", radius=8),
    )

    result = await run_heldout(store, registry, connector, client)

    assert result.episode.decisions_used == 2
    assert result.episode.primitives_used == 1
    stored = store.read_episode(result.episode.episode_id)
    assert [step.result.status for step in stored.steps] == ["rejected", "succeeded"]
    assert stored.steps[0].result.code == "INVALID_TOOL"
    assert result.skill_uses == ()


async def test_a_skill_the_agent_names_but_was_not_offered_is_rejected(
    store: EpisodeStore, registry: SkillRegistry
) -> None:
    connector = ScriptedConnector(ScriptedStep(terminal=True, terminal_reason="goal_reached"))
    client = ScriptedModelClient(skill(PROPOSED, object_id="obj_1"), primitive("observe", radius=8))

    result = await run_heldout(store, registry, connector, client)

    assert result.episode.decisions_used == 2
    assert result.episode.primitives_used == 1
    stored = store.read_episode(result.episode.episode_id)
    assert stored.steps[0].request.tool_name == PROPOSED
    assert stored.steps[0].result.code == "INVALID_TOOL"
    assert result.skill_uses == ()


async def test_experiment_budgets_cannot_exceed_the_held_out_limits(
    store: EpisodeStore, registry: SkillRegistry
) -> None:
    connector = ScriptedConnector(ScriptedStep(terminal=True, terminal_reason="goal_reached"))
    client = ScriptedModelClient(primitive("observe", radius=8))

    with pytest.raises(ValueError):
        await run_heldout(
            store,
            registry,
            connector,
            client,
            experiment_record=experiment(decision_budget=HELD_OUT_DECISION_BUDGET + 1),
        )
    assert connector.reset_calls == []


def test_heldout_experiment_freezes_the_contract_budgets() -> None:
    record = heldout_experiment(
        experiment_id="exp_heldout_0002",
        model_id="fake-model-a",
        connector_version="fake-minecraft-0.1.0",
        created_at=STARTED_AT,
    )
    assert record.condition == "self-improving"
    assert record.decision_budget == 12
    assert record.primitive_budget == 24
    assert record.wall_time_budget_ms == 90_000


def test_parse_decision_reads_a_fenced_or_bare_json_object() -> None:
    fenced = parse_decision(skill(ACCEPTED, object_id="obj_1"))
    assert fenced.kind == "skill"
    assert fenced.name == ACCEPTED
    assert fenced.arguments == {"object_id": "obj_1"}

    bare = parse_decision(primitive("observe", radius=8))
    assert bare.kind == "primitive"
    assert bare.name == "observe"
    assert bare.arguments == {"radius": 8}

    unusable = parse_decision("no json here")
    assert unusable.kind == "unusable"


async def test_the_action_prompt_carries_no_private_state(
    store: EpisodeStore, registry: SkillRegistry
) -> None:
    connector = ScriptedConnector(ScriptedStep(terminal=True, terminal_reason="goal_reached"))
    client = ScriptedModelClient(primitive("observe", radius=8))

    await run_heldout(store, registry, connector, client)

    prompt = client.requests[0].prompt.lower()
    for private in ("faulty", "clean", "grader", "predicate", "validation", "training trace"):
        assert private not in prompt
    rendered = render_action_prompt(
        public_goal="Light the lantern on the post.",
        observation=await connector.reset(LAYOUT_A, 1),
        tools=(await connector.manifest()).tools,
        skills=registry.available_skills(),
        history=(),
    )
    assert "Light the lantern on the post." in rendered
    assert ACCEPTED in rendered
