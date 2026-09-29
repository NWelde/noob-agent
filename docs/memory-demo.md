# Memory demo

The headless demo is a computer-shaped workstation with **real four-bit redstone
memory and a powered binary front panel**. A tested circuit plan makes
the build repeatable. Jev selects ready construction stages and chooses a repair;
the runtime executes bounded placements and independently reads back their
effects. The setup script places the scenery and support pad.

This demonstrates construction, testing, stored state, fault detection and repair.
It is a memory module, not the completed programmable computer: the
arithmetic, instruction storage, program counter and program execution are still
unfinished. It has its own evidence and never receives the original computer
trial's acceptance grade.

## Record the existing build

Open the existing Minecraft 1.21.1 client and connect to the dedicated WSL server
on port **25567**. Use the current WSL address from `hostname -I` if connecting
from Windows. The human player already allowlisted is `nathanbeyene`.

For a front view, run this in the Minecraft chat as an operator:

```text
/gamemode creative
/tp @s 30.5 66 -12.5 0 0
```

Move closer after establishing the whole workstation. The screen faces north
(toward decreasing Z). The glass chassis behind it exposes the memory lanes.
The five keyboard levers are at Y=65, Z=10: HOLD at X=18 and data inputs at
X=20, 26, 32, 38. Data bits are 3, 2, 1, 0 from left to right when facing south.

From the project directory, replay the verified sequence:

```sh
set -a; source .env; set +a
.venv/bin/python scripts/run_memory_demo.py --replay --pause 5
```

Replay first checks representative values on the existing hardware. Start the
recording when the terminal prints `MEMORY DEMO`, or trim the checks from the
recording afterward. Keep chat visible for the phase captions, and capture the
front-panel lamps throughout the sequence.

1. **LOAD:** input and screen show `0101` (5).
2. **HOLD:** input changes to `1010` (10); physical memory and screen retain 5.
3. **FAULT:** the script deliberately removes one bit-0 output wire. Screen
   changes to `0100` (4), while memory still holds 5.
4. **REPAIR:** Jev selects the missing-wire repair. Restoring that wire recovers
   `0101` without reloading memory.
5. **CLEAR:** input zero plus released HOLD returns the screen to `0000`.
6. The scene finishes holding `1001` (9), ready for another replay.

The controller changes only the five declared input levers during load/hold/clear.
It does not write displayed bits or stored repeater states. The deliberate wire
break and selected repair are separately journaled bounded construction actions.

Suggested narration: “This is our memory-module demo. Jev selects bounded
build stages, and we verify the effects in Minecraft. These lights reflect actual
redstone memory. Changing the inputs leaves the stored word intact. We inject a
wire fault, then the agent selects a repair and restores the display.”

## Rebuild and checks

```sh
set -a; source .env; set +a
.venv/bin/python scripts/run_memory_demo.py --pause 3
```

A fresh build clears only the demo scene at X=16..44, Y=64..74, Z=7..24,
constructs the scene and circuit, tests every value 0..15, then rehearses the
sequence. Do not run it during recording unless you intend to capture construction.

For another full value sweep without rebuilding:

```sh
.venv/bin/python scripts/run_memory_demo.py --replay --exhaustive --pause 3
```

Every run saves a manifest and event journal in `.noob-agent/redstone-trials/`.
`memory_demo_result.status=passed` means this memory/repair demonstration passed;
`final_grade.model_success=false` remains correct for the unfinished full computer.
The latest live evidence is summarized in `logs/memory-demo-2026-09-29.md`.
