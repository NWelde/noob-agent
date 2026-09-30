"""Live full-computer attempt with disclosed Sol design notes, no output synthesis."""

import json
import os
import time
from pathlib import Path

import noob_agent.redstone.loop as loop_module
from noob_agent.redstone.circuit_plan import CircuitPlan
from noob_agent.redstone.demo import ActionPing
from noob_agent.redstone.trial import TrialConfiguration, run_trial

os.environ["NOOB_PHYSICAL_ACTIONS"] = "1"
seed = {
    "module": "register",
    "notes": [
        (
            "ASSISTED DEMO: Sol supplies design guidance. This is not an unaided "
            "benchmark acceptance run."
        ),
        (
            "Build the frozen 4-bit accumulator/output, 3-bit PC, 8x6-bit stored- "
            "program computer. Physical player placement is enabled; no server block "
            "placement."
        ),
        (
            "Existing verified memory plan uses parallel locked-repeater lanes: data "
            "lever -> dust -> storage repeater, with a perpendicular repeater feeding "
            "its side to lock it. Build supports in earlier intentions, then "
            "functional components."
        ),
        (
            "Reference memory lanes x=20,26,32,38: data at z10, dust z11, storage "
            "repeater z12 facing north; locking repeater x-1,z12 facing west. HOLD "
            "routes to side repeaters. This is architecture guidance only, not "
            "present hardware."
        ),
        (
            "Adapt register load/hold/reset controls and independently grade actual "
            "hardware before arithmetic. Unlocked repeater follows data; locked "
            "repeater retains prior state. Reset must clear held value and output."
        ),
        (
            "Keep early build at y64 on protected grass to permit walking and "
            "ordinary reach. Reserve separate strips for arithmetic, instruction "
            "storage/control, output. Avoid unnecessarily long reset buses."
        ),
        (
            "Wall torch facing determines attachment: its support is one block "
            "opposite facing at SAME y, not below. Floor dust/repeaters need full "
            "solid support immediately below. Do not treat adjacent redstone "
            "components as supports."
        ),
        (
            "Use actual observed block states, never claim success from plan notes or "
            "expected arithmetic. Final visible program LOAD3, OUT, ADD5, OUT, HALT "
            "should physically produce outputs3 then8."
        ),
    ],
}
original = loop_module.TrialLoop


class AssistedDemoLoop(original):
    def __init__(self, manifest, actions, planner, jev, **kwargs):
        kwargs["announce"] = ActionPing(manifest, actions.transport).show
        super().__init__(manifest, actions, planner, jev, **kwargs)
        self.context.circuit_plan = CircuitPlan.model_validate(seed)
        manifest.data["assistance"] = {
            "kind": "Sol initial design notes",
            "seed": seed,
            "physical_actions": True,
            "unaided_benchmark": False,
        }
        manifest.save()
        # Real, charged walking stages from the frozen spawn toward assisted register area.
        for destination in [[38, 64, 75], [29, 64, 52], [20, 64, 29], [20, 64, 13]]:
            result = actions.read({"op": "physical", "action": "walk", "position": destination})
            event = actions.charge(
                "bounded_movement", {"method": "walking_controls", "destination": destination}
            )
            manifest.observed(event, result)
            print("APPROACH", json.dumps(result), flush=True)
        print("CAMERA_PLACEMENT_CHECKPOINT", flush=True)
        camera_gate = Path("demo/allow-computer-placement")
        wait_started = time.monotonic()
        while not camera_gate.exists():
            if time.monotonic() - wait_started > 300:
                raise RuntimeError("Camera placement handoff timed out; no placement started")
            time.sleep(0.5)


loop_module.TrialLoop = AssistedDemoLoop
Path("demo").mkdir(exist_ok=True)
Path("demo/assisted-seed-notes.json").write_text(json.dumps(seed, indent=2))
configuration = TrialConfiguration(
    mode="provider",
    task="computer",
    keep_agent_connected=True,
    planner_model="deepseek-ai/DeepSeek-V4-Pro-0813",
    planner_project="nathanweldegiorgis731-minerva-university/Noob-agent",
)
run_trial(configuration)
