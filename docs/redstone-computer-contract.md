# Redstone computer v1 behavioral contract

Milestone 1 implements an immutable Pydantic contract, strict JSON loader,
instruction encoder/decoder, bounded construction target validator, and a
software reference interpreter. No circuit, block layout, game integration,
trial runner, or Minecraft success is supplied. The canonical artifact is
`scenarios/minecraft/redstone-computer-v1/contract.json`; its complete content
must match `MachineContract()` or loading fails. Any requirement change needs a
new contract version and corresponding implementation, not an edited v1 JSON.
All nested collections are tuples and all nested models are frozen.
The loader compares normalized JSON with the executable defaults: key order and
whitespace may differ, but integer fields cannot be replaced by floats or
booleans, even when Python considers their values equal.

From the repository root:

```python
from noob_agent.redstone import load_contract, reference_execute
contract = load_contract('scenarios/minecraft/redstone-computer-v1/contract.json')
trace = reference_execute(contract.programs[1].words)
assert trace.outputs == (14, 3)
assert trace.evidence_kind == 'software_reference_only'
```

The oracle computes expected states only. Its result has no Minecraft success
field and is never evidence that a circuit exists or works.

## Machine interface

There is one unsigned four-bit accumulator A and a separate four-bit latched
output O, both initially zero. PC is a three-bit instruction address initially
zero. The machine stores eight six-bit words. Bits 5..4 are the opcode and bits
3..0 are the operand, in ordinary most-significant-bit-first binary notation.

| Opcode | Encoding | Behavior |
| --- | --- | --- |
| LOAD n | `00nnnn` | Replace A with n; preserve O |
| ADD n | `01nnnn` | A becomes (A+n) modulo 16; preserve O |
| OUT | `100000` | Copy A to O and toggle output-event strobe |
| HALT | `110000` | Latch halted, hold PC and all data |

Nonzero OUT/HALT operands are invalid. No carry, branch, subtraction or PC wrap
is required. Every valid program halts within eight retirements; words after
its first HALT must also be HALT. Short program descriptions are padded to eight
words with decimal 48 when loaded into hardware. The oracle accepts the short
form or padded form, validates all supplied words, and stops on the first HALT.

The external STEP control pulses high for two game ticks and low for 198. A
rising edge retires exactly one instruction; the trusted reader samples after
200 ticks before issuing another edge. PC advances after every non-HALT
instruction. Further STEP pulses after HALT change nothing. Hold reset with
STEP low for 200 ticks; release it and wait another 200 ticks before executing.
Reset clears A, O, PC, halted and strobe, but preserves stored instructions.
Loading is allowed only while reset is held. State holds between steps after
settling. Program execution is limited to eight steps, 1600 game ticks and 120
wall seconds, starting at the first STEP; reset/loading remain within the total
trial budget. Two post-halt probe steps are outside the program execution limit
but count against the trial budget.

O appears on four labeled redstone lamps b3..b0, with weights 8,4,2,1 and lit=1.
The planner declares probe locations for A, PC, halted, output strobe and every
stored bit, plus reset, STEP and programming controls. This declares observable
interfaces, not their placement or implementation. Module test access may use
declared controls to set inputs; the final program run must use stored words
and STEP, with no external computation driving A or O.

| Program | Words (decimal, before padding) | Output events | Final A/O |
| --- | --- | --- | --- |
| ordinary_addition | 3,32,21,32,48 | 3,8 | 8/8 |
| modular_addition | 14,32,21,32,48 | 14,3 | 3/3 |

Each program retires five instructions. The second demonstrates 14+5=3 modulo
16. Intermediate outputs and retirement states matter, not just the final lamp
pattern. Identical hardware must run both; only declared program controls may
change between them. Compare hardware block identities and configuration while
excluding program controls and transient power/lit state; separately check all
program bits. A grader must not excuse structural edits as transient state.

## Construction, reset and budgets

The JSON freezes inclusive bounds `(0,64,0)` through `(95,95,95)`, a 96×32×96
prism. Ordinary stone/glass supports, dust, torches, repeaters, comparators,
levers, stone buttons, lamps, redstone blocks and signs are allowed; exact IDs
are in the artifact. Wall variants are allowed. No command blocks, structures,
fill/clone, computational plugins, prebuilt circuits or external simulation may
implement the machine. The planner chooses the entire design.

Creative placement, removal and normal interaction have one target per
primitive. Direct placement is allowed only if supported by the inspected
adapter. `validate_build_action` checks target coordinates, action name and block
ID; it does not validate block properties, reachability, actual effects, remaining
budgets or existing target blocks. The later harness must check those, enforce
bounds on every effect and audit the resulting world. Movement and observations
also consume primitive actions. Construction intentions have at most 256 actions
and 60 seconds. Trial limits are 3600 wall seconds, 20000 primitives, 120 planner
calls, 1200 Jev calls and 24 repair rounds. A repair round is one proposed repair
followed by its recheck. Count failed/rejected actions and call retries; stop at
the first exhausted limit and save the reason. Final checks share these limits;
reserve sufficient time before entering final grading. No automatic extensions.

The dedicated creative superflat snapshot uses Java 1.21.1, seed 1600, bedrock at
60, dirt at 61–62 and grass at 63, with an empty build prism. Structures are off,
difficulty peaceful, weather clear, time 6000, daylight/weather cycles off and
randomTickSpeed zero. The artifact fixes spawn and inventory. The later harness
must produce and hash this template, restore it before every fresh conversation,
and record effective settings, inventory and player state. Snapshot creation and
restoration are trusted setup operations, never planner circuit-building tools.

## Exact live acceptance work still required

1. Milestone 2: create the specified snapshot, restore it twice after deliberate
   area/inventory/player mutations, and compare hashes and world probes. Try
   each boundary and one coordinate beyond it; reject unauthorized blocks and
   bulk operations. Verify a constructed lever–dust–lamp module toggles via real
   actions and direct server block-state reads. Persist failures and reset data.
2. Milestone 3: inspect the real planner/provider and Jev APIs and document their
   actual request/result/error fields. Map only verified capabilities to bounded
   intentions; prove rejected actions, retries, timeouts, disconnection and each
   budget exhaustion produce a stopped, recorded trial. Demonstrate an actual
   build, failed module test, repair and passing recheck in one fresh trial.
3. Milestone 4: check register load/hold/reset for all 16 values; arithmetic pairs
   (0,0), (0,15), (15,0), (15,1), (14,5), (7,8), checking O holds; program and read
   every bit of all eight words and exercise all addresses; test output lamps
   for every value, persistence and reset. Read real signals after settling.
   Return public results to the planner for repair, recording every attempt.
4. Milestone 5: freeze construction and probe map; audit whitelist and bounds;
   independently program/read back all 48 stored bits for each public program.
   Reset, step and compare A, PC, O, strobe and halted at every retirement with
   expected states. Verify no-clock hold, two post-HALT steps, reset clearing and
   storage preservation. Repeat on unchanged hardware for program 2. Include
   independent operand variations covering zero, fifteen and overflow, with
   order withheld until grading. Save timestamped raw world reads, actions,
   hardware comparisons and independent grades. Missing/ambiguous readings fail
   closed. Final checks use a trusted reader separate from planner/Jev messages;
   independent test results cannot drive repairs within that final evaluation.
5. Milestone 6: attach actual events to HUD and recording, rehearse interruption,
   fresh reset and recovery, and verify recorded behavior matches raw evidence.
   A video, HUD message or oracle trace alone never establishes success.

`RuntimeAdapters` contains configurable provider/model/adapter selection names,
all optional, and an explicit `pending_api_inspection` status. It supplies no
network protocol, credentials, fabricated endpoints or readiness claim. Pending
integration does not block this offline behavioral contract. No live checks,
models, Minecraft clients or other agents were launched for milestone 1.

## Milestone 1 continuation evidence (2026-09-22)

Bounded objective: repair strict artifact loading after the interrupted worker's
`data_bits=4.0` failure. Interfaces and the existing behavioral test were inspected
before edits. Added numeric drift tests before changing production code. The
manual live acceptance procedure remains the pending procedure above; this
continuation performed software checks only.

Exact commands run from the repository root, in execution order:

| Command | Result |
| --- | --- |
| `uv run pytest -q tests/test_redstone_contract.py` | Baseline exit 1: 1 failed, 22 passed, 1 warning in 0.05s; existing float regression failed. |
| `uv run pytest -q tests/test_redstone_contract.py` | Added tests, before fix: exit 1; 4 failed, 34 passed, 1 warning in 0.07s. Existing float case and floats for data bits, instruction bits and storage words failed. |
| `uv run pytest -q tests/test_redstone_contract.py` | After fix: exit 0; 38 passed, 1 warning in 0.07s. |
| `uv run ruff check src/noob_agent/redstone tests/test_redstone_contract.py` | Exit 1: E501 on preserved test decorator at line 67 (101 > 100). Wrapped that decorator. |
| `uv run mypy src/noob_agent/redstone` | Exit 0: Success: no issues found in 2 source files. |
| `git diff --check` | Exit 0, no output. |
| `uv run ruff check src/noob_agent/redstone tests/test_redstone_contract.py` | After wrapping: exit 0, All checks passed! |
| `uv run pytest -q tests/test_redstone_contract.py` | Final: exit 0; 38 passed, 1 warning in 0.07s. |
| `git diff --check` | Repeated after documentation/plan updates: exit 0, no output. |
| `git diff --no-index --check /dev/null src/noob_agent/redstone/contract.py` | Exit 1 (file differs from /dev/null), no whitespace diagnostics. |
| `git diff --no-index --check /dev/null tests/test_redstone_contract.py` | Exit 1 (file differs from /dev/null), no whitespace diagnostics. |
| `git diff --no-index --check /dev/null docs/redstone-computer-contract.md` | Exit 1 (file differs from /dev/null), no whitespace diagnostics. |

The pytest warning is the installed pytest-asyncio use of deprecated
`asyncio.get_event_loop_policy` on Python 3.14, scheduled for removal in 3.16.
No test failures remain. Regression cases cover floats and booleans in the three
literal fields, a nested budget, coordinates, program words and expected outputs.
Reordered/compact JSON still loads. The canonical artifact loads equal to
`MachineContract()`; software traces are (3,8) and (14,3), five steps each.
Manual comparison of the documentation, executable defaults and canonical JSON
confirmed matching encoding, capacity, arithmetic/output behavior, reset/clock
timing, programs, bounds, whitelist and budgets. No requirements or canonical
JSON changes were needed.

This continuation changed only `src/noob_agent/redstone/contract.py`,
`tests/test_redstone_contract.py`, this document, `hackathon_plan.md` and
`CHANGELOG.md`. Preserved artifacts also include the unchanged package exports
and canonical JSON. All unrelated edits/deletions, including deleted `AGENTS.md`,
were left in place. No Git stash/reset, controller work, subagents, game/model
launches or milestone advancement occurred.

Remaining: independent milestone 1 review/acceptance; runtime API inspection and
all live checks remain pending. No Minecraft behavior was verified. The caller's
estimate for this continuation is 40 credits, not a cap; actual billed usage is
unavailable and must be reconciled by the caller. No credits were assigned.

## Milestone 4 partial implementation boundary

The first milestone 4 chunk adds strict module interface declarations and charged
world inspection in `redstone/modules.py`; see `docs/redstone-trials.md` for the
exact schema, tests and live rejection evidence. All frozen behavioral
requirements above remain in force. Inspection validates observations only,
never drives declared controls, and always reports `behavioral_passed=false`.
The four public behavioral graders, bounded programming recipes, and real
two-tick STEP control path are implemented. Provider-free timeline and control
proofs passed on the dedicated server, and a negative register fixture verified
fail-fast behavior. A recent live timeline proof also exercised distinct
per-cycle recipes and storage-backed batched score readback with clean recovery.
Positive module circuits remain unverified. Per-cell structural identity
separates signal state from configuration; full-region same-hardware verification
is milestone 5 work.

### Trusted timing/control implementation boundary (milestone 4 continuation)

`GraderControl` in `src/noob_agent/redstone/grading.py` is harness-only. Its
server predicates read declared wire/lamp signals anywhere in the loaded build
prism; its lever controls validate declarations and preserve lever geometry.
`timeline()` drives only declared controls, raises STEP for two server ticks,
lowers it for 198 more, and records pulse-end and settled observations. The
trusted timeline is strictly stimulus/timing evidence: it performs no circuit
computation, program storage, expected-output synthesis or tick acceleration.
Runtime actions retain the existing reach-limited interaction and cannot invoke
these functions. Legacy `pulse_step()` and `wait_ticks()` methods are compatibility
wrappers over this same journaled timeline and cleanup lifecycle.

Live proof recorded 97338→97340, delta two, on/off success and near/far actual
signal readbacks. See the exact command, manifests and limits in
[redstone-trials.md](redstone-trials.md#milestone-4-trusted-control-continuation-2026-09-22).
A separate 198-tick wait does not make a 200-tick cycle when transport/reload gaps
intervene. That checkpoint predated the exact cycle/sampling scheduler, bounded
programming recipes and four behavioral graders now in place. No positive module
acceptance or live model-designed success is claimed.

Milestone-4 timeline continuation: `GraderControl.timeline` now measures chained
200-tick cycles, two-tick STEP and same-tick observed snapshots on the dedicated
server. Recipes accept declared boolean control levels and finite waits only;
server functions never calculate circuit results. The frozen contract is unchanged.
See `docs/redstone-trials.md` single-timeline checkpoint for actual evidence,
cleanup/recovery commands and remaining integration gaps. Per-cycle distinct
recipe batching and score aggregation into a journaled storage result have since
been implemented and passed the timeline infrastructure proof; module-level
use remains unverified. The current aggregate 1600-tick control allowance is
not a complete module-test budget policy.

Milestone-4 public behavioral implementation now compares register, arithmetic,
storage and output snapshots against Python expectations. Optional declarations
carry value-addressed input/programming recipes, restricted to declared levels
and bounded waits. Aggregate module allowances are separate from the frozen
per-program eight-step/1,600-tick deadlines. See the latest
[trial checkpoint](redstone-trials.md) for schema, budgets, software fixtures and
actual failing register evidence. Passing model-designed circuits, full hardware
identity auditing and final machine verification remain unverified.

Milestone 4 compact interface update: `recipe_templates` supports bounded bit
selection for load/add values and storage addresses/words, without supplying
wiring. See [trial continuation evidence](redstone-trials.md#milestone-4-bounded-continuation-compact-mappings-and-prompt-failure-feedback)
for strict syntax, measured complete declaration sizes, fail-fast semantics and
live cleanup evidence. Frozen behavioral requirements and timing are unchanged.
The latest batched fixture estimate is 55,616 scheduled ticks across 63
timelines. At the historical 2.994-second overhead fallback plus both
program reserves, it projects to about 3,209 seconds. About 391 seconds remain
for reset, construction, provider latency and cleanup. A 57-probe eight-sample
timeline measured 2.270 seconds of overhead and verified batched readback and
cleanup, but one infrastructure run is not a suite-wide timing bound. Positive
model-designed module acceptance remains pending.
