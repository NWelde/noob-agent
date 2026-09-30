"""Physical memory demo, separate from full-computer trials.

Jev selects ready construction stages from a fixed circuit plan and chooses a repair.
Minecraft redstone stores the displayed word.
Only input levers are driven by the demonstration/test controller.
"""

from __future__ import annotations

import json
import time
from typing import Any

from noob_agent.redstone.actions import Actions
from noob_agent.redstone.jev import JevSubprocess, action_request, selected_action
from noob_agent.redstone.rcon import RconClient
from noob_agent.redstone.sidecar import Sidecar
from noob_agent.redstone.trial import TrialManifest

LANES = (20, 26, 32, 38)  # Most significant bit first.
DATA = [[x, 65, 10] for x in LANES]
HOLD = [18, 65, 10]
DISPLAY = [[x + 2, 69, 7] for x in LANES]
FAULT = [40, 67, 11]
LEVER = "minecraft:lever[face=floor,facing=north,powered={level}]"


def circuit_stages() -> dict[str, list[dict[str, Any]]]:
    stages: dict[str, list[dict[str, Any]]] = {}

    def offer(items: list[dict[str, Any]], pos: list[int], block: str, **props: Any) -> None:
        items.append({"position": pos, "block": f"minecraft:{block}", "properties": props})

    bus: list[dict[str, Any]] = []
    offer(bus, HOLD, "lever", face="floor", facing="north", powered=False)
    for x in range(18, 37):
        if x in (22, 32):
            # Repeater facing points toward its input, opposite signal travel.
            offer(bus, [x, 65, 16], "repeater", facing="west", delay=1)
        else:
            offer(bus, [x, 65, 16], "redstone_wire")
    for x in (18, 24, 30, 36):
        for z in range(12, 16):
            offer(bus, [x, 65, z], "redstone_wire")
    offer(bus, [18, 65, 11], "redstone_wire")
    stages["hold_bus"] = bus
    for bit, x in enumerate(LANES):
        items: list[dict[str, Any]] = []
        offer(items, [x, 65, 10], "lever", face="floor", facing="north", powered=False)
        offer(items, [x, 65, 11], "redstone_wire")
        offer(items, [x, 65, 12], "repeater", facing="north", delay=1)
        offer(items, [x - 1, 65, 12], "repeater", facing="west", delay=1)
        for dx in range(3):
            offer(items, [x + dx, 65, 13], "redstone_wire")
        for y, z in ((66, 12), (67, 11), (68, 10), (69, 9)):
            offer(items, [x + 2, y - 1, z], "stone")
            offer(items, [x + 2, y, z], "redstone_wire")
        offer(items, [x + 2, 69, 8], "repeater", facing="south", delay=1)
        offer(items, DISPLAY[bit], "redstone_lamp")
        for dx in (1, 3):
            offer(items, [x + dx, 68, 9], "stone")
            offer(items, [x + dx, 69, 9], "redstone_wire")
            offer(items, [x + dx, 69, 8], "repeater", facing="south", delay=1)
            offer(items, [x + dx, 69, 7], "redstone_lamp")
        stages[f"memory_bit_{3 - bit}"] = items
    return stages


class MemoryDemo:
    def __init__(self, manifest: TrialManifest, transport: RconClient, sidecar: Sidecar):
        self.manifest, self.transport, self.sidecar = manifest, transport, sidecar
        self.actions = Actions(manifest, transport, sidecar)

    def command(self, command: str, kind: str = "demo_setup") -> str:
        event = self.manifest.attempt(kind, {"command": command})
        result = self.transport.command(command)
        self.manifest.observed(event, {"response": result})
        return result

    def settle(self) -> None:
        self.sidecar.request({"op": "settle"})

    def announce(self, text: str) -> None:
        print(text, flush=True)
        self.command("tellraw @a " + json.dumps({"text": text, "color": "aqua"}), "demo_caption")

    def prepare(self) -> None:
        # Scene and support-pad setup precedes circuit construction.
        self.command("fill 16 64 7 44 74 24 minecraft:air")
        self.command("fill 16 64 7 44 64 24 minecraft:stone")
        self.command("fill 17 64 8 41 64 10 minecraft:black_concrete")
        self.command("fill 18 64 10 38 64 10 minecraft:stone")
        self.command("fill 18 67 8 42 72 8 minecraft:black_concrete")
        self.command("fill 19 68 8 41 70 8 minecraft:gray_concrete")
        for x in LANES:
            for dx in (1, 2, 3):
                self.command(f"setblock {x + dx} 69 8 minecraft:air")
        # Transparent chassis shows the actual circuit behind the display.
        for x in (16, 44):
            self.command(f"fill {x} 65 11 {x} 69 22 minecraft:glass")
        self.command("fill 16 65 22 44 69 22 minecraft:glass")
        self.sign([30, 71, 7], "4-BIT MEMORY", "4 STORAGE BITS", "LOAD / HOLD", "PHYSICAL REDSTONE")
        for bit, x in zip((3, 2, 1, 0), LANES, strict=True):
            self.sign([x + 2, 67, 7], f"BIT {bit}", f"WEIGHT {1 << bit}", "", "")
        self.sign([18, 65, 8], "HOLD", "ON = STORE", "OFF = LOAD", "")
        for bit, x in zip((3, 2, 1, 0), LANES, strict=True):
            self.sign([x, 65, 8], f"DATA {bit}", f"VALUE {1 << bit}", "", "")
        self.settle()

    def sign(self, pos: list[int], *lines: str) -> None:
        target = " ".join(map(str, pos))
        block = "oak_wall_sign[facing=north]" if pos[1] >= 67 else "oak_sign[rotation=8]"
        self.command(f"setblock {target} minecraft:{block}")
        messages = ",".join("'" + json.dumps({"text": line}) + "'" for line in lines)
        self.command(f"data merge block {target} {{front_text:{{messages:[{messages}]}}}}")

    def upgrade_front_panel(self) -> None:
        """Widen existing physical bit indicators for a readable recording."""
        self.sign([30, 71, 7], "4-BIT MEMORY", "4 STORAGE BITS", "LOAD / HOLD", "PHYSICAL REDSTONE")
        for bit, x in zip((3, 2, 1, 0), LANES, strict=True):
            self.sign([x + 2, 67, 7], f"BIT {bit}", f"WEIGHT {1 << bit}", "", "")
            for dx in (1, 2, 3):
                self.command(f"setblock {x + dx} 69 8 minecraft:air")
                if dx != 2:
                    self.actions.apply("place", [x + dx, 68, 9], "minecraft:stone")
                    self.actions.apply("place", [x + dx, 69, 9], "minecraft:redstone_wire")
                self.actions.apply(
                    "place", [x + dx, 69, 8], "minecraft:repeater", {"facing": "south", "delay": 1}
                )
                self.actions.apply("place", [x + dx, 69, 7], "minecraft:redstone_lamp")

    def choose(self, jev: JevSubprocess, choices: dict[str, str], state: dict[str, Any]) -> str:
        request = action_request(state, choices)
        event = self.manifest.attempt("jev_stage_call", request)
        response = jev.evaluate(request, timeout=30)
        self.manifest.observed(event, response)
        return selected_action(response, request)

    def build(self) -> None:
        remaining = circuit_stages()
        finished: list[str] = []
        jev = JevSubprocess()
        while remaining:
            ready = {
                key: value
                for key, value in remaining.items()
                if key == "hold_bus" or "hold_bus" in finished
            }
            choice = self.choose(
                jev,
                {
                    key: f"Build supplied stage {key}; verify {len(items)} bounded placements."
                    for key, items in ready.items()
                },
                {
                    "fixed_plan": True,
                    "source": "tested repeater-memory plan",
                    "completed_stages": finished,
                    "ready_plans": ready,
                },
            )
            self.announce(f"Jev selected {choice}: building and verifying physical hardware")
            for item in remaining.pop(choice):
                self.actions.apply("place", item["position"], item["block"], item["properties"])
            finished.append(choice)
            self.manifest.data["memory_stages"] = finished.copy()
            self.manifest.save()
        self.announce("Four memory lanes and front-panel lamps built. Testing real redstone next.")

    def control(self, pos: list[int], level: bool) -> None:
        if pos not in [*DATA, HOLD]:
            raise ValueError("Demo controller can drive only declared input levers")
        self.command(
            "setblock " + " ".join(map(str, pos)) + " " + LEVER.format(level=str(level).lower()),
            "demo_input_control",
        )

    def data(self, value: int) -> None:
        if type(value) is not int or not 0 <= value <= 15:
            raise ValueError("Data word must be 0..15")
        for bit, pos in zip((3, 2, 1, 0), DATA, strict=True):
            self.control(pos, bool(value & (1 << bit)))
        self.settle()

    def hold(self, enabled: bool) -> None:
        self.control(HOLD, enabled)
        # Includes propagation along two bus boosters and the side lock repeater.
        self.settle()
        self.settle()

    def read(self) -> dict[str, Any]:
        self.settle()
        lamps = [self.actions.observe(pos) for pos in DISPLAY]
        if any(lamp["name"] != "minecraft:redstone_lamp" for lamp in lamps):
            raise RuntimeError("Missing display hardware")
        bits = [int(lamp["properties"]["lit"]) for lamp in lamps]
        wings = [[self.actions.observe([x + dx, 69, 7]) for dx in (1, 3)] for x in LANES]
        if any(
            lamp["name"] != "minecraft:redstone_lamp" or int(lamp["properties"]["lit"]) != bits[bit]
            for bit, row in enumerate(wings)
            for lamp in row
        ):
            raise RuntimeError("Wide display lamps disagree with stored bit")
        memories = [self.actions.observe([x, 65, 12]) for x in LANES]
        stored = sum(
            int(cell["properties"]["powered"]) << bit
            for cell, bit in zip(memories, (3, 2, 1, 0), strict=True)
        )
        return {
            "bits": bits,
            "value": sum(v << bit for v, bit in zip(bits, (3, 2, 1, 0))),
            "lamps": lamps,
            "display_wings": wings,
            "memory_cells": memories,
            "stored_value": stored,
        }

    def verify(self, exhaustive: bool = True) -> None:
        values = range(16) if exhaustive else (0, 5, 9, 15)
        for value in values:
            self.hold(False)
            self.data(value)
            loaded = self.read()
            self.hold(True)
            self.data(value ^ 15)
            held = self.read()
            self.data(0)
            self.hold(False)
            cleared = self.read()
            result = {
                "input": value,
                "loaded": loaded,
                "held_after_input_change": held,
                "cleared": cleared,
                "passed": loaded["value"] == held["value"] == value
                and loaded["stored_value"] == held["stored_value"] == value
                and cleared["value"] == 0
                and cleared["stored_value"] == 0
                and all(c["properties"]["locked"] for c in held["memory_cells"]),
            }
            self.manifest.data.setdefault("memory_checks", []).append(result)
            self.manifest.save()
            self.announce(
                f"Memory {value:04b}: load={loaded['value']}, hold={held['value']}, "
                f"clear={cleared['value']} — {'PASS' if result['passed'] else 'FAIL'}"
            )
            if not result["passed"]:
                raise RuntimeError(f"Physical memory check failed for {value}")

    def sequence(self, pause: float = 3) -> None:
        self.announce("MEMORY DEMO: Jev construction stages and repair")
        self.hold(False)
        self.data(5)
        if self.read()["value"] != 5:
            raise RuntimeError("Load failed during recording rehearsal")
        self.announce("LOAD 0101 (5): four front-panel lamps show the input")
        time.sleep(pause)
        self.hold(True)
        self.data(10)
        held = self.read()
        if held["value"] != 5:
            raise RuntimeError("Recording rehearsal failed to retain five")
        self.announce("HOLD: inputs now 1010 (10), physical memory still displays 0101 (5)")
        time.sleep(pause)
        self.actions.apply("break", FAULT)
        broken = self.read()
        if broken["value"] != 4 or broken["stored_value"] != 5:
            raise RuntimeError("Injected wire break did not produce the expected visible fault")
        self.announce("FAULT INJECTION: bit 0 output wire removed; display now 0100 (4)")
        time.sleep(pause)
        actual = self.actions.observe(FAULT)
        jev = JevSubprocess()
        choice = self.choose(
            jev,
            {
                "repair_output_wire": "Restore missing bit-0 display wire on verified stone.",
                "inspect_memory": "Inspect locked memory first; the missing wire needs repair.",
            },
            {
                "expected_word": 5,
                "observed_display": broken,
                "fault_cell": actual,
                "fault_position": FAULT,
                "repair_plan_available": True,
            },
        )
        if choice != "repair_output_wire":
            self.announce("Jev chose to inspect memory before repair")
            self.actions.observe([38, 65, 12])
            choice = self.choose(
                jev,
                {"repair_output_wire": "Restore the confirmed missing output wire."},
                {"fault_cell": actual, "memory_still_holds": 5},
            )
        self.actions.apply("place", FAULT, "minecraft:redstone_wire")
        repaired = self.read()
        if repaired["value"] != 5:
            raise RuntimeError("Repair failed to recover stored value")
        self.manifest.data["memory_repair"] = {
            "fault_injected": True,
            "choice": choice,
            "before": broken,
            "after": repaired,
            "passed": True,
        }
        self.manifest.save()
        self.announce("Jev repair verified: display restored to 0101 (5), without reloading memory")
        time.sleep(pause)
        self.data(0)
        self.hold(False)
        if self.read()["value"] != 0:
            raise RuntimeError("Clear failed")
        self.announce("CLEAR: stored value and display return to 0000")
        time.sleep(pause)
        self.data(9)
        self.hold(True)
        final = self.read()
        if final["value"] != 9 or not all(c["properties"]["locked"] for c in final["memory_cells"]):
            raise RuntimeError("Final preserved scene did not retain nine")
        self.manifest.data["memory_final_state"] = final
        self.manifest.save()
        self.announce("READY: physical memory holds 1001 (9). Replay with --replay.")


def run_memory_demo(
    *, replay: bool = False, pause: float = 3, upgrade: bool = False, exhaustive: bool = False
) -> TrialManifest:
    manifest = TrialManifest()
    manifest.data.update(
        kind="memory_demo",
        demo_configuration={
            "circuit_plan": "fixed",
            "scenery": "setup_commands",
            "construction": "Jev selects stages; bounded placements independently read back",
            "test_controls": "trusted harness drives only data/hold levers",
            "fullcomputer_eligible": False,
            "model_designed": False,
        },
    )
    manifest.save()
    try:
        with RconClient.dedicated() as transport, Sidecar(manifest, keep_connected=True) as sidecar:
            demo = MemoryDemo(manifest, transport, sidecar)
            if not replay:
                demo.announce("Preparing 4-bit memory workstation")
                demo.prepare()
                demo.build()
                demo.verify()
            else:
                if upgrade:
                    demo.upgrade_front_panel()
                fault_cell = demo.actions.observe(FAULT)
                if fault_cell["name"] == "minecraft:air":
                    choice = demo.choose(
                        JevSubprocess(),
                        {"repair_output_wire": "Restore missing known output wire before replay."},
                        {"replay_recovery": True, "fault_cell": fault_cell, "position": FAULT},
                    )
                    if choice == "repair_output_wire":
                        demo.actions.apply("place", FAULT, "minecraft:redstone_wire")
                demo.verify(exhaustive=exhaustive)
            demo.sequence(pause)
            manifest.data["memory_demo_result"] = {
                "status": "passed",
                "memory": True,
                "repair": True,
                "full_computer": False,
            }
            manifest.save()
    except Exception as error:
        manifest.data["errors"].append(
            {"stage": "memory_demo", "type": type(error).__name__, "message": str(error)[:240]}
        )
        manifest.save()
        raise
    finally:
        manifest.finish_incomplete(["Memory demo only; not a full-computer trial or grade"])
        print(f"Evidence: {manifest.path}", flush=True)
    return manifest
