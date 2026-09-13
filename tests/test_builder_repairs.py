"""Step 22.C: repairs that converge within the protocol's learning budget."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from test_builder_prompts import _doom_like_episode

from noob_agent.agents.builder import BuilderAgent
from noob_agent.agents.evidence import select_evidence
from noob_agent.connectors.doom import MANIFEST as DOOM_MANIFEST
from noob_agent.models.client import ModelRequest, ModelResponse
from noob_agent.prompts.builder import (
    BUILDER_SYSTEM,
    DEFAULT_REPAIR_MAX_OUTPUT_TOKENS,
    PROMPT_TOKEN_LIMIT,
    estimated_tokens,
    render_repair_prompt,
)
from noob_agent.skills.errors import SkillValidationIssue
from noob_agent.skills.executor import LocalSubprocessSkillExecutor
from noob_agent.skills.package import MAX_SOURCE_BYTES
from noob_agent.skills.registry import SkillRegistry

AUTHORED_AT = datetime(2026, 9, 13, 6, 0, tzinfo=UTC)
NAMES = tuple(tool.name for tool in DOOM_MANIFEST.tools)

# Loads and runs, but calls a primitive its metadata never declares.
UNDECLARED_SOURCE = '''from noob_agent.skills.contract import SkillContext, SkillResult


async def run(context: SkillContext, inputs: dict[str, object]) -> SkillResult:
    """Fire once."""
    if inputs:
        return SkillResult(status="failed", summary="No inputs.", primitive_actions_used=0)
    result = await context.call("attack", ticks=1)
    return SkillResult(status="failed", summary=result.code,
                       primitive_actions_used=result.primitive_actions_charged)
'''
METADATA = {
    "name": "fire_once",
    "version": 1,
    "parent_version": None,
    "purpose": "Fire once.",
    "input_schema": {"properties": {}},
    "required_tools": ["observe"],
    "max_primitive_actions": 2,
    "max_wall_time_seconds": 5,
    "success_claim": "Never claims success.",
    "api_version": "noob-agent.skill.v1",
}


def _candidate(source: str = UNDECLARED_SOURCE) -> str:
    return f"```python\n{source}```\n\n```json\n{json.dumps(METADATA)}\n```\n"


class Scripted:
    provider = "scripted"

    def __init__(self, *responses: ModelResponse) -> None:
        self._responses = list(responses)
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return self._responses.pop(0)


def _reply(text: str, *, finish_reason: str = "stop", output_tokens: int = 500) -> ModelResponse:
    return ModelResponse(
        text=text,
        input_tokens=2_000,
        output_tokens=output_tokens,
        model_id="f",
        finish_reason=finish_reason,
    )


async def _build(client: Scripted, **options: Any) -> Any:
    stored = _doom_like_episode(steps=3, objects=2)
    spent = {key: options.pop(key) for key in list(options) if key.startswith("spent_")}
    agent = BuilderAgent(
        client,
        SkillRegistry(),
        executor=LocalSubprocessSkillExecutor(),
        max_repairs=options.pop("max_repairs", 1),
        **options,
    )
    return await agent.build(
        select_evidence(stored),
        training_trace=stored,
        primitive_names=NAMES,
        authoring_model_id="f",
        created_at=AUTHORED_AT,
        **spent,
    )


def _issue(evidence_size: int = 0) -> SkillValidationIssue:
    return SkillValidationIssue(
        check="training_replay",
        code="undeclared_tool",
        message="The skill called `attack`, which required_tools does not declare.",
        fixture="training_replay",
        public_inputs={},
        public_result={"summary": "x" * evidence_size},
        public_logs=({"event": "y" * evidence_size},),
    )


async def test_a_repair_carries_definitions_evidence_source_and_failure() -> None:
    client = Scripted(_reply(_candidate()), _reply("no candidate"))

    await _build(client)

    repair = client.requests[1]
    assert repair.system == BUILDER_SYSTEM
    for tool in DOOM_MANIFEST.tools:
        assert tool.description in repair.prompt
    assert "screen_offset" in repair.prompt
    assert UNDECLARED_SOURCE.strip() in repair.prompt
    assert "Failing checks" in repair.prompt


async def test_repairs_use_their_own_smaller_default_cap() -> None:
    client = Scripted(_reply(_candidate()), _reply("no candidate"))

    await _build(client)

    assert client.requests[0].max_output_tokens == 6_000
    assert client.requests[1].max_output_tokens == DEFAULT_REPAIR_MAX_OUTPUT_TOKENS == 3_000


def test_a_repair_of_a_maximum_size_skill_fits_the_prompt_budget() -> None:
    stored = _doom_like_episode(steps=40, objects=20, message="z" * 5_000)
    source = "# " + "s" * (MAX_SOURCE_BYTES - 3) + "\n"
    issues = tuple(_issue(evidence_size=5_000) for _ in range(8))

    rendered = render_repair_prompt(
        previous_source=source,
        issues=issues,
        evidence=select_evidence(stored),
        primitive_names=NAMES,
        tools=DOOM_MANIFEST.tools,
    )

    assert source.strip() in rendered
    assert estimated_tokens(BUILDER_SYSTEM) + estimated_tokens(rendered) <= PROMPT_TOKEN_LIMIT


def test_a_small_repair_keeps_every_optional_section() -> None:
    stored = _doom_like_episode(steps=3, objects=2)

    rendered = render_repair_prompt(
        previous_source=UNDECLARED_SOURCE,
        issues=(_issue(),),
        evidence=select_evidence(stored),
        primitive_names=NAMES,
        tools=DOOM_MANIFEST.tools,
    )

    assert "Primitive tools:" in rendered
    assert "What happened" in rendered


@pytest.mark.parametrize("stage", ["build", "repair"])
async def test_a_reply_cut_off_at_its_cap_stops_with_truncated_reply(stage: str) -> None:
    capped = _reply("```python\nasync def run(", finish_reason="length", output_tokens=6_000)
    client = Scripted(capped) if stage == "build" else Scripted(_reply(_candidate()), capped)

    outcome = await _build(client, max_repairs=3)

    assert outcome.accepted is False
    assert outcome.stop_reason == "truncated_reply"
    assert len(client.requests) == (1 if stage == "build" else 2)


async def test_an_unusable_reply_that_finished_normally_is_still_unusable() -> None:
    outcome = await _build(Scripted(_reply("I cannot write that.")))

    assert outcome.stop_reason == "unusable_reply"


async def test_the_learning_budget_stops_the_builder_before_an_unsent_call() -> None:
    heavy_build = ModelResponse(
        text=_candidate(), input_tokens=5_000, output_tokens=1_000, model_id="f"
    )
    client = Scripted(heavy_build, _reply(_candidate()))

    outcome = await _build(
        client,
        max_repairs=3,
        learning_token_budget=60_000,
        learning_call_budget=22,
        spent_tokens=50_000,
        spent_calls=20,
    )

    # 50,000 spent: the build's projection (prompt plus its 6,000 cap) fits under 60,000.
    # After the build's 6,000 tokens, a repair's projection (prompt plus 3,000) does not.
    assert len(client.requests) == 1
    assert outcome.stop_reason == "learning_budget_exhausted"
    assert outcome.accepted is False


async def test_the_call_budget_is_enforced_too() -> None:
    client = Scripted(_reply(_candidate()))

    outcome = await _build(
        client, learning_token_budget=60_000, learning_call_budget=22, spent_calls=22
    )

    assert client.requests == []
    assert outcome.stop_reason == "learning_budget_exhausted"
    assert outcome.attempts == 0


async def test_a_repair_that_renames_the_skill_is_rejected_not_a_crash() -> None:
    renamed = _candidate().replace('"fire_once"', '"fire_twice"')
    client = Scripted(_reply(_candidate()), _reply(renamed), _reply("no candidate"))

    outcome = await _build(client, max_repairs=2)

    assert outcome.accepted is False
    assert len(client.requests) == 3
    third = client.requests[2].prompt
    assert "renamed_repair" in third
    assert "fire_once" in third
    assert UNDECLARED_SOURCE.strip() in third


async def test_the_repair_prompt_names_the_skill_to_keep() -> None:
    client = Scripted(_reply(_candidate()), _reply("no candidate"))

    await _build(client)

    assert "Keep the skill name `fire_once`" in client.requests[1].prompt


async def test_the_sequence_charges_training_spend_to_the_builder_budget(store: Any) -> None:
    import itertools

    from fakes.connector import FakeClock, ScriptedConnector, ScriptedStep
    from test_loop_bench import CANDIDATE

    from noob_agent.runtime.sequence import HeldOutCell, LearningSequence

    class Heavy:
        provider = "scripted"

        def __init__(self) -> None:
            self.requests: list[ModelRequest] = []

        async def complete(self, request: ModelRequest) -> ModelResponse:
            self.requests.append(request)
            if request.system == BUILDER_SYSTEM:
                return ModelResponse(text=CANDIDATE, input_tokens=1, output_tokens=1, model_id="f")
            decision = {
                "subgoal": "s",
                "expected_evidence": "e",
                "action": "observe",
                "arguments": {},
                "finding": None,
            }
            return ModelResponse(
                text=json.dumps(decision), input_tokens=28_000, output_tokens=100, model_id="f"
            )

    ids = itertools.count(1)
    client = Heavy()
    result = await LearningSequence(
        connector_factory=lambda: ScriptedConnector(
            ScriptedStep(),
            ScriptedStep(terminal=True, terminal_reason="done"),
            episode_id=f"ep_heavy_{next(ids):03d}",
        ),
        client=client,
        model_id="f",
        store=store,
        registry=SkillRegistry(),
        executor=LocalSubprocessSkillExecutor(),
        grade=lambda stored, connector: None,
        clock=FakeClock(wall=AUTHORED_AT),
    ).run(
        sequence_id="heavy",
        training_scenario_id="train",
        training_seed=1,
        heldout=(HeldOutCell("held", 2),),
    )

    assert result.builder.stop_reason == "learning_budget_exhausted"
    assert not [request for request in client.requests if request.system == BUILDER_SYSTEM]
