"""Exercise the actual opt-in runner in an isolated Python process; no server calls."""

import subprocess
import sys


def test_production_runner_retains_primitive_charges_and_rejects_semantic_changes():
    code = r"""
import json, runpy, sys, tempfile
from pathlib import Path
from datetime import UTC, datetime, timedelta
import noob_agent.redstone.trial as trial
import noob_agent.redstone.actions as actions
from noob_agent.redstone.contract import load_contract

base = Path("scenarios/minecraft/redstone-computer-v1/contract.json")
original = json.loads(base.read_text())
expanded = json.loads(base.read_text())
expanded["budgets"].update(
    wall_seconds=43200,
    primitive_actions=200000,
    planner_calls=2400,
    jev_calls=24000,
    repair_rounds=240,
)
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    contract = root / "contract.json"
    contract.write_text(json.dumps(expanded))
    path = root / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "test",
                "loop": {"status": "stopped"},
                "events": [],
                "production_profile": {"production_limits": expanded["budgets"]},
                "contract": {"path": str(contract)},
            }
        )
    )
    trial.run_trial = lambda *args, **kwargs: None
    sys.argv = ["scripts/run_server_assisted_computer_demo.py", str(path)]
    ns = runpy.run_path(sys.argv[0], run_name="test_runner")
    assert actions.Actions is ns["CumulativeActions"]
    assert ns["load_assisted_contract"](contract).budgets.wall_seconds == 43200
    assert ns["load_assisted_contract"](base).budgets.wall_seconds == 3600
    bad = json.loads(contract.read_text())
    bad["semantics"][0] = "Software computes results"
    contract.write_text(json.dumps(bad))
    try:
        ns["load_assisted_contract"](contract)
    except ValueError:
        pass
    else:
        raise AssertionError("semantic drift admitted")
    contract.write_text(json.dumps(expanded))

    class Manifest:
        def __init__(self):
            self.data = {
                "started_at": (datetime.now(UTC) - timedelta(seconds=200)).isoformat(),
                "events": [
                    {"kind": k}
                    for k in (
                        "bounded_action",
                        "charged_observation",
                        "bounded_movement",
                        "planner_call",
                    )
                ],
                "action_budget": {"used": 0},
            }

        def attempt(self, kind, request):
            self.data["events"].append({"kind": kind, "request": request})
            return len(self.data["events"]) - 1

        def observed(self, sequence, result):
            pass

        def save(self):
            pass

    class Reader:
        timeout = 1

        def request(self, request):
            return {
                "name": "minecraft:air",
                "position": request["position"],
                "properties": {},
            }

    class Transport:
        timeout = 1

    m = Manifest()
    runtime = actions.Actions(m, Transport(), Reader(), clock=lambda: 1000)
    assert runtime.used == 3 and m.data["action_budget"]["used"] == 3
    assert 199 <= 1000 - runtime.started <= 201
    runtime.observe([10, 64, 9])
    assert runtime.used == 4
    assert runtime.maximum == 200000
    m2 = Manifest()
    m2.data["action_budget"]["used"] = 10
    runtime2 = actions.Actions(m2, Transport(), Reader(), clock=lambda: 1000)
    assert runtime2.used == 10
    runtime2.observe([10, 64, 9])
    assert runtime2.used == 11
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr + result.stdout
