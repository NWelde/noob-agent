"""Executable candidate validation before a skill can be accepted."""

from __future__ import annotations

import json

import pytest

from noob_agent.domain.records import StoredEpisode
from noob_agent.skills import SkillValidationError
from noob_agent.skills.executor import LocalSubprocessSkillExecutor
from noob_agent.verification.validator import validate_candidate

METADATA = {
    "name": "inspect_target",
    "version": 1,
    "parent_version": None,
    "purpose": "Inspect a visible target through the public primitive API.",
    "input_schema": {"properties": {}, "additionalProperties": False},
    "required_tools": ["observe"],
    "max_primitive_actions": 2,
    "max_wall_time_seconds": 5,
    "success_claim": "The target is visible in a fresh observation.",
    "api_version": "noob-agent.skill.v1",
}

VALID_SOURCE = '''from noob_agent.skills.contract import EvidenceRef, SkillContext, SkillResult


async def run(context: SkillContext, inputs: dict[str, object]) -> SkillResult:
    if inputs:
        return SkillResult(status="failed", summary="Unexpected input.", primitive_actions_used=0)
    observation = await context.observe()
    if not observation.visible_objects:
        return SkillResult(status="failed", summary="Target missing.", primitive_actions_used=0)
    return SkillResult(
        status="succeeded",
        summary="Target is visible.",
        evidence=(EvidenceRef(kind="object_id", value=observation.visible_objects[0].object_id),),
        outputs={"target": observation.visible_objects[0].object_id},
        primitive_actions_used=0,
    )
'''

NONEXISTENT_API_SOURCE = '''from noob_agent.skills.contract import SkillContext, SkillResult


async def run(context: SkillContext, inputs: dict[str, object]) -> SkillResult:
    await context.attack(target="nearest")
    return SkillResult(status="failed", summary="Done.", primitive_actions_used=0)
'''


async def test_runs_the_full_public_pipeline_three_times_with_an_unseen_variation(
    episode, step_factory
) -> None:
    trace = StoredEpisode(episode=episode, steps=(step_factory(1),))

    report = await validate_candidate(
        VALID_SOURCE,
        json.dumps(METADATA),
        known_primitive_names={"observe", "use_object"},
        training_trace=trace,
        executor=LocalSubprocessSkillExecutor(),
    )

    assert report.accepted is True
    assert report.repeatability_runs == 3
    assert report.checks == (
        "package",
        "static_policy",
        "load",
        "contract",
        "training_replay",
        "negative_cases",
        "repeatability",
        "validation_variation",
    )
    assert report.variation_object_ids == ("validation_obj_1",)


async def test_rejects_a_candidate_that_calls_a_context_api_that_does_not_exist(
    episode, step_factory
) -> None:
    trace = StoredEpisode(episode=episode, steps=(step_factory(1),))

    with pytest.raises(SkillValidationError) as rejected:
        await validate_candidate(
            NONEXISTENT_API_SOURCE,
            json.dumps(METADATA),
            known_primitive_names={"observe", "use_object"},
            training_trace=trace,
            executor=LocalSubprocessSkillExecutor(),
        )

    issue = rejected.value.issues[0]
    assert issue.check == "contract"
    assert issue.code == "SKILL_RAISED"
    assert "attack" in issue.message
    assert issue.public_inputs == {}
    assert issue.public_result is not None
    assert "held-out" not in rejected.value.__str__().lower()


async def test_negative_cases_reject_an_unknown_retry(episode, step_factory) -> None:
    source = '''from noob_agent.skills.contract import SkillContext, SkillResult


async def run(context: SkillContext, inputs: dict[str, object]) -> SkillResult:
    first = await context.call("observe")
    used = 1
    if first.status == "failed":
        return SkillResult(status="failed", summary="Observe failed.", primitive_actions_used=used)
    if first.status == "unknown":
        await context.call("observe")
        used = 2
    return SkillResult(status="inconclusive", summary="Observed.", primitive_actions_used=used)
'''
    metadata = {**METADATA, "max_primitive_actions": 3}
    trace = StoredEpisode(episode=episode, steps=(step_factory(1), step_factory(2)))

    with pytest.raises(SkillValidationError) as rejected:
        await validate_candidate(
            source,
            json.dumps(metadata),
            known_primitive_names={"observe", "use_object"},
            training_trace=trace,
            executor=LocalSubprocessSkillExecutor(),
        )

    assert any(
        issue.check == "negative_case" and issue.code == "unknown_primitive_retried"
        for issue in rejected.value.issues
    )
