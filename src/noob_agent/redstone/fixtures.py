"""SCRIPTED FIXTURES ONLY: missing-dust live integration, never model design.

Only explicit fixture mode imports this layout. The subprocess is a deterministic
Jev protocol stand-in; it never imports the shared provider handler or uses keys.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from noob_agent.models.client import ModelRequest, ModelResponse
from noob_agent.redstone.actions import Actions

LEVER, DUST, LAMP = [48, 64, 95], [49, 64, 95], [50, 64, 95]


def offer(name: str, action: str, position: list[int], **state: Any) -> dict[str, Any]:
    return {
        "id": name,
        "criteria": "Scripted fixture step: " + name,
        "action": action,
        "position": position,
        **state,
    }


class MissingDustPlanner:
    async def complete(self, request: ModelRequest) -> ModelResponse:
        feedback = json.loads(request.prompt)["feedback"]
        if not feedback:
            actions = [
                offer(
                    "lever",
                    "place",
                    LEVER,
                    block="minecraft:lever",
                    properties={"face": "floor", "facing": "north", "powered": False},
                ),
                offer("lamp", "place", LAMP, block="minecraft:redstone_lamp"),
            ]
        else:
            check = feedback[-1]["check"]
            phase = check["name"]
            if phase == "initial_off" and check["passed"]:
                actions = [offer("power_on", "interact", LEVER)]
            elif (
                phase == "missing_dust_powered"
                and check["passed"] is False
                and check["dust"]["name"] == "minecraft:air"
                and check["lamp"]["properties"].get("lit") is False
                and check["lever"]["properties"].get("powered") is True
            ):
                actions = [
                    offer("repair_missing_dust", "place", DUST, block="minecraft:redstone_wire")
                ]
            elif phase == "repaired_powered" and check["passed"]:
                actions = [offer("power_off", "interact", LEVER)]
            else:
                raise ValueError("Fixture observations do not authorize next step")
        return ModelResponse(
            text=json.dumps({"summary": "SCRIPTED missing-dust fixture", "actions": actions}),
            input_tokens=0,
            output_tokens=0,
            model_id="fixture/missing-dust",
            usage_reported=False,
        )


class MissingDustCheck:
    def __init__(self) -> None:
        self.phase = 0

    def __call__(self, actions: Actions) -> dict[str, Any]:
        phase = ["initial_off", "missing_dust_powered", "repaired_powered", "final_off"][self.phase]
        lever, dust, lamp = (actions.observe(position) for position in (LEVER, DUST, LAMP))
        powered = self.phase in (1, 2)
        passed = (
            lever["name"] == "minecraft:lever"
            and lever["properties"].get("powered") is powered
            and lamp["name"] == "minecraft:redstone_lamp"
            and lamp["properties"].get("lit") is powered
        )
        if self.phase >= 1:
            passed = (
                passed
                and dust["name"] == "minecraft:redstone_wire"
                and dust["properties"].get("power") == (15 if powered else 0)
            )
        else:
            passed = passed and dust["name"] == "minecraft:air"
        self.phase += 1
        return {
            "name": phase,
            "passed": passed,
            "complete": self.phase == 4 and passed,
            "lever": lever,
            "dust": dust,
            "lamp": lamp,
        }


def main() -> None:
    request = json.load(sys.stdin)
    choice = next(iter(request["questions"]["action"]["criteria"]))
    print(
        json.dumps(
            {
                "answers": {"action": {"choice": choice}},
                "usage": {"fixture": True, "totalTokens": 0},
                "response": {"modelId": "fixture/jev"},
            }
        )
    )


if __name__ == "__main__":
    main()
