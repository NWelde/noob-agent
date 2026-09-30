"""Orchestration fixtures only; these do not establish Minecraft acceptance."""

import json

from test_machine_interface import interface
from test_redstone_behavior import compact_declared
from test_redstone_loop import Jev, World

from noob_agent.models.client import ModelResponse
from noob_agent.redstone.actions import Actions
from noob_agent.redstone.loop import TrialLoop
from noob_agent.redstone.trial import TrialManifest


def strict_declaration(module):
    value = compact_declared(module)
    if module == "register":
        ids = {"reset", "step", "i0", "i1", "i2", "i3"}
        value["controls"] = [c for c in value["controls"] if c["id"] in ids]
        value["recipe_templates"]["load"] = value["recipe_templates"]["load"][:4]
    return value


async def test_opt_in_machine_phase_requires_grade_and_preserves_failed_evidence(
    tmp_path, monkeypatch
):
    manifest = TrialManifest(tmp_path)
    world = World()
    actions = Actions(manifest, world, world)
    replies = [
        dict(summary=f"Grade {module}", actions=[], module_inspection=strict_declaration(module))
        for module in ("register", "arithmetic", "storage", "output")
    ]
    replies += [dict(summary="Grade full machine", actions=[], machine_inspection=interface())] * 2
    requests = []

    class Planner:
        async def complete(self, request):
            requests.append(request)
            return ModelResponse(
                text=json.dumps(replies[len(requests) - 1]),
                input_tokens=3,
                output_tokens=4,
                model_id="fixture/planner",
            )

    monkeypatch.setattr(
        "noob_agent.redstone.loop.inspect_module",
        lambda *_: {"valid": True, "scope": "interface_readback_only"},
    )
    monkeypatch.setattr(
        "noob_agent.redstone.loop.grade_module",
        lambda *_args, **_kw: dict(
            scope="public_module_behavior",
            complete=True,
            behavioral_passed=True,
            checks=[{"passed": True}],
        ),
    )
    monkeypatch.setattr("noob_agent.redstone.machine_world.ServerMachineWorld", lambda *_: object())
    grades = iter([dict(passed=False, failures=[{"target": "pc"}]), dict(passed=True, failures=[])])
    monkeypatch.setattr("noob_agent.redstone.machine.verify_programs", lambda *_: next(grades))
    loop = TrialLoop(
        manifest,
        actions,
        Planner(),
        Jev(manifest),
        require_module_grading=True,
        full_machine_verification=True,
        strict_module_gate=True,
    )
    await loop.run({})
    assert len(requests) == 6
    assert "machine_inspection" in requests[4].response_schema["properties"]
    grades = [e for e in manifest.data["events"] if e["kind"] == "full_machine_grade"]
    assert [e["result"]["passed"] for e in grades] == [False, True]
    assert manifest.data["loop"]["status"] == "full_machine_behavior_passed"
    assert manifest.data["final_grade"]["model_success"] is False
    rechecks = [e for e in manifest.data["events"] if e["kind"] == "module_integration_recheck"]
    assert len(rechecks) == 8


async def test_strict_gate_blocks_later_module_before_jev(tmp_path):
    manifest = TrialManifest(tmp_path)
    world = World()
    actions = Actions(manifest, world, world)
    calls = []

    class Planner:
        async def complete(self, request):
            calls.append(request)
            return ModelResponse(
                text=json.dumps(
                    dict(
                        summary="Premature arithmetic",
                        actions=[],
                        module_inspection=compact_declared("arithmetic"),
                    )
                ),
                input_tokens=3,
                output_tokens=4,
                model_id="fixture/planner",
            )

    jev = Jev(manifest)
    loop = TrialLoop(
        manifest, actions, Planner(), jev, require_module_grading=True, strict_module_gate=True
    )
    await loop.run({})
    assert manifest.data["loop"]["reason"] == "planner_validation_exhausted"
    assert not jev.requests
    assert not manifest.data["milestone_4"]["modules"]
    assert not any(e["kind"] == "module_inspection" for e in manifest.data["events"])
