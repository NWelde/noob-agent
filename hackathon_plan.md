# noob-agent Hackathon Plan

## Current execution direction

The user directed the controller to spend its remaining budget on the product.
The 1,600-credit remaining-budget checkpoint is recorded in the controller's
`operator_product_takeover` history; current ledger balances govern dispatch.
Further controller improvements are deferred unless an observed failure prevents
product work. Product worker calls may use the existing runner's twelve-minute
runtime option to finish a coherent implementation chunk.

The user will configure the Jev key after the integration is built. Continue
building and reviewing the planner/Jev loop, independent graders, trial command,
HUD and recorder integration using local provider fixtures and actual Minecraft
checks. Keep real-provider and full-machine demonstration evidence pending until
those runs occur. Software readiness is not a successful model-built computer.
Milestone 2's implementation exit is the live bounded-action smoke and two fully
verified resets; absent trial recording stays explicit and is completed in
milestone 6. For subsequent milestones, distinguish implementation review from
the deferred live-provider acceptance instead of blocking all coding on the key.

## Goal

Demonstrate an agent that designs, builds, tests, and repairs a programmable
4-bit redstone machine in Minecraft Java 1.21.1. The machine must have stored
instructions, at least one 4-bit register, arithmetic, and an output that judges
can read in the game. The headline result is a working machine executing a
stored program, visibly and correctly, after the agent constructed it.

The challenge is circuit design and construction. The agent must choose the
circuit and build it; the implementation must not hand the planner a finished
layout, a block-by-block blueprint, or a prebuilt computer. A fixed behavioral
specification and independent tests are allowed. Those define success without
supplying the design.

## Fixed decisions

- Use a fixed flat creative world for repeatable attempts. Start each trial from
  the same world state, inventory, agent position, and task specification.
- Start a fresh model conversation for every trial. Carry no learned circuit
  design, hidden notes, or successful plan from one trial into another.
- A planner model chooses the circuit design and issues specific, bounded build
  intentions. Jev chooses and executes actions within those intentions, then
  communicates observations, action results, and failures back to the planner.
- Use an iterative loop. After each module, the planner inspects evidence,
  tests the module, and either accepts it or revises the design and repairs it.
  Module completion is a checkpoint, not the end of a trial.
- Record every trial, including failures. A trial is one fresh attempt to build
  and test the machine from the standard reset until success, failure, or a
  declared limit. Iterations within it belong to the same trial.
- Show short, human-readable decisions and progress in an in-game HUD for the
  demo. Keep full model/tool records separately; the HUD is a summary of actual
  events, not a claim about private chain of thought.
- Use a human-like third-person client view for the recording. The agent's
  observations and the independent grader must not depend on the camera view.

## What counts as success

1. **Foundation:** The world resets reliably, the planner can issue bounded
   intentions, Jev can report what it actually did, and the recorder captures a
   complete trial. A simple redstone module can be built and tested live.
2. **Working modules:** The agent designs and constructs the required register,
   arithmetic, instruction storage/readout, and visible output. Each module has
   an independent behavioral check and can be repaired after a failed check.
3. **Headline:** In a fresh trial, the agent assembles those modules into one
   machine. The same built machine executes stored instructions and produces the
   expected visible result. A second program or changed program data must also
   produce its corresponding result without rebuilding a hard-coded display.
   The demo recording and run record show the design decisions, construction,
   tests, repairs, and final result.

The grader checks the Minecraft world and circuit behavior directly. Planner
claims, Jev success messages, the HUD, and the video are evidence for a human to
inspect, but do not by themselves establish that the machine works.

## Trial contract

Before model calls begin, freeze the public task, permitted build area, blocks,
world seed and reset procedure, action and time budgets, test programs, and
success criteria. The planner may design within those bounds. The harness must
reject out-of-bounds actions and stop at a declared limit, recording why.

Each trial receives a unique ID. Save the initial conditions, model identity and
configuration, public prompts and responses, planner intentions, Jev actions and
results, module checks, final grade, timing, errors, and recording paths under
that ID. Record partial and failed trials as carefully as successful ones.
Separate public feedback the planner may use for repair from independent final
checks. A rerun must recreate the starting conditions; it does not need to
reproduce stochastic model choices exactly.

## Implementation sequence

Work one milestone at a time. Before changing production code, inspect the
relevant interfaces, add a failing behavioral test, and define the manual live
check. Keep each milestone runnable and report what was verified in Minecraft
versus what passed only in tests. Do not mark the headline complete from a unit
test or an edited video.

0. **Bootstrap the JavaScript controller.** A scoped worker implements the
   coordinator-only workflow below. Test task dispatch, milestone budget gates,
   handoff, review gating, ledger recovery, and a refusal to launch work that
   cannot fit the remaining budget using fake agent results before any long
   Astra build run. Preserve the current uncommitted work; do not stash, reset,
   or overwrite another agent's files.
1. **Freeze the behavioral machine contract.** Specify the 4-bit datapath,
   required operations, instruction encoding and storage capacity, register
   behavior, output convention, clock/reset behavior, two test programs, and
   allowed completion time. These are observable requirements, not a circuit
   layout. Resolve the open decisions below before implementation depends on
   them.
2. **Make trials repeatable.** Start a dedicated flat creative world from a
   known state, clear the build area, verify readiness, apply bounded actions,
   and grade the simple module in the actual game. Persist a trial manifest.
3. **Connect planner and Jev.** Give the planner the frozen requirements and
   public observations. Validate bounded intentions before Jev acts, return
   Jev's actual action results, and enforce action/time limits and error
   reporting. Prove one module can be built, tested, and repaired live.
4. **Build and check modules.** Add independent checks for register state,
   arithmetic, instruction readout, and visible output. Let the planner choose
   their placement and wiring. Record failed checks and repairs.
5. **Run the full machine.** Use the same constructed hardware for at least two
   stored programs or program contents. Check each expected result directly in
   Minecraft and save the complete trace.
6. **Make the demo legible and reliable.** Add a concise HUD for decisions,
   current module, observed test result, and final grade. Rehearse reset, fresh
   conversation, live build, recovery from a failed module, recording, and
   playback. Produce a short judge-facing recording from an actual trial.

Prioritize a verified end-to-end build over extra models, large benchmarks, or
polish that does not help a judge understand whether the machine works.

## Astra development workflow

This section governs the coding agents building the demo. It is separate from
the planner and Jev operating inside a Minecraft trial. One JavaScript
controller runs the development workflow from this plan and durable state on
disk. One coordinator Astra selects and scopes work, estimates call cost,
checks evidence, and decides whether to continue, hand off, review, or advance.
The coordinator does not edit production code, tests, or configuration. Only a
worker Astra implements a section. A reviewer Astra inspects a completed section
in a fresh context and does not implement it.

The controller launches one code-writing worker at a time because the world,
trial harness, and planner/Jev contracts share state. Read-only review can run
in parallel. Workers cannot assign their own budgets, spawn more coding agents,
or mark a milestone accepted. The controller enforces the active milestone,
spending gates, and acceptance checks. Worker file assignments scope the task
and review, but workers have the host user's filesystem and shell permissions;
the controller does not enforce a worker file allowlist. Coordinator and reviewer
calls remain read-only. Record all worker changes across the milestone for the
reviewer to assess, including changes outside the assigned files. Use the supported Codex JavaScript control interface after verifying
its current usage and event fields; do not assume it exposes a precise live
context-percentage or an account-level credit counter.

For each milestone, the coordinator gives a worker the relevant slice of this
plan, acceptance criteria, scoped files, current repository state, and a call
cost estimate. The worker first inspects interfaces and writes a failing
behavioral test, then implements and verifies only that section. It returns a
structured result with changed files, tests and live checks, observed failures,
remaining work, and billed usage. The controller saves this result outside the
conversation. A fresh reviewer sees the requirement, diff, tests, and evidence,
without the implementer's private reasoning, and looks for failures or claims
that the evidence does not support. Review findings go back to a worker. The
coordinator advances only when the milestone's acceptance gate passes.

Aim to hand off a worker when its active context reaches about 50% of the
available window, preferably at a test or file boundary. The worker records a
compact handoff: exact objective, files touched, current diff or commit,
commands and results, unresolved errors, decisions, and next action. A new
Astra reads that handoff, this plan, and the relevant files, then continues the
**same** milestone with its remaining budget. Completing a milestone starts a
new worker for the next one. If the control interface cannot report active
context accurately, use conservative turn/input-size checkpoints and label the
50% threshold as an estimate. Never count cumulative billed tokens as current
context occupancy or interrupt a running file edit or game operation mid-step.

The installed Codex CLI 0.155.1 has no verified control for capping internal
model requests within one `exec` call. For milestone 0, use a fresh call for
each bounded chunk, the prompt-size and runtime checkpoints, and structured
handoffs. Record the 50% target as an estimate and disclose that internal model
requests are not capped. Acceptance requires the controller tests and a fresh
independent review of this limitation and the recovery evidence; do not claim a
hard model-call or credit ceiling.

The JavaScript controller must resume safely after interruption: store the
current milestone, worker and reviewer IDs, call estimates and actual charges,
handoffs, acceptance results, and artifact paths. It must not launch duplicate
workers for an already active section, reuse an accepted section's budget for a
new one without recording the transfer, or overwrite work left by a prior run.
Keep review and test evidence in the repository or a Git-ignored run directory,
with secrets excluded.

### Development credit budget

The total available budget is 2,000 credits. These are ceilings for the build
workflow, not target spending. The coordinator may move unused credits between
milestones when evidence shows a different bottleneck, but must log the transfer
and keep the total ceiling. The 200-credit reserve is released only for a
specific blocker or failed acceptance gate, with a written reason.

| Pool | Credits | Exit evidence |
| --- | ---: | --- |
| 0. Controller and coordinator | 200 | Dispatch, handoff, review gate, durable ledger |
| 1. Behavioral machine contract | 120 | Frozen externally testable requirements |
| 2. Repeatable trials | 250 | Live reset and simple-module check |
| 3. Planner and Jev loop | 320 | Live build, observation, and repair of one module |
| 4. Required modules | 390 | Independent live checks for all four modules |
| 5. Full machine | 250 | Two programs verified on the same built machine |
| 6. HUD and demo readiness | 140 | Legible actual recording and rehearsed recovery |
| Independent reviews | 130 | Findings resolved before each milestone advances |
| Unallocated reserve | 200 | Assigned only to a demonstrated blocker |
| **Total** | **2,000** | |

The milestone pools and the 2,000-credit global ceiling are the spending gates.
Per-agent call estimates guide dispatch and monitoring; they are not individual
caps, and exceeding one does not pause the workflow while the milestone and
global budgets still fit. A continuation draws from the same milestone pool.
The coordinator may transfer unused future or reserve credits with a written
reason when a milestone pool needs more. Check reported billed credits before
and after each agent run and before launching the next one.
If billing is delayed or the interface offers no hard spending cap, use
conservative token/turn limits and leave a safety margin rather than claiming
the 2,000-credit cap is exact. Stop dispatching work when remaining credits
cannot cover the next bounded run plus that margin. Record actual charges and
reconcile the ledger with the account's usage display.

## Recording status

A dependency-free recorder already controls Windows OBS Studio from WSL through
an authenticated local WebSocket. `scripts/record_minecraft.mjs` configures a
`Minecraft Demo` scene with a `Minecraft Window` source, starts a unique run,
then stops it and saves a manifest, original MKV, and MP4 under
`.noob-agent/recordings/<run-id>/`. A smoke test captured visible Minecraft
Java 1.21.1 footage; the recorder's focused tests pass. Keep the Minecraft
window unminimized during a trial and use F5 for the intended third-person view.
The recorder is not yet attached automatically to the trial runner, and the HUD
is not yet implemented. See the command sequence in `README.md`.

## Production readiness for this demo

- A fresh checkout has documented setup and one canonical command for a trial.
- Required services and credentials are checked before a trial starts. Secrets
  stay out of source, logs, manifests, and recordings.
- Resets, build bounds, limits, and grading are deterministic and independently
  checkable. Failures leave a usable run record and recoverable recording.
- A stopped or crashed run cannot silently contaminate the next trial's world
  or conversation. The system reports the cause and can start a fresh trial.
- Automated checks cover contracts and regressions; live checks prove the
  Minecraft and Jev integration. The demo has a practiced fallback recording
  of a real trial if live execution fails during judging.
- Documentation distinguishes implemented behavior, live evidence, and planned
  behavior. A successful OBS recording is not presented as a successful agent.

## Open decisions to settle before implementation

- Exact instruction set, encoding width, instruction storage size, and the two
  public test programs. “4-bit” currently specifies the data path, not the
  instruction width.
- Which redstone components and construction tools are allowed, the build-area
  dimensions, and whether the planner may request direct block placement or
  only player-like actions through Jev.
- The action, wall-clock, model-call, and repair limits for one trial.
- The independent way to read register, program, arithmetic, and output state
  without telling the planner the hidden grading details.
- The planner model/provider and the exact Jev interface to use. Inspect Jev's
  current documentation and the repository integration before freezing APIs.

## Milestone 1 implementation decisions (2026-09-22)

The executable v1 specification is now in `src/noob_agent/redstone/contract.py`
and `scenarios/minecraft/redstone-computer-v1/contract.json`. See
`docs/redstone-computer-contract.md` for exact semantics, usage and later live
checks. This is an implementation slice awaiting independent acceptance, not
Minecraft evidence or permission to advance milestones.

- Freeze unsigned 4-bit A and latched O, a 3-bit PC, eight 6-bit stored words,
  and LOAD/ADD/OUT/HALT. ADD wraps modulo 16. Public output traces are (3,8)
  and (14,3), both on the same hardware. No circuit layout is supplied.
- Freeze ordinary component whitelist, inclusive 96×32×96 build prism, bounded
  single-target creative actions, world template rules, public module checks,
  independent final checks and finite trial budgets in the versioned artifact.
- Runtime planner/provider and Jev selections remain configurable and explicitly
  pending inspection of the real APIs. Credentials do not block the contract.
  No integration APIs are assumed. The software oracle only derives expectations.
- User directive superseding earlier development allocations: **remaining
  product budget is 1600 credits; controller improvements are deferred except
  for a demonstrated execution blocker**. Do not interpret the historical pool
  table as a fresh allocation. This worker assigns no credits or transfers.
  The 65-credit call estimate is informational, not a hard cap. Actual billed
  usage is unavailable to this worker and must be reconciled by the caller.
- This invocation is milestone 1 only, with one code writer, no subagents,
  no model/game launches, and preservation of all pre-existing edits/deletions.

### Milestone 1 strict-loader continuation (2026-09-22)

Reproduced the preserved float regression and fixed type-sensitive artifact
comparison, with equivalent numeric drift regression coverage. Exact red/green
pytest, Ruff, mypy and whitespace results are retained in
`docs/redstone-computer-contract.md` under continuation evidence. The canonical
JSON and machine requirements are unchanged. Software checks do not constitute
live Minecraft evidence; independent acceptance and all live checks remain
pending. This continuation's caller-provided estimate is 40 credits (the earlier
65-credit estimate above describes the prior invocation). Actual billed usage
is unavailable; no credits are assigned and no milestone is advanced.
