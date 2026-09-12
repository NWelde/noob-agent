from __future__ import annotations

import pytest
from pydantic import ValidationError

from noob_agent.domain.model import (
    ConnectorManifest,
    Observation,
    PublicMessage,
    PublicPlayerState,
    PublicPosition,
    StepResult,
    ToolDefinition,
    ToolRequest,
    VisibleObject,
)


def make_observation(*, sequence: int = 0, last_action_id: str | None = None) -> Observation:
    return Observation(
        episode_id="episode-1",
        sequence=sequence,
        game_id="fake-game",
        scenario_id="public-scenario",
        public_goal="Complete the public objective.",
        status={"health": 10, "has_key": False},
        player=PublicPlayerState(
            position=PublicPosition(x=1, y=2, z=3),
            properties={"held_item": None},
        ),
        visible_objects=[
            VisibleObject(
                object_id="object-7",
                label="unfamiliar device",
                position=PublicPosition(x=2, y=2, z=3),
                distance=1.0,
                properties={"lit": False},
            )
        ],
        messages=[PublicMessage(kind="game_feedback", text="The device hums.")],
        last_action_id=last_action_id,
        terminal=False,
        terminal_reason=None,
        logical_time=0,
    )


def test_public_connector_models_serialize_to_json_safe_data() -> None:
    manifest = ConnectorManifest(
        connector_version="fake-connector.v1",
        game_id="fake-game",
        observation_mode="structured-local-state",
        timing_model="turn-based",
        tools=(
            ToolDefinition(
                name="inspect_object",
                description="Inspect a visible object.",
                argument_schema={"object_id": {"type": "string"}},
                preconditions=["target must be visible"],
                max_duration=1,
                state_changing=False,
            ),
        ),
    )
    request = ToolRequest(
        action_id="action-1",
        tool_name="inspect_object",
        arguments={"object_id": "object-7"},
    )
    result = StepResult(
        action_id=request.action_id,
        sequence=1,
        status="succeeded",
        code="OK",
        message="Inspection completed.",
        observation=make_observation(sequence=1, last_action_id=request.action_id),
        state_changed=False,
        primitive_actions_charged=1,
        logical_duration=1,
        wall_time_ms=12,
    )

    assert manifest.model_dump(mode="json")["tools"][0]["name"] == "inspect_object"
    visible_object = result.model_dump(mode="json")["observation"]["visible_objects"][0]
    assert visible_object["object_id"] == "object-7"


def test_manifest_rejects_duplicate_primitive_tool_names() -> None:
    tool = ToolDefinition(
        name="observe",
        description="Refresh public observations.",
        argument_schema={},
        preconditions=[],
        max_duration=1,
        state_changing=False,
    )

    with pytest.raises(ValidationError, match="unique"):
        ConnectorManifest(
            connector_version="fake-connector.v1",
            game_id="fake-game",
            observation_mode="structured-local-state",
            timing_model="turn-based",
            tools=(tool, tool),
        )


@pytest.mark.parametrize("status", ["complete", "timed_out", "ok"])
def test_step_result_rejects_statuses_outside_the_connector_contract(status: str) -> None:
    with pytest.raises(ValidationError):
        StepResult(
            action_id="action-1",
            sequence=0,
            status=status,
            code="INVALID",
            message="Invalid status.",
            observation=make_observation(),
            state_changed=None,
            primitive_actions_charged=0,
            logical_duration=0,
            wall_time_ms=0,
        )


def test_models_reject_private_extra_fields_and_non_json_arguments() -> None:
    with pytest.raises(ValidationError):
        make_observation().model_validate(
            {**make_observation().model_dump(), "private_grader_state": "secret"}
        )

    with pytest.raises(ValidationError):
        ToolRequest(
            action_id="action-1",
            tool_name="observe",
            arguments={"not_json": {"a", "set"}},
        )


def test_recorded_models_are_immutable() -> None:
    observation = make_observation()

    with pytest.raises(ValidationError, match="frozen"):
        observation.sequence = 1
