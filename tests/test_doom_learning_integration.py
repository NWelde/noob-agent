"""The learning sequence against real ViZDoom, driven by a scripted provider.

The skill below is a hand-written test fixture returned by a fake provider. It
proves the plumbing only and must never be reported as model-generated.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fakes.connector import FakeClock

pytest.importorskip("vizdoom", reason="ViZDoom is not installed; see docs/doom-env.md.")

from noob_agent.connectors.doom import DoomConnector  # noqa: E402
from noob_agent.domain.records import StoredEpisode  # noqa: E402
from noob_agent.grading.doom import (  # noqa: E402
    DoomEpisodeGrade,
    DoomPrivateOutcome,
    grade_doom_episode,
)
from noob_agent.models.client import ModelRequest, ModelResponse  # noqa: E402
from noob_agent.prompts.builder import BUILDER_SYSTEM  # noqa: E402
from noob_agent.runtime.sequence import HeldOutCell, LearningSequence  # noqa: E402
from noob_agent.skills.executor import LocalSubprocessSkillExecutor  # noqa: E402
from noob_agent.skills.registry import SkillRegistry  # noqa: E402
from noob_agent.storage import EpisodeStore  # noqa: E402

STARTED_AT = datetime(2026, 9, 12, 22, 45, 0, tzinfo=UTC)
FIXTURE_MODEL_ID = "scripted-fixture-not-model-generated"
SKILL_NAME = "center_and_fire"

FIXTURE_SOURCE = '''"""Turn until the visible target is centered, then fire."""

import math

from noob_agent.skills.contract import EvidenceRef, SkillContext, SkillResult


async def run(context: SkillContext, inputs: dict[str, object]) -> SkillResult:
    """Center the visible target by its screen offset and fire until the episode ends."""
    observation = await context.observe()
    used = 0
    for _ in range(8):
        targets = [item for item in observation.visible_objects if item.label == "Cacodemon"]
        if not targets:
            result = await context.call("turn_left", degrees=30)
        else:
            offset = targets[0].properties.get("screen_offset")
            if not isinstance(offset, int):
                return SkillResult(status="failed", summary="No screen offset.",
                                   primitive_actions_used=used)
            if abs(offset) <= 3:
                result = await context.call("attack", ticks=4)
            else:
                degrees = max(1, min(90, round(math.degrees(math.atan(abs(offset) / 100)))))
                tool = "turn_right" if offset > 0 else "turn_left"
                result = await context.call(tool, degrees=degrees)
        used += result.primitive_actions_charged
        if result.status != "succeeded":
            return SkillResult(status="failed", summary=f"{result.code}",
                               primitive_actions_used=used)
        observation = result.observation
        if observation.terminal:
            return SkillResult(
                status="succeeded",
                summary="The episode ended after firing at the centered target.",
                evidence=(EvidenceRef(kind="action_id", value=result.action_id),),
                outputs={"terminal": True},
                primitive_actions_used=used,
            )
    return SkillResult(status="inconclusive", summary="The episode did not end.",
                       primitive_actions_used=used)
'''

FIXTURE_METADATA = {
    "name": SKILL_NAME,
    "version": 1,
    "parent_version": None,
    "purpose": "Center the visible target by its screen offset and fire until the episode ends.",
    "input_schema": {"properties": {}},
    "required_tools": ["turn_left", "turn_right", "attack"],
    "max_primitive_actions": 8,
    "max_wall_time_seconds": 30,
    "success_claim": "The episode ends after firing at the centered target.",
    "api_version": "noob-agent.skill.v1",
}

FIXTURE_REPLY = (
    f"```python\n{FIXTURE_SOURCE}```\n\n```json\n{json.dumps(FIXTURE_METADATA, indent=2)}\n```\n"
)


class ScriptedDoomProvider:
    """Returns the fixture to the Builder, observes when no skill is offered,
    and invokes the skill whenever it is offered."""

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if request.system == BUILDER_SYSTEM:
            text = FIXTURE_REPLY
        elif SKILL_NAME in request.prompt:
            text = json.dumps(
                {
                    "subgoal": "Use the learned routine.",
                    "expected_evidence": "The episode ends.",
                    "skill": SKILL_NAME,
                    "inputs": {},
                }
            )
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
            text=text, input_tokens=100, output_tokens=40, model_id=FIXTURE_MODEL_ID
        )


def grade(stored: StoredEpisode, connector: DoomConnector) -> DoomEpisodeGrade:
    outcome = DoomPrivateOutcome.from_connector(
        stored.episode.episode_id, connector.private_outcome()
    )
    return grade_doom_episode(stored, outcome)


async def test_a_doom_learning_sequence_reuses_the_accepted_skill_on_heldout_variations(
    store: EpisodeStore,
) -> None:
    sequence = LearningSequence(
        connector_factory=DoomConnector,
        client=ScriptedDoomProvider(),
        model_id=FIXTURE_MODEL_ID,
        store=store,
        registry=SkillRegistry(),
        executor=LocalSubprocessSkillExecutor(),
        grade=grade,
        clock=FakeClock(wall=STARTED_AT),
    )

    result = await sequence.run(
        sequence_id="seq_doom_fixture",
        training_scenario_id="doom-basic-training",
        training_seed=20260912,
        heldout=(
            HeldOutCell("doom-basic-heldout-a", 101),
            HeldOutCell("doom-basic-heldout-b", 201),
        ),
    )

    assert result.training.stop_reason == "decision_limit"
    assert result.training.grade.goal_completed is False
    assert result.builder.accepted is True
    assert result.accepted_version is not None
    assert result.accepted_version.authoring_model_id == FIXTURE_MODEL_ID
    assert len(result.heldout) == 2
    for episode in result.heldout:
        assert SKILL_NAME in episode.offered_skills
        assert episode.skill_uses >= 1
        assert episode.stop_reason == "terminal_state"
        assert episode.grade.goal_completed is True
        stored = store.read_episode(episode.episode_id)
        charged = sum(step.result.primitive_actions_charged for step in stored.steps)
        assert charged == episode.primitives_used > 0
