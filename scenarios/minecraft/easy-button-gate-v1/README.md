# Easy button-gate diagnostic room v1 (non-benchmark)

A deliberately trivial Minecraft Java 1.21.1 data pack for the
**non-benchmark** easy-mode live diagnostic
(`scripts/run_minecraft_easy_live_smoke.py`). It checks basic perception,
action selection, JSON compliance, and connector execution before the
Resonator mechanic is attempted. It is not an evaluation scenario: it has no
held-out layout, planted fault, grader, or private predicate, and results from
it must not be reported as model performance or learning transfer.

## Task

The player starts facing a wall with a 3x3 iron-bar gateway. A stone button
labelled `Gate Button` on a lime block sits beside the bars, about 3.7 blocks
away and within ordinary reach and the default 5-block observation radius.
Pressing it once shows `The iron-bar gateway opens.` and removes the bars,
revealing a gold wall. There is no container, recipe, timer, scoreboard,
inventory, or distractor.

## Install and reset

The room occupies `x=24..34`, `y=99..104`, `z=-4..4`, inside the spawn chunks
and outside the Resonator room plus the 8-block maximum observation radius.
Copy this directory into the world's `datapacks/` directory (vanilla rejects
symlinks), then run from the server console:

```text
reload
function noob_agent_easy:reset
```

`reload` also runs the Resonator pack's load function, which resets that room
and teleports online players into it. This pack deliberately has no load
function, so reloading never moves players into the easy room.

Reset removes the labels and dropped items, rebuilds the room, clears
inventories and effects, teleports every online player to `27.5 100 0.5`
facing east in Adventure mode, and gives infinite Night Vision, Saturation, and
Resistance.

## Removal

Delete the world's `datapacks/<this pack>` directory, run `reload`, then
`forceload remove 24 -4 34 4`. The built blocks stay in the world until
overwritten; they are outside every Resonator observation.
