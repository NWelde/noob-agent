# Full computer headless experiment — 2026-09-29

## Result

**The full-computer trial stopped incomplete. No module passed and no working computer was produced.** The final trial ran for 1008 seconds (16.8 minutes), accepted 47 intentions, and recorded 1866 events. Three consecutive planner responses were rejected for `Dependency cycle`; none of those rejected responses dispatched Jev or world actions. Task is `computer`, provider mode, with no `--stop-after-module` flag. No video work performed.

## Attempts

| Run | Events | Intentions | Checks | Loop status |
| --- | ---: | ---: | ---: | --- |
| `20260929T213903-c92546eed7174aa582b5d64a5fae9538` | 213 | 3 | 0 | {'reason': 'SidecarError', 'status': 'stopped'} |
| `20260929T214120-c496a8029e38412c94bc5bc6efa6c9ac` | 242 | 4 | 0 | {'reason': 'SidecarError', 'status': 'stopped'} |
| `20260929T214452-8f5ca1911cb945788b2af7f298df3993` | 1866 | 47 | 0 | {'reason': 'planner_validation_exhausted', 'status': 'stopped', 'validation_reason': 'Dependency cycle'} |

The earlier retained register resume stopped at `continuation_check` with `ResetError`, before planner dispatch. The configured server preflight returned all 13 probes without errors.

## Harness fixes and validation

- Floor-level dust support: permit read-only block observations one layer below the build minimum. Interactions remain bounded to the build prism. The regression failed before the fix; all 11 JavaScript sidecar tests passed afterward.
- Lever reach: journal and charge bot positioning, normal lever activation, return movement, and position verification. Budget estimates include the extra operations. All 21 action tests passed; Ruff and `git diff --check` passed. Live run independently verified multiple lever toggles and returned positions.
- Broader Python action/loop run: 72 passed, two failed on attachment validation (`Ambiguous attachment ... declare depends_on`). Action/sidecar run: 34 passed, one journal-delivery fixture failed. These broader checks are not green.

## Latest planner trace

| Intention | Agent plan |
| ---: | --- |
| 1 | Start the register module: place a lever as the reset control on a stone support, then read it back to verify placement. |
| 2 | Place reset wire from lever to register area. |
| 3 | Extend reset wire from lever toward register reset input; place second wire segment. |
| 4 | Extend reset wire east and place a redstone lamp probe to verify reset signal propagation. |
| 5 | Extend reset wire east and add a second lamp probe to verify propagation before building register storage. |
| 6 | Extend reset wire east and add a second lamp probe to verify propagation. |
| 7 | Continue reset signal route east and add a second lamp probe to verify propagation. |
| 8 | Continue reset bus east and add a second lamp probe to verify signal reaches the next segment. |
| 9 | Continue reset bus east to register area and add a second support/lever anchor for future control routing. |
| 10 | Continue reset wire east from (25,64,11) to (27,64,11) and add lamp probe at (28,64,11). |
| 11 | Continue reset bus east and add a control anchor; verify propagation with lamp probes. |
| 12 | Continue reset bus east to x=34 and add a lamp probe at x=33 to verify propagation. |
| 13 | Continue reset bus east and add a control lever support anchor; verify wire and lamp states. |
| 14 | Continue reset bus east and add a control lever support anchor at x=40. |
| 15 | Continue reset bus east and add a control lever support at x=44; verify wire and support. |
| 16 | Continue register module: place reset lever on existing stone support and verify it powers the reset bus. |
| 17 | Reset bus is not propagating from lever; inspect immediate wire and support, then rebuild a short direct reset path. |
| 18 | Diagnose reset bus: lever at (10,65,10) powers wire at (10,64,11) but downstream wire at (11,64,11) stays 0; inspect adjacent wire and lamp at (11,64,11)/(13,64,11). |
| 19 | Fix reset bus propagation by replacing the lamp at x=13 with wire and adding a repeater after x=14 to boost the signal. |
| 20 | Diagnose reset bus break: observe wire/repeater/lamp states along x=10..44 and test lever propagation. |
| 21 | Diagnose reset bus: lever powers origin wire but downstream stays 0; inspect wire connections and repeater input. |
| 22 | Diagnose reset bus break: observe wire at x=14 and repeater input/output, then test lever on. |
| 23 | Diagnose reset line break: inspect wire at x=14 and repeater input; replace wire at x=14 if needed. |
| 24 | Diagnose reset line break: observe wire at x=10 and lever support, then replace wire at x=10 if needed. |
| 25 | Diagnose reset line break: observe wires 10-14 and repeater input/output to find where signal stops. |
| 26 | Diagnose reset line break: observe wire at x=16 and repeater input/output, then test lever power propagation. |
| 27 | Diagnose reset line break: inspect wire at x=16 and repeater input/output, then repair connection. |
| 28 | Diagnose reset line break: observe wire at x=11 and repeater input side; likely missing connection between lever output and wire. |
| 29 | Diagnose reset line break: lever at (10,65,10) is floor-mounted but its support stone at (10,64,10) is not adjacent to wire at (10,64,11); add a stone block under the lever and reroute wire to receive |
| 30 | Fix reset lever support: place stone under lever at (10,64,11), then verify lever powers wire. |
| 31 | Diagnose why lever power does not reach wire at (11,64,11); inspect support and adjacent wire connectivity. |
| 32 | Restore reset route by replacing stone at (10,64,11) with redstone wire, then verify lever powers the line. |
| 33 | Build register module: 4-bit D latch with reset, load, and labeled A probes. |
| 34 | Build register bit 0 RS NOR latch: place two torches on side supports and wire reset/load inputs. |
| 35 | Build bit0 RS latch: add set/reset wire supports and connect reset lever to reset torch input. |
| 36 | Continue register bit0 RS latch: add reset input wire from reset lever to reset torch, then verify torch state. |
| 37 | Build bit0 RS latch: place reset torch on north face of reset support, set torch on south face of set support, then wire reset lever to reset torch input. |
| 38 | Build bit0 RS latch: place set torch on support, wire reset lever to reset torch input, observe states. |
| 39 | Rebuild bit0 RS latch: place set torch on north face of support at (20,64,21), then wire reset lever path to reset torch input at (20,65,19). |
| 40 | Fix bit0 set torch placement: place wall torch on stone support at (20,65,21) facing north, then verify. |
| 41 | Retry bit0 set torch with wall torch facing north on existing stone support at (20,64,21). |
| 42 | Fix bit0 set torch by placing a wall torch on the north face of the stone support at (20,64,21), then observe it. |
| 43 | Fix bit0 set torch: place redstone_wall_torch on north face of stone at (20,64,21), facing south. |
| 44 | Continue register bit0 RS NOR latch: place reset torch on south face of stone at (20,64,19), then observe it. |
| 45 | Continue register bit0 RS latch: place reset-side wire and observe both torches. |
| 46 | Complete bit0 RS NOR latch wiring and verify both side wires are powered by their torches. |
| 47 | Observe current bit0 latch state and lever positions to verify RS NOR behavior before extending register. |

## Evidence snapshot

- Authoritative manifest: `.noob-agent/redstone-trials/20260929T214452-8f5ca1911cb945788b2af7f298df3993/manifest.json` plus its adjacent `events.jsonl`, loaded through `TrialManifest.load_data`.
- Event kinds: `{'trusted_reset': 71, 'sidecar': 544, 'planner_call': 53, 'jev_call': 233, 'bounded_action': 83, 'charged_observation': 541, 'bounded_command': 71, 'action_feedback': 233, 'planner_validation': 6, 'action_offer_skipped': 7, 'bounded_movement': 24}`.
- Effect-verified mutations/interactions: 79.
- Final grade: `{'model_success': False, 'status': 'not_evaluated'}`.
- Session budgets: `{'jev_calls': 233, 'planner_calls': 53, 'repair_rounds': 9}`; primitive actions: `{'limit': 20000, 'reason': 'stopped', 'stopped': True, 'used': 719}`.
- Started: `2026-09-29T21:44:52.994280+00:00`; ended: `2026-09-29T22:02:11.275675+00:00`.
- Passed modules: `{}`.
- Completion: `{'elapsed_seconds': 1008.0216939009988, 'ended_at': '2026-09-29T22:02:11.275675+00:00', 'reasons': ['Provider trial; no final grader', 'Real-provider, full-machine and recording verification pending; no milestone acceptance', 'Persistent agent remains connected; the next trial resets the same session'], 'status': 'incomplete'}`.
- Errors: `[{'sequence': 1707, 'stage': 'bounded_action', 'type': 'EffectMismatch'}, {'sequence': 1720, 'stage': 'bounded_action', 'type': 'EffectMismatch'}, {'sequence': 1733, 'stage': 'bounded_action', 'type': 'EffectMismatch'}, {'sequence': 1746, 'stage': 'bounded_action', 'type': 'EffectMismatch'}, {'stage': 'trial_loop', 'type': 'PlannerValidationExhausted', 'validation_reason': 'Dependency cycle'}]`.

## Terminal verification

The monitored process exited with code 2. The saved session has an end timestamp, `loop.status=stopped`, and `loop.reason=planner_validation_exhausted`. This was a full-computer attempt; the register, arithmetic, storage, output, and final program checks remain unverified because construction never reached grading. The dedicated server ports and persistent bot process remain live, preserving the partial build.

- `.noob-agent/redstone-trials/20260929T214452-8f5ca1911cb945788b2af7f298df3993/manifest.json` — SHA-256 `7c9040c1485eb3b8e0f6fef2fb83b4ce8f03dbc0d9ef8482fa7983b0189f8eb3`.
- `.noob-agent/redstone-trials/20260929T214452-8f5ca1911cb945788b2af7f298df3993/events.jsonl` — SHA-256 `0a6c3d438fb81bdd266b265045bc08585bbf28da4bdc17c9fee26df8047d66b1`.
