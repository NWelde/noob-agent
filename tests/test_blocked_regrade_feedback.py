"""Guard refusal must return repair feedback even on the first resumed grade."""

import json

from test_machine_phase import strict_declaration
from test_redstone_loop import Jev, World

from noob_agent.models.client import ModelResponse
from noob_agent.redstone.actions import Actions
from noob_agent.redstone.loop import TrialLoop
from noob_agent.redstone.trial import TrialManifest


async def test_first_grade_blocked_by_restored_guard_returns_feedback(tmp_path, monkeypatch):
    manifest = TrialManifest(tmp_path)
    world = World()
    actions = Actions(manifest, world, world)
    declaration = strict_declaration("register")

    class Planner:
        calls = 0

        async def complete(self, request):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("fixture stop after feedback")
            return ModelResponse(
                text=json.dumps(
                    {
                        "summary": "Regrade retained register",
                        "actions": [],
                        "module_inspection": declaration,
                    }
                ),
                input_tokens=3,
                output_tokens=4,
                model_id="fixture/planner",
            )

    planner = Planner()
    loop = TrialLoop(manifest, actions, planner, Jev(manifest), require_module_grading=True)
    loop.failed_module_checks["register"] = {
        "failed_checks": [{"case": "reset", "passed": False}],
        "declaration": declaration,
    }
    monkeypatch.setattr(loop.repair_guard, "blocks_regrade", lambda *args: True)
    monkeypatch.setattr(
        "noob_agent.redstone.loop.inspect_module",
        lambda *_: (_ for _ in ()).throw(AssertionError("blocked grade must not inspect")),
    )
    await loop.run({})
    assert planner.calls == 2
    assert manifest.data["loop"]["reason"] == "RuntimeError"
    event = next(e for e in manifest.data["events"] if e["kind"] == "module_regrade_blocked")
    assert event["outcome"] == "observed" and event["result"]["repair_required"]
    assert not manifest.data["milestone_4"]["modules"]


def test_module_diagnostic_improvement_allows_full_regrade_without_acceptance():
    from noob_agent.redstone.repair_guard import RepairGuard

    guard = RepairGuard()
    failure = [{"case": "1:reset", "actual": {"a": 1}, "expected": {"a": 0}}]
    declaration = {"module": "register"}
    guard.record("register", failure, "same-hardware", declaration)
    guard.record_module_diagnostic_improvement("register", verified_improvement=False)
    assert guard.blocks_regrade("register", failure, "same-hardware", declaration)
    guard.record_module_diagnostic_improvement("register", verified_improvement=True)
    assert not guard.blocks_regrade("register", failure, "same-hardware", declaration)
    assert "register" not in guard.to_state()["last"]
    guard.record("register", failure, "same-hardware", declaration)
    assert guard.blocks_regrade("register", failure, "same-hardware", declaration)
