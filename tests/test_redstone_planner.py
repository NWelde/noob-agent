"""Planner replies are untrusted; conversations belong to one trial only."""

import json
from types import SimpleNamespace

import pytest

from noob_agent.models.client import ModelResponse
from noob_agent.redstone.contract import MachineContract
from noob_agent.redstone.planner import (
    PlannerContext,
    TrialWandbClient,
    planner_validation_instruction,
    validate_intention,
)
from noob_agent.settings import ModelSettings, WandbSettings


def intention():
    return {
        "summary": "Place support",
        "actions": [
            {
                "id": "support",
                "criteria": "Support",
                "action": "place",
                "position": [1, 64, 1],
                "block": "minecraft:stone",
            }
        ],
    }


@pytest.mark.parametrize(
    "change",
    [
        {"position": [-1, 64, 1]},
        {"position": [True, 64, 1]},
        {"action": "command"},
        {"block": "minecraft:command_block"},
        {"properties": {"bad]": "x"}},
        {"id": ""},
    ],
)
def test_invalid_intention_never_becomes_an_offer(change):
    value = intention()
    value["actions"][0].update(change)
    with pytest.raises(ValueError):
        validate_intention(value, MachineContract())


def test_duplicate_ids_and_intention_limit():
    value = intention()
    value["actions"] *= 2
    with pytest.raises(ValueError):
        validate_intention(value, MachineContract())
    value["actions"] *= 200
    with pytest.raises(ValueError):
        validate_intention(value, MachineContract())


def test_out_of_bounds_feedback_identifies_the_action_and_coordinates():
    value = intention()
    value["actions"][0]["position"] = [48, 64, 98]
    with pytest.raises(
        ValueError,
        match=r"Action support at x 48 y 64 z 98 outside build bounds",
    ):
        validate_intention(value, MachineContract())
    assert "y=63 is protected ground" in PlannerContext(MachineContract()).request({}).system


async def test_context_is_fresh_and_actual_feedback_reaches_next_request():
    class Client:
        async def complete(self, request):
            self.request = request
            return ModelResponse(
                text=json.dumps(intention()),
                input_tokens=3,
                output_tokens=4,
                model_id="fixture/planner",
            )

    client = Client()
    first = PlannerContext(MachineContract())
    first.feedback({"passed": False, "actual": "air"})
    request = first.request({"player": "actual"})
    await client.complete(request)
    assert '"passed": false' in client.request.prompt
    second = PlannerContext(MachineContract())
    assert '"passed": false' not in second.request({}).prompt
    assert "independent_final_checks" not in second.request({}).prompt


def test_output_cap_repair_schema_limits_response_size():
    context = PlannerContext(MachineContract())
    ordinary = context.request({}).response_schema
    assert ordinary["properties"]["actions"]["maxItems"] == 32
    context.feedback(
        {"intention_rejected": "Response reached its output token cap before complete JSON."}
    )
    compact = context.request({}).response_schema
    assert compact["properties"]["actions"]["maxItems"] == 8
    assert compact["properties"]["summary"]["maxLength"] == 200
    assert compact["$defs"]["OfferedAction"]["properties"]["criteria"]["maxLength"] == 160
    assert ordinary["properties"]["actions"]["maxItems"] == 32


def test_provider_feedback_retains_recent_failures_without_unbounded_prompt_growth():
    context = PlannerContext(MachineContract(), require_module_grading=True)
    for index in range(9):
        context.feedback({"failed_case": index})
    feedback = json.loads(context.request({}).prompt)["feedback"]
    assert [item["failed_case"] for item in feedback] == [3, 4, 5, 6, 7, 8]
    context.grading_enabled = True
    assert "physical repair before regrading" in context.request({}).system


def test_provider_schema_allows_build_only_and_bounds_intention():
    context = PlannerContext(MachineContract(), require_module_grading=True)
    first = context.request({})
    schema = first.response_schema
    assert schema["properties"]["actions"]["maxItems"] == 8
    assert schema["properties"]["summary"]["maxLength"] == 200
    assert schema["$defs"]["OfferedAction"]["properties"]["criteria"]["maxLength"] == 160
    assert "module_inspection" not in schema["properties"]
    assert list(schema["$defs"]) == ["OfferedAction"]
    assert "quoted string id" in first.system
    assert "request_grading" in schema["properties"]
    context.feedback(
        {"intention_rejected": "Response reached its output token cap before complete JSON."}
    )
    assert "module_inspection" not in context.request({}).response_schema["properties"]
    context.grading_enabled = True
    later = context.request({}).response_schema
    assert later["properties"]["actions"]["maxItems"] == 8
    assert "module_inspection" in later["properties"]


def test_forced_grade_switches_to_declaration_schema_and_prompt():
    context = PlannerContext(MachineContract(), require_module_grading=True)
    context.force_grading = True

    request = context.request({})

    assert "module_inspection" in request.response_schema["properties"]
    assert "construction limit was reached" in request.system.lower()

    schema = request.response_schema
    assert schema["properties"]["actions"]["maxItems"] == 0
    assert "module_inspection" in schema["required"]
    assert schema["properties"]["module_inspection"] == {
        "$ref": "#/$defs/ModuleDeclaration"
    }
    assert "empty actions" in request.system.lower()
    assert request.response_schema["properties"]["actions"]["minItems"] == 0
    assert request.response_schema["properties"]["request_grading"] == {
        "type": "boolean",
        "enum": [False],
    }
    assert "first return exactly one json object" in request.system.lower()


def test_forced_grade_keeps_zero_actions_even_after_output_cap_feedback():
    context = PlannerContext(MachineContract(), require_module_grading=True)
    context.force_grading = True
    context.feedback(
        {"intention_rejected": "Response reached its output token cap before complete JSON."}
    )

    request = context.request({})

    assert request.response_schema["properties"]["actions"]["maxItems"] == 0
    assert request.response_schema["properties"]["module_inspection"] == {
        "$ref": "#/$defs/ModuleDeclaration"
    }


def test_lamp_repair_planner_gets_small_task_schema_and_seeded_fixture():
    request = PlannerContext(MachineContract(), task="lamp_repair").request({})

    assert "module_inspection" not in request.response_schema["properties"]
    assert "repair the seeded" in request.system.lower()
    assert request.response_schema["properties"]["actions"]["maxItems"] == 1
    assert "minecraft:redstone_wire" in request.system
    assert json.loads(request.prompt)["fixture"]["agent_created"] is False
    assert "open_connection" in json.loads(request.prompt)["fixture"]


def test_grading_validation_feedback_repairs_missing_roles_and_aliased_positions():
    missing_roles = planner_validation_instruction(
        "Exactly one reset and STEP are required", "stop"
    )
    assert "exactly one" in missing_roles.lower()
    assert "role `reset`" in missing_roles
    assert "role `step`" in missing_roles

    aliased_positions = planner_validation_instruction(
        "Aliased control step and control load at x 14 y 65 z 10", "stop"
    )
    assert "distinct" in aliased_positions.lower()
    assert "position" in aliased_positions.lower()
    assert "`control step`" in aliased_positions
    assert "`control load`" in aliased_positions
    assert "[14, 65, 10]" in aliased_positions
    assert "keep both" in aliased_positions.lower()
    assert "do not" in aliased_positions.lower()


def test_grading_validation_feedback_preserves_output_cap_repair():
    instruction = planner_validation_instruction("schema error", "length")
    assert "compact JSON" in instruction


def test_validation_feedback_removes_block_state_from_nonplacement_actions():
    instruction = planner_validation_instruction("Unexpected block state", "stop")
    assert "break" in instruction
    assert "interact" in instruction
    assert "observe" in instruction
    assert "only a `place`" in instruction.lower()


def test_validation_feedback_uses_namespaced_block_ids():
    instruction = planner_validation_instruction("Block is not permitted", "stop")
    assert "minecraft:redstone_wire" in instruction
    assert "namespaced" in instruction


def test_wandb_sdk_disables_retries_and_sets_timeout(monkeypatch):
    captured = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(
        "noob_agent.models.client.importlib.import_module",
        lambda _: SimpleNamespace(AsyncOpenAI=factory),
    )
    client = TrialWandbClient(
        ModelSettings(provider="wandb-inference", inference_model="test"),
        WandbSettings(api_key="secret-fixture"),
        timeout=3,
    )
    client._client()
    assert captured["max_retries"] == 0
    assert captured["timeout"] == 3


@pytest.mark.parametrize("module", ["register", "arithmetic", "storage", "output"])
def test_complete_parameterized_declarations_are_planner_intentions(module):
    from test_redstone_behavior import compact_declared

    reply = {
        "summary": f"Check complete {module} interface",
        "actions": [],
        "module_inspection": compact_declared(module),
    }
    validated = validate_intention(json.loads(json.dumps(reply)), MachineContract())
    assert validated.module_inspection.recipe_templates
    request = PlannerContext(MachineContract()).request({})
    assert request.max_output_tokens == 16_384
    assert "recipe_templates" in request.system
    assert "ParameterBit" in request.response_schema["$defs"]
