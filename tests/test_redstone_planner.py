"""Planner replies are untrusted; conversations belong to one trial only."""

import json
from types import SimpleNamespace

import pytest

from noob_agent.models.client import ModelResponse
from noob_agent.redstone.contract import MachineContract
from noob_agent.redstone.planner import PlannerContext, TrialWandbClient, validate_intention
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
    assert request.max_output_tokens == 4096
    assert "recipe_templates" in request.system
    assert "ParameterBit" in request.response_schema["$defs"]
