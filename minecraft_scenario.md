# Minecraft Scenario Specification

## Scenario goal

The BDP scenario is a short unfamiliar-machine task in a prepared Minecraft
world. The player begins near a sealed gateway with normal survival needs
removed. Everything required is nearby. The player must discover how to create
the object that opens the gateway, then use it successfully.

The mechanic is implemented with a data pack or command blocks for the first
build. It does not require a custom mod or a full game playthrough.

Working name: **The Resonator**. Names and visuals may change, but the behavior
and evaluation split below must remain fixed once the campaign starts.

## Public instructions

Every condition receives exactly this task text:

> Open the sealed gateway using the objects and devices in this area. You may
> inspect, collect, place, and use what you find. The purpose of unfamiliar
> objects is not documented, so use visible results as evidence. If behavior
> contradicts evidence you established earlier, report expected and observed
> behavior separately. Finish within the action budget.

The prompt does not mention a resonator, recipe, clean/faulty versions, planted
defect, quantities, or grader conditions.

## Public world elements

The area contains:

- A sealed gateway with a visible inactive state.
- One unfamiliar processing device, publicly named `Resonator`.
- At least two collectible unfamiliar inputs, publicly named `Dull Shard`.
- One nearby activation control.
- Containers or platforms that may hold inputs and harmless distractors.
- Public feedback messages for accepted input, rejected input, process start,
  process completion, and gateway activation.

The unfamiliar names identify objects but do not explain their purpose. The
agent sees only nearby public state through the connector. It never sees command
blocks, data-pack state, tags used by the grader, or scenario source.

## Underlying rule

In the clean scenario:

1. Two Dull Shards must be supplied to the Resonator.
2. The activation control must then be used.
3. After 40 game ticks, the device consumes exactly two shards and produces
   exactly one `Charged Key`.
4. Using one Charged Key on the sealed gateway consumes the key and changes the
   gateway from inactive to open.

Wrong objects are not consumed and produce the public message `The device does
not respond.` Activating with fewer than two shards produces `The device hums,
but nothing changes.` These messages help experimentation without revealing the
complete solution.

## Training world

The training world teaches the mechanic through interaction, not instructions.
It has one fixed compact room, enough shards for three experiments, one
Resonator, one gateway, and two distractor objects. The initial locations and
seed are stored in the training manifest.

The agent may make one bounded training attempt. Its trace may be given to the
Builder. Task completion during training is useful evidence but does not count
as held-out success.

The Builder's validation variation changes object IDs, swaps the two container
positions, and starts the player from the opposite side of the room. It preserves
the same underlying rule and is never shown in the Builder prompt.

## Held-out variations

Held-out scenarios change irrelevant details while preserving the learned rule:

| Variation | Changes | What remains constant |
| --- | --- | --- |
| `layout_a` | Player start, device rotation, container positions | Inputs, timing, recipe, gateway behavior |
| `layout_b` | Shards split across locations; extra distractor | Inputs, timing, recipe, gateway behavior |

Each variation has three precommitted seeds. Seed values and build hashes are
sealed before model runs and published with the final results. Models never see
another condition's trace.

Every layout has a matched clean and faulty build with identical public task,
starting inventory, visible layout, object labels, and budgets. The scenario ID
presented to the model identifies the layout but not whether it is clean or
faulty.

## Clean and faulty behavior

The clean build follows the rule above.

The faulty build contains exactly one planted behavior: after two shards are
supplied and the device is activated, it consumes two shards but produces **two**
Charged Keys instead of one. Timing, messages, gateway behavior, and all other
rules remain identical.

The extra output remains publicly visible and collectible. The fault does not
prevent goal completion. This lets the evaluation score task performance and
defect detection separately.

A valid defect report must cite public evidence for the expected one output and
the observed two outputs. Merely saying that the scenario may contain a bug is a
false alarm. The training trace can establish the expected behavior; the private
clean/faulty label cannot.

## Private grader predicates

The grader reads scenario-controlled state that is never placed in prompts or
public observations.

### Progress predicates

- `found_device`: the player inspected the Resonator.
- `supplied_first_input`: at least one valid shard was accepted.
- `started_process`: activation occurred with at least two valid shards.
- `created_key`: at least one Charged Key was produced.
- `goal_completed`: the correct gateway changed to open because the player used
  a Charged Key.

Progress score is the number of true predicates in that order, from 0 to 5. It
is computed after the episode and is never fed back to the model.

### Behavior predicates

- `inputs_consumed == 2` for one successful processing event.
- `outputs_created == 1` in clean builds.
- `outputs_created == 2` in faulty builds.
- `unrelated_state_unchanged` for distractors, other inventory, and gateway
  before key use.

### Finding predicates

A defect is verified only when the report includes a relevant expected count,
actual count, and evidence references; the faulty build predicate is true; and
the matched clean build does not show the behavior under the same action
sequence.

Fresh reproduction resets the same faulty layout with a different precommitted
seed, executes the minimal evidence sequence through ordinary primitives, and
requires two outputs again. The system, not the reporting model, performs the
final private check.

## Reset and isolation

Reset restores player position and inventory, all object locations and counts,
device state, gateway state, messages, timers, and private counters. It also
assigns new episode-local object IDs. A reset smoke test runs the scripted clean
sequence twice and compares grader-relevant state hashes.

Training, validation, held-out, and reproduction use separate world instances
or complete snapshots. Held-out files and private predicates are not mounted in
the Builder sandbox.

## Scenario acceptance checklist

Before model evaluation, a scripted oracle must demonstrate that:

- Every public object required by the task is reachable with declared tools.
- The clean sequence consumes two inputs, produces one output, and opens the
  gateway.
- Wrong input and insufficient input produce the specified public feedback.
- Both held-out layouts are solvable within 12 decisions and 24 primitives when
  the procedure is known.
- The faulty sequence differs only in output count.
- A clean run never satisfies the fault predicate.
- Reset produces the same logical starting state for a fixed seed.
- No observation, tool description, error, or object ID leaks the solution or
  clean/faulty identity.
