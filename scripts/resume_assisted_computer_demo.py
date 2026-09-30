"""Bounded physical recovery after independent unchanged-target/world readback."""

import json
import os
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import noob_agent.redstone.loop as loop_module
import noob_agent.redstone.trial as trial_module
from noob_agent.redstone.circuit_plan import CircuitPlan
from noob_agent.redstone.demo import ActionPing
from noob_agent.redstone.sidecar import Sidecar
from noob_agent.redstone.trial import TrialConfiguration, TrialManifest

os.environ["NOOB_PHYSICAL_ACTIONS"] = "1"
source = Path(sys.argv[1])
saved = TrialManifest.load_data(source)
assert saved["loop"] == {"reason": "SidecarError", "status": "stopped"}
assert saved.get("assistance", {}).get("physical_actions") is True
attempt = len(saved.get("physical_recoveries", [])) + 1
assert attempt <= 2, "Physical recovery cap reached"
unknown = [event for event in saved["events"] if event["outcome"] == "unknown"]
failed = [event for event in unknown if event["kind"] == "bounded_action"]
assert len(failed) == 1 and failed[0]["request"]["action"] == "place"
failed_position = failed[0]["request"]["position"]
assert all(event["request"].get("position") == failed_position for event in unknown)
assert all(
    event["kind"] in {"bounded_action", "sidecar", "charged_observation"} for event in unknown
)
expected = {}
for event in saved["events"]:
    if event["kind"] == "bounded_action" and (event.get("result") or {}).get("effect_verified"):
        expected[tuple(event["request"]["position"])] = event["result"]["after"]
before = next(
    event["result"]
    for event in reversed(saved["events"])
    if event["kind"] == "charged_observation"
    and event["outcome"] == "observed"
    and event["request"].get("position") == failed_position
)
expected[tuple(failed_position)] = before
proof = TrialManifest(Path("demo/physical-continuation-proof"))
transient = {"powered", "lit", "power", "signal_strength", "north", "south", "east", "west"}
with Sidecar(proof, keep_connected=True) as reader:
    for position, wanted in expected.items():
        actual = reader.request({"op": "block", "position": list(position)})
        assert actual["name"] == wanted["name"], (position, actual)
        assert all(
            actual["properties"].get(key) == value
            for key, value in wanted["properties"].items()
            if key not in transient
        )
    player = reader.request({"op": "player"})
    assert player["gameMode"] == "creative"
proof.data["recovery_proof"] = {
    "passed": True,
    "source": str(source),
    "failed_target_unchanged": failed_position,
    "checked_cells": len(expected),
    "player": player,
}
proof.save()
backup = source.with_name(f"before-physical-recovery-{attempt}.json")
shutil.copy2(source, backup)
saved.setdefault("physical_recoveries", []).append(
    {"proof": str(proof.path), "attempt": attempt, "checked_cells": len(expected)}
)
temporary = source.with_suffix(".recovery.tmp")
temporary.write_text(json.dumps(saved, indent=2))
temporary.replace(source)
original_eligibility = trial_module.can_resume_model_failure
trial_module.can_resume_model_failure = lambda data: (
    (
        data.get("run_id") == saved["run_id"]
        and data.get("physical_recoveries") == saved["physical_recoveries"]
    )
    or original_eligibility(data)
)
original_loop = loop_module.TrialLoop
prior_used = dict(saved["loop_budget"])
prior_actions = saved["action_budget"]["used"]
elapsed = (datetime.now(UTC) - datetime.fromisoformat(saved["started_at"])).total_seconds()


class RecoveredDemoLoop(original_loop):
    def __init__(self, manifest, actions, planner, jev, **kwargs):
        kwargs["announce"] = ActionPing(manifest, actions.transport).show
        super().__init__(manifest, actions, planner, jev, **kwargs)
        self.used = dict(prior_used)
        actions.used += prior_actions + len(expected) + 1
        actions.started -= elapsed
        self.deadline = min(
            self.deadline, time.monotonic() + self.contract.budgets.wall_seconds - elapsed
        )
        plan = self.context.circuit_plan.model_dump() if self.context.circuit_plan else {}
        plan.setdefault("notes", []).append(
            "ASSISTED RECOVERY: all retained hardware and failed target independently read back. "
            "Register circuitry is not verified. Torches alone are not storage. Complete genuine "
            "load/hold/reset latch wiring and request public register grading before arithmetic."
        )
        self.context.circuit_plan = CircuitPlan.model_validate(plan)
        self.context.grading_enabled = True
        manifest.data["budget_preservation"] = {
            "prior_calls": prior_used,
            "prior_actions": prior_actions,
            "recovery_reads": len(expected) + 1,
            "elapsed_wall_seconds": elapsed,
        }
        manifest.save()


loop_module.TrialLoop = RecoveredDemoLoop
configuration = TrialConfiguration(
    mode="provider",
    task="computer",
    keep_agent_connected=True,
    planner_model="deepseek-ai/DeepSeek-V4-Pro-0813",
    planner_project="nathanweldegiorgis731-minerva-university/Noob-agent",
)
print("RECOVERY_VERIFIED", proof.path, "cells", len(expected), flush=True)
trial_module.run_trial(configuration, resume=source)
