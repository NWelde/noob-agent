# Memory demo — verified live result, 2026-09-29

**The current headless demo works.** A computer-shaped workstation contains four physical repeater-memory lanes and a wide powered binary front panel. The latest clean build passed all 16 load/hold/clear checks and the visible wire-fault/repair sequence. It finishes holding `1001` (9).

This is a memory-module demo. Jev selects ready stages from a fixed circuit plan and chooses a repair; the runtime performs bounded effect-verified circuit placements. The controller drives data/HOLD inputs. Actual Minecraft redstone stores the word and powers all twelve screen lamps. Arithmetic, instruction memory, program counter, and stored-program execution remain unfinished; this is not full-computer acceptance.

## Live evidence

- Final clean build: `.noob-agent/redstone-trials/20260929T222557-40ffb45a33d8481cabab783398ab4b3d/manifest.json`.
- Started: `2026-09-29T22:25:57.553372+00:00`; ended: `2026-09-29T22:29:28.815390+00:00`; elapsed: 206.0 seconds.
- Stages selected by Jev: `['hold_bus', 'memory_bit_3', 'memory_bit_2', 'memory_bit_1', 'memory_bit_0']`.
- Event journal: 3816 events; errors: none.
- Every input 0..15: correct loaded word, retained word after complemented data inputs, locked memory repeaters, zero after clear, matching center and side lamps.
- Rehearsal: load 5, change inputs to 10 while holding 5, remove the bit-0 output wire, observe displayed 4 while stored repeaters still hold 5, Jev selects repair, restore displayed 5 without reload, clear to 0, finish holding 9.
- The demo-specific terminal result is `passed`. Frozen `final_grade.model_success=false` and generic template completion `incomplete` remain intentional: no full-computer grade is claimed.

## Attempts

| Run | Memory cases passed | Demo result |
| --- | ---: | --- |
| `20260929T221433-944f99459b7b4c89a33a5a86daffb542` | 16/16 | passed |
| `20260929T221916-c573311658f143599d0f5c6251f55b0b` | 1/2 | stopped before successful sequence |
| `20260929T222225-dab7458dc5cb4295bfd65783b1d99e90` | 16/16 | passed |
| `20260929T222557-40ffb45a33d8481cabab783398ab4b3d` | 16/16 | passed |

The first widened-screen attempt failed because branching dust stopped powering the lamp faces. Adding output repeaters fixed the real connection. The second widened-screen sweep and the final clean rebuild both passed all 16 values.

## Code checks

43 focused action/execution/memory-control tests passed. Ruff and `git diff --check` passed. The live build and behavioral sweep provide the physical proof; offline tests do not establish a working Minecraft circuit.

The wall-torch support-direction bug in the original construction queue was also fixed and covered by the regression reproducing the false dependency cycle. Live neighbor-update observations verified east-facing torches attach west, and reverse-facing unsupported torches disappear.

## Recording handoff

[Camera, replay command, controls, and narration](../docs/memory-demo.md). The current WSL address observed during validation is `172.17.247.132`; the server uses game port `25567`. The server and bot remain connected. No video capture was started.

```sh
set -a; source .env; set +a
.venv/bin/python scripts/run_memory_demo.py --replay --pause 5
```

## Artifact integrity

- `.noob-agent/redstone-trials/20260929T222557-40ffb45a33d8481cabab783398ab4b3d/manifest.json` — SHA-256 `b26bf56ecec15625c25de427fffdcbb056c7bc9a7ffe67a1b3c76a344cbc6da1`.
- `.noob-agent/redstone-trials/20260929T222557-40ffb45a33d8481cabab783398ab4b3d/events.jsonl` — SHA-256 `c84fbaeecbf09bd9cff74a3f70c26e9d0fd7add2073ec3d6ec8d529350485468`.

## Preserved visual scene

Independent final readbacks verified the black-concrete monitor frame, glass chassis, front title sign and stone support pad. The physical lamps and memory repeaters were verified by the final behavioral sweep. Scene check journal: `.noob-agent/redstone-trials/20260929T223108-7d42d47b00f447b0b4103418ffb38c09/manifest.json`.
