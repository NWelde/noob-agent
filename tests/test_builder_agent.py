"""The Builder turns public trace evidence into one recorded skill candidate."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from noob_agent.agents.builder import BuilderAgent
from noob_agent.agents.evidence import select_evidence
from noob_agent.domain.records import EpisodeOutcome, EpisodeRecord, ExperimentRecord, StepRecord
from noob_agent.models.client import (
    DisabledModelClient,
    ModelRequest,
    ModelResponse,
    ModelUnavailableError,
)
from noob_agent.prompts.builder import render_builder_prompt, render_repair_prompt
from noob_agent.skills import SkillRegistry, SkillValidationIssue
from noob_agent.skills.executor import LocalSubprocessSkillExecutor
from noob_agent.storage import EpisodeStore

AUTHORED_AT = datetime(2026, 9, 12, 18, 0, 0, tzinfo=UTC)
SKILL_NAME = "record_visible_state"

VALID_SOURCE = '''"""Record one fresh public observation."""

from noob_agent.skills.contract import EvidenceRef, SkillContext, SkillResult


async def run(context: SkillContext, inputs: dict[str, object]) -> SkillResult:
    """Record one fresh public observation without changing the environment."""
    if inputs:
        return SkillResult(status="failed", summary="Unexpected input.", primitive_actions_used=0)
    observation = await context.observe()
    return SkillResult(
        status="inconclusive",
        summary="Recorded the current public state.",
        evidence=(EvidenceRef(kind="observation_sequence", value=str(observation.sequence)),),
        outputs={"terminal": observation.terminal},
        primitive_actions_used=0,
    )
'''

# Forbidden import: the static policy allowlist refuses `os`.
INVALID_SOURCE = '''"""Candidate that reaches outside the allowlist."""

import os

from noob_agent.skills.contract import SkillContext, SkillResult


async def run(context: SkillContext, inputs: dict[str, object]) -> SkillResult:
    """Read the environment, which the contract forbids."""
    return SkillResult(
        status="failed",
        summary=str(os.environ),
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

PRIMITIVE_NAMES = ("observe", "use_object")


def candidate(source: str, **metadata_overrides: object) -> str:
    """One model reply in the format the Builder prompt asks for."""
    metadata = {**METADATA, **metadata_overrides}
    return (
        "Here is the skill.\n\n"
        f"```python\n{source}```\n\n"
        f"```json\n{json.dumps(metadata, indent=2)}\n```\n"
    )


class ScriptedModelClient:
    """A deterministic stand-in for a provider: no credentials, no network."""

    def __init__(self, *replies: str) -> None:
        self._replies = list(replies)
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._replies:
            raise AssertionError("The Builder asked for more replies than were scripted.")
        return ModelResponse(
            text=self._replies.pop(0),
            input_tokens=120,
            output_tokens=340,
            model_id="fake-model-a",
        )


@pytest.fixture
def stored_episode(
    store: EpisodeStore,
    experiment: ExperimentRecord,
    episode: EpisodeRecord,
    outcome: EpisodeOutcome,
    step_factory: Callable[..., StepRecord],
) -> EpisodeStore:
    store.create_experiment(experiment)
    store.create_episode(episode)
    store.append_step(step_factory(1))
    store.append_step(
        step_factory(2, tool_name="use_object", status="rejected", code="INVALID_TOOL")
    )
    store.append_step(step_factory(3))
    store.finalize_episode(outcome)
    return store


@pytest.fixture
def registry() -> SkillRegistry:
    return SkillRegistry()


async def build(
    client: ScriptedModelClient,
    registry: SkillRegistry,
    store: EpisodeStore,
    episode_id: str,
    *,
    max_repairs: int = 1,
) -> object:
    agent = BuilderAgent(
        client,
        registry,
        executor=LocalSubprocessSkillExecutor(),
        max_repairs=max_repairs,
    )
    evidence = select_evidence(store.read_episode(episode_id))
    return await agent.build(
        evidence,
        training_trace=store.read_episode(episode_id),
        primitive_names=PRIMITIVE_NAMES,
        authoring_model_id="fake-model-a",
        created_at=AUTHORED_AT,
    )


async def test_accepts_a_valid_candidate(
    stored_episode: EpisodeStore, registry: SkillRegistry, episode: EpisodeRecord
) -> None:
    client = ScriptedModelClient(candidate(VALID_SOURCE))

    outcome = await build(client, registry, stored_episode, episode.episode_id)

    assert outcome.accepted is True
    assert outcome.stop_reason == "accepted"
    assert outcome.attempts == 1
    assert outcome.version is not None
    assert outcome.version.version == 1
    assert outcome.version.status == "accepted"
    assert outcome.validation is not None
    assert outcome.validation.repeatability_runs == 3
    assert registry.accepted_skill(SKILL_NAME) == outcome.version
    assert outcome.version.authoring_episode_id == episode.episode_id


async def test_records_the_candidate_even_when_validation_rejects_it(
    stored_episode: EpisodeStore, registry: SkillRegistry, episode: EpisodeRecord
) -> None:
    """A rejected attempt stays visible, so the candidate is stored before validation."""
    client = ScriptedModelClient(candidate(INVALID_SOURCE))

    outcome = await build(client, registry, stored_episode, episode.episode_id, max_repairs=0)

    recorded = registry.get(SKILL_NAME, 1)
    assert recorded.status == "rejected"
    assert recorded.package.source == INVALID_SOURCE
    assert "os" in recorded.status_reason
    assert outcome.accepted is False
    assert registry.accepted_skill(SKILL_NAME) is None


async def test_runtime_contract_failure_is_rejected_before_acceptance(
    stored_episode: EpisodeStore, registry: SkillRegistry, episode: EpisodeRecord
) -> None:
    source = """from noob_agent.skills.contract import SkillContext, SkillResult


async def run(context: SkillContext, inputs: dict[str, object]) -> SkillResult:
    await context.attack(target="nearest")
    return SkillResult(status="failed", summary="Done.", primitive_actions_used=0)
"""
    client = ScriptedModelClient(candidate(source))

    outcome = await build(client, registry, stored_episode, episode.episode_id, max_repairs=0)

    assert outcome.accepted is False
    assert registry.get(SKILL_NAME, 1).status == "rejected"
    assert "SKILL_RAISED" in registry.get(SKILL_NAME, 1).status_reason
    assert registry.available_skills() == ()


async def test_runtime_failure_evidence_is_returned_to_the_bounded_repair(
    stored_episode: EpisodeStore, registry: SkillRegistry, episode: EpisodeRecord
) -> None:
    source = """from noob_agent.skills.contract import SkillContext, SkillResult


async def run(context: SkillContext, inputs: dict[str, object]) -> SkillResult:
    await context.attack(target="nearest")
    return SkillResult(status="failed", summary="Done.", primitive_actions_used=0)
"""
    client = ScriptedModelClient(candidate(source), candidate(VALID_SOURCE))

    outcome = await build(client, registry, stored_episode, episode.episode_id, max_repairs=1)

    assert outcome.accepted is True
    repair = client.requests[1].prompt
    assert '"fixture": "training_replay"' in repair
    assert '"public_inputs": {}' in repair
    assert "SKILL_RAISED" in repair
    assert "attack" in repair


async def test_a_repair_keeps_its_parent_and_leaves_the_rejection_intact(
    stored_episode: EpisodeStore, registry: SkillRegistry, episode: EpisodeRecord
) -> None:
    client = ScriptedModelClient(candidate(INVALID_SOURCE), candidate(VALID_SOURCE))

    outcome = await build(client, registry, stored_episode, episode.episode_id, max_repairs=1)

    rejected = registry.get(SKILL_NAME, 1)
    assert outcome.accepted is True
    assert outcome.attempts == 2
    assert outcome.version is not None
    assert outcome.version.version == 2
    assert outcome.version.parent_version == 1
    assert rejected.status == "rejected"
    assert rejected.package.source == INVALID_SOURCE


async def test_the_repair_budget_is_finite(
    stored_episode: EpisodeStore, registry: SkillRegistry, episode: EpisodeRecord
) -> None:
    client = ScriptedModelClient(candidate(INVALID_SOURCE), candidate(INVALID_SOURCE))

    outcome = await build(client, registry, stored_episode, episode.episode_id, max_repairs=1)

    assert outcome.accepted is False
    assert outcome.stop_reason == "repair_budget_exhausted"
    assert outcome.attempts == 2
    assert len(registry.versions(SKILL_NAME)) == 2
    assert registry.accepted_skill(SKILL_NAME) is None


async def test_the_repair_prompt_carries_the_public_validation_errors(
    stored_episode: EpisodeStore, registry: SkillRegistry, episode: EpisodeRecord
) -> None:
    client = ScriptedModelClient(candidate(INVALID_SOURCE), candidate(VALID_SOURCE))

    await build(client, registry, stored_episode, episode.episode_id, max_repairs=1)

    repair_prompt = client.requests[1].prompt
    assert "forbidden_import" in repair_prompt
    assert INVALID_SOURCE.strip() in repair_prompt


async def test_an_unusable_reply_stops_without_recording_a_version(
    stored_episode: EpisodeStore, registry: SkillRegistry, episode: EpisodeRecord
) -> None:
    client = ScriptedModelClient(
        "I could not find a repeated procedure worth turning into a skill."
    )

    outcome = await build(client, registry, stored_episode, episode.episode_id)

    assert outcome.accepted is False
    assert outcome.stop_reason == "unusable_reply"
    assert outcome.version is None
    assert registry.versions(SKILL_NAME) == ()


async def test_the_prompt_offers_only_the_declared_primitives(
    stored_episode: EpisodeStore, registry: SkillRegistry, episode: EpisodeRecord
) -> None:
    client = ScriptedModelClient(candidate(VALID_SOURCE))

    await build(client, registry, stored_episode, episode.episode_id)

    prompt = client.requests[0].prompt
    for name in PRIMITIVE_NAMES:
        assert name in prompt
    assert "charge_keystone" not in prompt


def test_evidence_is_bounded_and_keeps_what_went_wrong(
    stored_episode: EpisodeStore, episode: EpisodeRecord
) -> None:
    """A capped selection must not drop the rejected step, which is the useful signal."""
    evidence = select_evidence(stored_episode.read_episode(episode.episode_id), max_steps=1)

    assert len(evidence.steps) == 1
    assert evidence.steps[0].status == "rejected"
    assert evidence.steps[0].code == "INVALID_TOOL"


def test_evidence_carries_public_fields_only(
    stored_episode: EpisodeStore, episode: EpisodeRecord
) -> None:
    stored = stored_episode.read_episode(episode.episode_id)

    evidence = select_evidence(stored)
    serialized = evidence.model_dump_json()

    assert evidence.episode_id == episode.episode_id
    assert evidence.public_goal == stored.episode.reset_observation.public_goal
    assert evidence.stop_reason == "goal_completed"
    for private in ("faulty", "clean_variant", "grader", "predicate", "held_out", "held-out"):
        assert private not in serialized.lower()


async def test_the_disabled_client_never_reaches_a_provider() -> None:
    with pytest.raises(ModelUnavailableError):
        await DisabledModelClient().complete(
            ModelRequest(system="s", prompt="p", max_output_tokens=16)
        )


def test_the_repair_prompt_lists_each_failing_check() -> None:
    issue = SkillValidationIssue(
        check="static_policy", code="forbidden_import", message="`os` is not on the allowlist."
    )

    rendered = render_repair_prompt(previous_source=VALID_SOURCE, issues=(issue,))

    assert "forbidden_import" in rendered
    assert "`os` is not on the allowlist." in rendered


def test_the_repair_prompt_refuses_an_empty_issue_list() -> None:
    """A repair with nothing to fix would invite the model to guess."""
    with pytest.raises(ValueError):
        render_repair_prompt(previous_source=VALID_SOURCE, issues=())


def test_the_builder_prompt_states_the_public_goal(
    stored_episode: EpisodeStore, episode: EpisodeRecord
) -> None:
    evidence = select_evidence(stored_episode.read_episode(episode.episode_id))

    rendered = render_builder_prompt(evidence, primitive_names=PRIMITIVE_NAMES)

    assert evidence.public_goal in rendered


def test_both_builder_prompts_document_the_real_skill_contract(
    stored_episode: EpisodeStore, episode: EpisodeRecord
) -> None:
    """A live repair spent its whole output cap guessing an undocumented API."""
    from typing import get_args

    from noob_agent.skills.contract import EvidenceKind, SkillStatusClaim

    evidence = select_evidence(stored_episode.read_episode(episode.episode_id))
    issue = SkillValidationIssue(check="contract", code="SKILL_INVALID_RESULT", message="bad")

    for rendered in (
        render_builder_prompt(evidence, primitive_names=PRIMITIVE_NAMES),
        render_repair_prompt(previous_source=VALID_SOURCE, issues=(issue,)),
    ):
        assert "await context.observe()" in rendered
        assert "await context.call(" in rendered
        assert "visible_objects" in rendered and "object_id" in rendered
        assert "SkillResult(" in rendered and "status=" in rendered
        assert "summary=" in rendered and "primitive_actions_used=" in rendered
        assert "EvidenceRef(kind=" in rendered
        for kind in get_args(EvidenceKind):
            assert f'"{kind}"' in rendered
        for status in get_args(SkillStatusClaim):
            assert f'"{status}"' in rendered
