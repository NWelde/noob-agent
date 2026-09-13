"""Acceptance tests for the ViZDoom connector."""

from __future__ import annotations

from noob_agent.domain.model import StepResult, ToolRequest

SCENARIO = "basic.cfg"
SEED = 20260912


async def test_manifest_declares_the_ten_doom_bdp_primitives() -> None:
    from noob_agent.connectors.doom import DoomConnector

    names = [tool.name for tool in (await DoomConnector().manifest()).tools]

    assert names == [
        "observe",
        "move_forward",
        "move_backward",
        "strafe_left",
        "strafe_right",
        "turn_left",
        "turn_right",
        "attack",
        "use",
        "wait",
    ]


async def test_reset_and_every_primitive_produce_public_schema_valid_results() -> None:
    from noob_agent.connectors.doom import DoomConnector

    connector = DoomConnector()
    try:
        reset = await connector.reset(SCENARIO, SEED)
        assert reset.sequence == 0
        assert reset.game_id == "doom-vizdoom"
        assert "health" in reset.status
        assert all(item.object_id.startswith("obj_") for item in reset.visible_objects)

        requests = [
            ToolRequest(action_id="a1", tool_name="observe"),
            ToolRequest(action_id="a2", tool_name="move_forward", arguments={"ticks": 1}),
            ToolRequest(action_id="a3", tool_name="move_backward", arguments={"ticks": 1}),
            ToolRequest(action_id="a4", tool_name="strafe_left", arguments={"ticks": 1}),
            ToolRequest(action_id="a5", tool_name="strafe_right", arguments={"ticks": 1}),
            ToolRequest(action_id="a6", tool_name="turn_left", arguments={"degrees": 15}),
            ToolRequest(action_id="a7", tool_name="turn_right", arguments={"degrees": 15}),
            ToolRequest(action_id="a8", tool_name="attack", arguments={"ticks": 1}),
            ToolRequest(action_id="a9", tool_name="use", arguments={"ticks": 1}),
            ToolRequest(action_id="a10", tool_name="wait", arguments={"ticks": 1}),
        ]
        for sequence, request in enumerate(requests, 1):
            result = await connector.step(request)
            assert StepResult.model_validate(result.model_dump()) == result
            assert result.status == "succeeded"
            assert result.sequence == sequence
            assert result.observation.last_action_id == request.action_id
    finally:
        await connector.close()


async def test_invalid_arguments_are_rejected_without_charging_a_primitive() -> None:
    from noob_agent.connectors.doom import DoomConnector

    connector = DoomConnector()
    try:
        await connector.reset(SCENARIO, SEED)
        result = await connector.step(
            ToolRequest(action_id="bad", tool_name="turn_left", arguments={"degrees": 91})
        )
    finally:
        await connector.close()
    assert result.status == "rejected"
    assert result.code == "INVALID_ARGUMENT"
    assert result.primitive_actions_charged == 0
