"""Step 22.B: a Builder prompt grounded in the real skill API and public observations."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from noob_agent.agents.evidence import select_evidence
from noob_agent.connectors.doom import MANIFEST as DOOM_MANIFEST
from noob_agent.connectors.minecraft import MANIFEST as MINECRAFT_MANIFEST
from noob_agent.domain.model import (
    ConnectorManifest,
    Observation,
    PublicMessage,
    StepResult,
    ToolRequest,
    VisibleObject,
)
from noob_agent.domain.records import EpisodeOutcome, EpisodeRecord, StepRecord, StoredEpisode
from noob_agent.prompts.builder import (
    BUILDER_SYSTEM,
    PROMPT_TOKEN_LIMIT,
    SKILL_API_REFERENCE,
    WORKED_EXAMPLE_METADATA,
    WORKED_EXAMPLE_SOURCE,
    estimated_tokens,
    render_builder_prompt,
)
from noob_agent.skills.contract import EvidenceRef, SkillBudget, SkillContext, SkillResult
from noob_agent.skills.package import validate_skill_package

FINISHED_AT = datetime(2026, 9, 13, 5, 0, tzinfo=UTC)


def test_the_api_reference_names_every_skill_context_method_and_result_field() -> None:
    methods = [name for name in dir(SkillContext) if not name.startswith("_")]
    assert set(methods) == {"observe", "call", "remaining_budget", "log"}
    for name in methods:
        assert f"context.{name}(" in SKILL_API_REFERENCE
    for model in (SkillResult, EvidenceRef, SkillBudget):
        for field in model.model_fields:
            assert field in SKILL_API_REFERENCE, (model.__name__, field)


def test_the_api_reference_names_the_observation_and_step_fields_a_skill_reads() -> None:
    for field in ("sequence", "status", "visible_objects", "terminal", "messages"):
        assert field in Observation.model_fields
        assert f"observation.{field}" in SKILL_API_REFERENCE
    for field in VisibleObject.model_fields:
        assert field in SKILL_API_REFERENCE
    for field in ("status", "code", "message", "observation", "primitive_actions_charged"):
        assert field in StepResult.model_fields
        assert f"result.{field}" in SKILL_API_REFERENCE


def test_the_system_prompt_carries_the_reference_and_the_worked_example() -> None:
    assert SKILL_API_REFERENCE in BUILDER_SYSTEM
    assert WORKED_EXAMPLE_SOURCE.strip() in BUILDER_SYSTEM


def test_the_worked_example_is_a_valid_package_for_a_fictional_tool() -> None:
    called = set(re.findall(r'context\.call\(\s*"([a-z_]+)"', WORKED_EXAMPLE_SOURCE))
    assert called
    declared = set(WORKED_EXAMPLE_METADATA["required_tools"])
    assert called <= declared
    for manifest in (DOOM_MANIFEST, MINECRAFT_MANIFEST):
        assert not called & {tool.name for tool in manifest.tools}, manifest.game_id

    package = validate_skill_package(
        WORKED_EXAMPLE_SOURCE,
        json.dumps(WORKED_EXAMPLE_METADATA),
        known_primitive_names=declared,
    )
    assert package.metadata.name == WORKED_EXAMPLE_METADATA["name"]


def _doom_like_episode(
    *, steps: int, objects: int, message: str = "Action completed."
) -> StoredEpisode:
    def observation(sequence: int) -> Observation:
        return Observation(
            episode_id="ep_doom",
            sequence=sequence,
            game_id="doom-vizdoom",
            scenario_id="doom-basic-training",
            public_goal="Eliminate the hostile target in this area.",
            status={"health": 100.0, "ammo": 50.0 - sequence},
            player={},
            visible_objects=tuple(
                VisibleObject(
                    object_id=f"obj_{sequence}_{index}",
                    label="Cacodemon" if index == 0 else f"Label{index}",
                    properties={"screen_offset": 38 - sequence},
                )
                for index in range(objects)
            ),
            messages=(PublicMessage(kind="observation", text=message),),
            terminal=False,
            logical_time=sequence,
        )

    records = tuple(
        StepRecord(
            episode_id="ep_doom",
            sequence=number,
            request=ToolRequest(
                action_id=f"a_{number:04d}", tool_name="attack", arguments={"ticks": 1}
            ),
            result=StepResult(
                action_id=f"a_{number:04d}",
                sequence=number,
                status="succeeded",
                code="OK",
                message=message,
                observation=observation(number),
                state_changed=True,
                primitive_actions_charged=1,
                logical_duration=1,
                wall_time_ms=5,
            ),
        )
        for number in range(1, steps + 1)
    )
    return StoredEpisode(
        episode=EpisodeRecord(
            episode_id="ep_doom",
            experiment_id="exp",
            game_id="doom-vizdoom",
            scenario_id="doom-basic-training",
            seed=1,
            split="training",
            manifest=DOOM_MANIFEST,
            reset_observation=observation(0),
            started_at=FINISHED_AT,
        ),
        steps=records,
        outcome=EpisodeOutcome(
            episode_id="ep_doom",
            stop_reason="decision_limit",
            terminal=False,
            total_decisions=steps,
            total_primitives=steps,
            finished_at=FINISHED_AT,
        ),
    )


def test_evidence_carries_the_public_observation_after_each_step_and_at_reset() -> None:
    evidence = select_evidence(_doom_like_episode(steps=3, objects=2))

    assert evidence.reset_observation.status == {"health": 100.0, "ammo": 50.0}
    first = evidence.steps[0].observation
    assert first is not None
    assert first.status["ammo"] == 49.0
    assert [(o.label, o.properties) for o in first.visible_objects] == [
        ("Cacodemon", {"screen_offset": 37}),
        ("Label1", {"screen_offset": 37}),
    ]


def test_evidence_keeps_at_most_five_objects_per_observation() -> None:
    evidence = select_evidence(_doom_like_episode(steps=1, objects=9))

    observation = evidence.steps[0].observation
    assert observation is not None
    assert len(observation.visible_objects) == 5
    assert len(evidence.reset_observation.visible_objects) == 5


def test_the_prompt_shows_primitive_definitions_and_observations() -> None:
    stored = _doom_like_episode(steps=2, objects=1)
    evidence = select_evidence(stored)

    rendered = render_builder_prompt(
        evidence,
        primitive_names=[tool.name for tool in DOOM_MANIFEST.tools],
        tools=DOOM_MANIFEST.tools,
    )

    for tool in DOOM_MANIFEST.tools:
        assert tool.description in rendered
    assert '"degrees"' in rendered and '"maximum": 90' in rendered
    assert "screen_offset" in rendered
    assert "Cacodemon" in rendered


@pytest.mark.parametrize("manifest", [DOOM_MANIFEST, MINECRAFT_MANIFEST])
def test_the_worst_case_build_prompt_fits_the_protocol_budget(manifest: ConnectorManifest) -> None:
    stored = _doom_like_episode(steps=40, objects=20, message="x" * 5_000)
    stored = StoredEpisode(
        episode=stored.episode.model_copy(update={"manifest": manifest}),
        steps=stored.steps,
        outcome=stored.outcome,
    )
    evidence = select_evidence(stored)

    rendered = render_builder_prompt(
        evidence, primitive_names=[t.name for t in manifest.tools], tools=manifest.tools
    )

    assert estimated_tokens(BUILDER_SYSTEM) + estimated_tokens(rendered) <= PROMPT_TOKEN_LIMIT
    assert PROMPT_TOKEN_LIMIT == 5_000


def test_estimated_tokens_counts_characters_divided_by_four() -> None:
    assert estimated_tokens("abcd" * 10) == 10
    assert estimated_tokens("abcde") == 2


def test_evidence_text_fields_are_bounded() -> None:
    evidence = select_evidence(_doom_like_episode(steps=1, objects=1, message="y" * 5_000))

    assert len(evidence.steps[0].message) <= 200


def test_observation_evidence_carries_no_private_fields(
    observation_factory: Callable[..., Observation],
) -> None:
    del observation_factory
    serialized = select_evidence(_doom_like_episode(steps=2, objects=2)).model_dump_json().lower()
    for private in ("faulty", "clean_variant", "grader", "predicate", "held_out", "kills"):
        assert private not in serialized


# --- The Builder agent and the sequence --------------------------------------------


class _RecordingClient:
    provider = "scripted"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.requests: list[Any] = []

    async def complete(self, request: Any) -> Any:
        from noob_agent.models.client import ModelResponse

        self.requests.append(request)
        return ModelResponse(text=self.reply, input_tokens=1, output_tokens=1, model_id="f")


async def test_the_builder_sends_its_thinking_setting_and_the_default_build_cap() -> None:
    from noob_agent.agents.builder import BuilderAgent
    from noob_agent.skills.executor import LocalSubprocessSkillExecutor
    from noob_agent.skills.registry import SkillRegistry

    stored = _doom_like_episode(steps=2, objects=1)
    client = _RecordingClient("no candidate here")
    agent = BuilderAgent(
        client, SkillRegistry(), executor=LocalSubprocessSkillExecutor(), thinking=False
    )

    await agent.build(
        select_evidence(stored),
        training_trace=stored,
        primitive_names=[tool.name for tool in DOOM_MANIFEST.tools],
        authoring_model_id="f",
        created_at=FINISHED_AT,
    )

    (request,) = client.requests
    assert request.thinking is False
    assert request.max_output_tokens == 6_000
    assert request.system == BUILDER_SYSTEM
    for tool in DOOM_MANIFEST.tools:
        assert tool.description in request.prompt


async def test_the_sequence_sends_the_builder_thinking_setting(store: Any) -> None:
    import itertools

    from fakes.connector import FakeClock, ScriptedConnector, ScriptedStep
    from test_loop_bench import CANDIDATE

    from noob_agent.models.client import ModelResponse
    from noob_agent.runtime.sequence import HeldOutCell, LearningSequence
    from noob_agent.skills.executor import LocalSubprocessSkillExecutor
    from noob_agent.skills.registry import SkillRegistry

    class Routing:
        provider = "scripted"

        def __init__(self) -> None:
            self.requests: list[Any] = []

        async def complete(self, request: Any) -> ModelResponse:
            self.requests.append(request)
            text = (
                CANDIDATE
                if request.system == BUILDER_SYSTEM
                else json.dumps(
                    {
                        "subgoal": "s",
                        "expected_evidence": "e",
                        "action": "observe",
                        "arguments": {},
                        "finding": None,
                    }
                )
            )
            return ModelResponse(text=text, input_tokens=1, output_tokens=1, model_id="f")

    ids = itertools.count(1)
    client = Routing()
    result = await LearningSequence(
        connector_factory=lambda: ScriptedConnector(
            ScriptedStep(),
            ScriptedStep(terminal=True, terminal_reason="done"),
            episode_id=f"ep_bt_{next(ids):03d}",
        ),
        client=client,
        model_id="f",
        store=store,
        registry=SkillRegistry(),
        executor=LocalSubprocessSkillExecutor(),
        grade=lambda stored, connector: None,
        clock=FakeClock(wall=FINISHED_AT),
        builder_thinking=False,
    ).run(
        sequence_id="builder-thinking",
        training_scenario_id="train",
        training_seed=1,
        heldout=(HeldOutCell("held", 2),),
    )

    builder = [request for request in client.requests if request.system == BUILDER_SYSTEM]
    assert result.builder.accepted is True
    assert builder and all(request.thinking is False for request in builder)


def test_the_reference_says_which_context_calls_are_free_and_how_to_count_actions() -> None:
    reference = " ".join(SKILL_API_REFERENCE.split())

    assert "`context.observe()` charges no primitive action" in reference
    assert "is not the `observe` primitive tool" in reference
    assert (
        "`primitive_actions_used` must equal the sum of `result.primitive_actions_charged`"
        in reference
    )
    assert "never count actions by hand" in reference
