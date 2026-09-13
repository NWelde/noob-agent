# Resonator training scenario v1

This directory is the source of the Minecraft Java 1.21.1 training-world data
pack. It implements only the fixed training room from `minecraft_scenario.md`.
It contains no held-out layout, planted fault, connector, or private grader.

## Clean rule

Drop two `Dull Shard` items onto the Resonator basin, press the nearby
activation control, and wait 40 game ticks. The device consumes exactly two
shards and places exactly one `Charged Key` in the output barrel. Take that key
and press the sealed gateway's control; the key is consumed and the gateway
opens.

Wrong objects remain on the basin and display `The device does not respond.`
Activating after supplying fewer than two shards displays
`The device hums, but nothing changes.`

## Install and reset

Copy this directory into the dedicated world's `datapacks/` directory (vanilla
rejects data-pack symlinks by default), then run these commands from the server
console:

```text
reload
function noob_agent:reset
```

The room occupies `x=-7..7`, `y=99..105`, `z=-7..7`. Reset cancels scenario
timers, rebuilds the room and all objects, removes scenario entities and dropped
items, restores counters and messages, and resets every online player's
position, inventory, game mode, spawn point, and protective effects.

## Executable acceptance checks

From the server console, with or without an online player, run:

```text
function noob_agent:oracle/check_feedback
function noob_agent:oracle/start
```

The first command checks both required negative-input outcomes without consuming
the rejected or insufficient input. The second executes the successful sequence
twice with a full reset between runs and records success only when both final
states match and contain no assertion failures. Query the machine-readable
results with:

```text
scoreboard players get #feedback_status noob_agent.test
scoreboard players get #oracle_status noob_agent.test
```

Each value is `1` on success and `-1` on failure.
