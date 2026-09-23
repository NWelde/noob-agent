# Repeatable redstone trials: milestone 2 continuation

This milestone is unfinished because recording and independent review are pending.
Hashed baseline resets, bounded construction, and the infrastructure-only live
lever/dust/lamp check are now implemented and verified; see the reset continuation
at the end for current commands and evidence. Earlier entries below are historical.
Do not expose raw infrastructure transports as planner tools or claim acceptance.

From the repository root, with the existing Python environment installed:

```sh
.venv/bin/python scripts/run_redstone_trial.py --preflight
```

This is the canonical **preflight** command; the complete trial command is still
pending. It prints the unique manifest path and exits **2** because the result is
incomplete, even when every read-only probe receives a response. It never starts
a server. It connects only to `127.0.0.1:25577`, checks the configured game port
is `25567`, and reads the password from
`.noob-agent/redstone-server/server.properties`. The password is neither printed
nor added to a manifest. Escaped Java-properties passwords currently fail closed
with a sanitized configuration error. The external training world is untouched.

## Implemented interfaces

- `RconClient.dedicated()` provides trusted synchronous commands with a five-second
  total exchange deadline, packet and aggregate response limits, an ordered
  empty-command response barrier, and no automatic retry. Authentication failures
  are sanitized. A lost or malformed command response closes the connection and
  raises `UnknownOutcome`, including when the request may already have executed.
- `CommandTransport` is the narrow injectable command/close protocol for trusted
  harness code and local fixtures. It must not be exposed to planner/Jev actions.
- `TrialManifest` creates unique directories and atomically replaces an fsynced
  JSON manifest. `attempt()` persists an unknown outcome before delivery;
  `observed()` records the actual reply. A crash cannot turn a pending operation
  into success. Handled failures and interruptions retain their stage/type without
  arbitrary exception text, which could contain credentials.
- `run_preflight()` freezes contract identity and budgets, journals 13 fixed
  read-only probes, and explicitly records missing template, player and recording
  evidence. Received command text is transport evidence, not readiness proof.
  Budget enforcement for construction remains to be implemented.

## Live evidence, 2026-09-23 UTC

The canonical command ran twice against the already running dedicated server;
both invocations exited 2 as designed. No server lifecycle or world mutation
commands were issued.

- First run:
  `.noob-agent/redstone-trials/20260923T024940-9848e596d36e4e40b107fcd5b417307a/manifest.json`
- Final-code run:
  `.noob-agent/redstone-trials/20260923T025227-dd7a9186b882441888c05e3ab4b7832a/manifest.json`

Each manifest contains all 13 actual responses and no transport errors. Readbacks
report seed 1600, peaceful difficulty, daytime 6000, disabled daylight/weather
cycles, randomTickSpeed 0, and a normal 20.0 TPS target. Conditional block probes
matched bedrock at `(0,60,0)`, dirt at y61 and y62, grass at y63, and air at y64.
These five points do **not** verify the whole template. The player list reported
zero connected players; attaching `noobagentbot` remains implementation work,
not an environmental blocker or a request for user intervention. Startup log
inspection also reported Java server version 1.21.1; this is local log evidence,
not an authenticated live version probe.

No lever/dust/lamp smoke, reset, inventory verification or recording was
performed. No model was invoked, no machine blueprint was produced, and no
Minecraft module success is claimed.

## Focused red/green checks

Evidence directory: `.noob-agent/redstone-trials/development-m2-transport/`.

Before implementation, the RCON behavioral suite failed collection because the
new module did not exist (`red.txt`); the trial suite likewise failed before its
implementation (`red-trial.txt`). Subsequent behavioral regressions demonstrated
a leaked cleanup exception (1 failed, 4 passed; `red-close.txt`) and missing
interruption cause (1 failed, 5 passed; `red-interrupt.txt`) before their fixes.

Final commands and results:

```sh
.venv/bin/pytest -q tests/test_redstone_contract.py tests/test_redstone_rcon.py tests/test_redstone_trial.py
# 58 passed, 1 warning in 0.27s; exit 0
# Warning: pytest-asyncio uses deprecated asyncio.get_event_loop_policy on Python 3.14.

.venv/bin/ruff check src/noob_agent/redstone/rcon.py src/noob_agent/redstone/trial.py scripts/run_redstone_trial.py tests/test_redstone_rcon.py tests/test_redstone_trial.py
# All checks passed!; exit 0

.venv/bin/mypy src/noob_agent/redstone/rcon.py src/noob_agent/redstone/trial.py scripts/run_redstone_trial.py
# Success: no issues found in 3 source files; exit 0
```

The retained outputs are `green.txt`, `ruff.txt`, `mypy.txt`, and
`live-command.txt`. Wire fixtures cover fragmented/multipart responses, alignment
across commands, disconnect after a mutation, malformed lengths/IDs, sanitized
authentication failure, total deadlines despite ongoing traffic, command
injection rejection, and refusal of other server endpoints. Journal tests cover
unique runs, startup failure, on-disk intent before delivery, cleanup failure,
and interruption. Tests use local socket pairs and injected command transports;
only the preflight above exercised the real dedicated server.

## Prior transport handoff (superseded by sidecar continuation below)

One writer; no subagents, credit assignments, controller changes, Git stash/reset,
or commits. Existing edits and deletions, including `AGENTS.md` and the legacy
scripts, were preserved. This chunk created `rcon.py`, `trial.py`, the preflight
script, two test files and this document; it appended to README and CHANGELOG.
The frozen contract and legacy connector/sidecar were inspected and unchanged.

Next implement the remaining foundation with tests first:

1. Add the separate JSONL `redstone.js` sidecar using the installed Mineflayer
   dependency, attach `noobagentbot` to `127.0.0.1:25567`, and independently read
   actual block states, inventory, position/orientation and game mode. Preserve
   `index.js`. Do not require Jev/model credentials for this work.
2. Implement a hashed baseline/template snapshot restoration on the dedicated
   server, with complete restoration evidence, effective settings validation,
   deterministic readiness, inventory and player-state verification. A few fill
   commands or sampled blocks cannot establish a verified reset. Persist failures
   before readiness and keep trusted reset operations outside the action API.
3. Add bounded single-target place/break/normal-use actions, whitelist and
   version-correct property validation, finite action/time limits, charged failed
   attempts/retries and observations, and actual effects/readbacks. Transport
   uncertainty must stop execution without fabricating state or retrying blindly.
4. Define and run the live check: restore/verify the complete baseline and initial
   player state; build an infrastructure-only lever/dust/lamp fixture; observe
   real dust power and lamp state off/on/off after normal lever use and settling;
   restore/verify a second time and compare restored world/player/inventory with
   the same hashed baseline. Retain direct evidence of removal/restoration and
   all attempted actions. Keep the fixed fixture private to the harness, outside
   planner prompts; it is never model success.
5. Record absent recording as incomplete without expanding into HUD/recorder
   implementation. Extend the canonical entry point and document exact live
   artifact paths. Run focused pytest, sidecar tests, Ruff and mypy. Return
   complete only after the whole assigned foundation is implemented and verified;
   independent acceptance belongs to the coordinator.

The worker used a bounded transport/evidence chunk within the 12-minute runtime
checkpoint. Context occupancy and billed credits are not exposed. The supplied
110-credit estimate is informational; actual usage must be reconciled by the
caller, and this continuation remains in milestone 2's pool.


## Sidecar continuation, 2026-09-23 UTC

Canonical attachment/readback check, from the repository root:

```sh
.venv/bin/python scripts/run_redstone_trial.py --observe
```

This launches only a Mineflayer client, attaching `noobagentbot` to the existing
`127.0.0.1:25567` server with protocol 1.21.1. It does not start a server or
issue RCON commands. The separate `redstone.js` preserves legacy `index.js`.
Exit **2** means incomplete, even when attachment and observations succeed.
Dependencies are the existing sidecar npm installation; see its package.json.

`Sidecar` provides sequential JSONL exchanges, finite startup/exchange deadlines,
response-size limits and sanitized failures. It journals before delivery, leaves
failed requests unknown, closes on uncertainty and never retries. Its injected
process command is for tests only. This is a trusted harness interface, not yet a
budget-enforcing action runtime. The sidecar supports actual player state,
single-block reads, registry-based whitelist/property validation and a normal
single-lever use within 4.5 blocks, followed by ten game ticks and an independent
block readback. No direct placement, removal or movement is implemented yet.
Properties use JSON booleans/integers for registry bool/int types. Player yaw and
pitch are Mineflayer **radians**, not Minecraft command degrees; reset verification
must convert conventions explicitly. Inventory reports every occupied window slot,
including equipment/crafting slots, rather than just hotbar items.

Live manifest:
`.noob-agent/redstone-trials/20260923T025811-bde6b980e5a54f41a25f102eff4209ce/manifest.json`

The command exited 2 and saved two observed responses with no errors. Actual bot
state: creative, overworld, position `(50.5,64,108.5)`, yaw pi, pitch zero, empty
inventory. Block `(48,64,95)` was air. These readbacks explicitly do **not** match
all frozen initial conditions: the required position is `(48.5,64,98.5)` and the
required inventory contains component stacks. No readiness assertion was made.
No blocks were placed or interacted with in this live check. The lever-use path
has fixture evidence only; live off/on/off and two complete resets are outstanding.
Recording remains missing; no model was invoked or model success claimed.

Focused evidence directory:
`.noob-agent/redstone-trials/development-m2-sidecar/`

- `red-node.txt`: behavioral suite failed because redstone.js did not exist.
- `red-python.txt`: behavioral suite failed because sidecar.py did not exist.
- `green-python.txt`: 67 passed, one pytest-asyncio Python 3.14 deprecation warning,
  in 0.53s. Command: `.venv/bin/pytest -q tests/test_redstone_contract.py
  tests/test_redstone_rcon.py tests/test_redstone_trial.py tests/test_redstone_sidecar.py`.
- `green-node.txt`: `node --test tests/redstone_sidecar.test.cjs`, 6 passed.
- `ruff.txt`: Ruff check passed on all redstone Python source and focused tests
  plus scripts/run_redstone_trial.py.
- `format.txt`: Ruff format check passed, 10 files already formatted.
- `mypy.txt`: `.venv/bin/mypy src/noob_agent/redstone scripts/run_redstone_trial.py`,
  success, 6 source files.
- `whitespace.txt`: `git diff --check`, exit 0.
- `live-command.txt`: canonical observe manifest path; command exit 2.

## Prior sidecar handoff (superseded by reset continuation below)

Implement hashed baseline restoration and complete world/settings/player/inventory
verification first, with failing behavioral tests. Then implement budget accounting
for all attempted primitives (including failures, retries and observations),
validated single-target placement/removal and effects verification. Keep trusted
reset commands separate from that action surface. The current sidecar deliberately
has no reset or budget authority and must not be exposed directly to planner/Jev.

The defined live acceptance check remains: restore the whole template, verify
actual player/inventory/settings, build a small lever/dust/lamp fixture, observe
off/on/off with ordinary lever interaction and real physics, then restore and
verify the same hashed baseline again. Persist independent dust-power/lamp-state
readbacks and both complete reset comparisons under a unique manifest. Extend the
canonical script with that smoke mode. Recording absence must remain incomplete.
A subsequent independent review is required; no milestone acceptance is claimed.

Files changed in this continuation: redstone.js, sidecar.py, the two new sidecar
test files, scripts/run_redstone_trial.py, README.md, CHANGELOG.md and this document.
All earlier edits/deletions and legacy index.js/world were preserved. No controller
changes, subagents, credit assignments, Git stash/reset or commits were used.
The caller's estimate is 120 credits; actual billed usage and exact active context
occupancy are unavailable. This coherent sidecar boundary is a conservative
context checkpoint within the 12-minute runtime checkpoint; continuation remains
in the same milestone pool.


## Reset and bounded-action continuation, 2026-09-23 UTC

Canonical infrastructure command (run from the repository root):

```sh
.venv/bin/python scripts/run_redstone_trial.py --smoke
```

`--reset-check` runs two full restores with an independently observed redstone
block dirtied between them. `--smoke` restores, constructs a private three-block
lever/dust/lamp fixture with bounded actions, uses the lever normally twice,
independently observes dust power and lamp state, removes the lamp with a bounded
break, and restores again. Both commands print a unique manifest path and exit
**2**, even when infrastructure checks pass: recording is missing, no model was
invoked, and no milestone acceptance is claimed. Neither starts a server. They
use only the existing dedicated endpoint and locally read credentials.

`reset.py` owns trusted restoration, separate from `Actions`. Its deterministic
snapshot descriptor and SHA-256 are included in each manifest. The owned region
is x=0..95, y=60..95, z=0..111: the whole build prism, four foundation layers and
player apron, **387,072 cells**. The actual client scans every cell, including all
block-state properties, in y/x/z order; per-layer SHA-256 comparisons must all
match. Missing chunks, missing layers or any changed state fail closed. This is
complete block-state verification of that finite region, not a hash of the entire
infinite world, entity population, or server files. Restoration uses deterministic
layer fills, each within vanilla's command limit; a fill reply alone is never
readiness evidence. The rest of the dedicated world and the external world are
not restored or claimed verified.

Exact occupied inventory slots are checked, including absence of extra equipment
or crafting items. Wall variants share base items; redstone wire uses redstone;
signs have their normal full stack of 16, other items 64. Player identity,
dimension, creative mode, position and yaw/pitch are checked. Mineflayer yaw zero
radians corresponds to the contract's Minecraft yaw 180 degrees. Effective seed,
difficulty, daytime, gamerules, clear rain/thunder predicate and normal 20 TPS
target are independently queried. The tick check verifies the configured target,
not achieved hardware throughput. The reset does not verify unrelated player
NBT such as XP or statistics, which the frozen initial-condition contract does
not specify. Static generator/version provenance remains the previously observed
Java 1.21.1 dedicated superflat server plus the sidecar's fixed protocol version.

`actions.py` exposes one-target place/break/lever interaction and block observation.
It charges the attempted action before validation, including rejected actions;
validation/readback/settling adapter calls are also conservatively charged. It
checks the contract action and wall-time ceilings before delivery and after
readbacks. Attempts and uncertain outcomes are durable; transport uncertainty
stops subsequent operations without retries. Registry validation checks whitelist,
property names, types and values before a single `setblock` can be delivered.
Placement/removal uses direct single-block commands as allowed by the contract;
lever use calls Mineflayer `activateBlock`. Actual post-state is compared with
requested effects. Standalone observations and internal effect observations
consume budget. Runtime construction begins after the initial trusted reset;
planner/Jev/intention budgets belong to the still-pending milestone-3 integration.
Movement and normal use of buttons/repeaters are not implemented; normal use is
currently deliberately limited to levers. The raw RCON and sidecar interfaces
are trusted harness internals, not planner tools.

### Actual live evidence

- Two reset check, exit 2, no errors:
  `.noob-agent/redstone-trials/20260923T030708-91221b267e41499e8584efc318124630/manifest.json`.
  Both resets verified all layers, settings and player inventory. The intervening
  redstone block was independently observed before being removed by restoration.
- Retained failed smoke, exit 2:
  `.noob-agent/redstone-trials/20260923T030935-94f3493cacbf42c5aed0cabb2956d64d/manifest.json`.
  The first off check failed because Mineflayer returned dust power as string
  `"0"`. It contains one verified reset and the failed check, not two resets or
  module success. A failing regression preceded registry-based integer readback
  normalization.
- Passing infrastructure smoke, exit 2, no errors:
  `.noob-agent/redstone-trials/20260923T031031-b9fe267177ba4d14a22c8f798142a9f3/manifest.json`.
  Normal lever interactions produced independently read dust/lamp states
  **0/false → 15/true → 0/false**. All three checks passed. Two complete resets
  verified the same baseline hash; the final region is restored. There were
  **33 charged operations**, including internal observations/validation/settling.
  This live run preceded only the subsequent unknown-outcome persistence fix,
  which changes the failure path and has a retained red/green regression.

Baseline hash for both successful live commands:
`24de052bea41617b7d685d59e3668b6c4d1929193d8b7331800d9faead7fe5ff`.
All manifests remain incomplete, recording missing, final grade not evaluated,
and model_success false. This is infrastructure fixture evidence only.

### Focused checks and handoff

Evidence directory: `.noob-agent/redstone-trials/development-m2-reset/`.
Red evidence: `red-python.txt`, `red-node.txt`, `red-actions.txt`,
`red-integer-state.txt`, `red-stopped-journal.txt`. These retain absent-interface,
integer readback and unknown-outcome persistence failures before fixes.

Final focused commands:

```sh
.venv/bin/pytest -q tests/test_redstone_contract.py tests/test_redstone_rcon.py tests/test_redstone_trial.py tests/test_redstone_sidecar.py tests/test_redstone_reset.py tests/test_redstone_actions.py
# 77 passed; one pytest-asyncio Python 3.14 deprecation warning; exit 0.
node --test tests/redstone_sidecar.test.cjs
# 8 passed; exit 0.
.venv/bin/ruff check src/noob_agent/redstone scripts/run_redstone_trial.py tests/test_redstone_*.py
# All checks passed; exit 0.
.venv/bin/ruff format --check src/noob_agent/redstone scripts/run_redstone_trial.py tests/test_redstone_*.py
# 14 files already formatted; exit 0.
.venv/bin/mypy src/noob_agent/redstone scripts/run_redstone_trial.py
# Success: 8 source files; exit 0.
node --check src/noob_agent/connectors/minecraft_sidecar/redstone.js
# exit 0.
git diff --check
# exit 0.
```

Outputs use `green-python.txt`, `green-node.txt`, `ruff.txt`, `format.txt`,
`mypy.txt`, `node-syntax.txt`, `whitespace.txt`. Live CLI outputs are
`live-command-1.txt`, `live-smoke-1.txt`, `live-smoke-2.txt`.

Remaining milestone-2 work: attach a complete recording to the canonical runner,
retain honest recording failures, review the reset/action boundary independently,
and resolve findings before any acceptance. Additional construction adapters
(movement and nonlever normal use) and intention/model/Jev budget integration are
not implemented; assess needed scope against the frozen contract before exposing
Actions to a planner. No controller change, subagent, credit assignment, stash,
reset, commit, external-world edit or frozen-contract edit occurred. Pre-existing
edits/deletions and shared Jev handler/package files were preserved.

Changed in this chunk: reset.py, actions.py, sidecar.py, redstone.js, canonical
script, test_redstone_reset.py, test_redstone_actions.py, redstone_sidecar.test.cjs,
this document, README.md and CHANGELOG.md. This is a bounded milestone-2 handoff,
not acceptance. Caller-provided estimate: **110 credits**; actual billed usage
and exact active context occupancy are unavailable and require caller reconciliation.

## Milestone 3 adapter boundary (implementation handoff)

The planner/Jev adapters are implemented and tested offline; the canonical trial
command still offers only milestone-2 infrastructure modes. **There is no connected
planner trial or live repair proof yet.** No external provider was called for this
chunk, and no credentials were requested. Recording remains pending milestone 6.

`planner.py` validates strict intention objects with a short summary and a bounded
list of offered actions. Each offer has a unique `id`, selection `criteria`, one
`action` (`place`, `break`, `interact`, or `observe`), one integer `position`, and
optional `block`/`properties`. Offers are alternatives: Jev selects exactly one.
Bounds, whitelist, field types, duplicate IDs and command-like properties are
rejected before an intention can be offered. Registry semantics and actual effects
still belong to existing `Actions`; validation alone never establishes execution.
No circuit layout is included in the production planner prompt.

Create a new `PlannerContext` for every trial. It serializes frozen public
requirements, actual observations and copied public feedback into each request;
it excludes the independent final-check list. It does not itself execute models,
keep prior-trial designs, journal events, or enforce trial budgets. The
`TrialWandbClient` reuses the existing W&B ModelClient completion path, with explicit
product `ModelSettings`/`WandbSettings`, SDK `max_retries=0` and a finite timeout
(up to 30 seconds). Development Astra settings are not consulted. The forthcoming
loop must also bound the whole await by remaining wall time; an SDK timeout alone
is not a total trial deadline.

`JevSubprocess` invokes the existing shared `scripts/jev_handler.mjs` unchanged.
Requests use `state` and `questions.action` with `type: choice`, `instructions`
and `criteria`. Only an exact string `answers.action.choice` offered in that
request is accepted. Usage and `response.modelId` are retained. The subprocess
has a maximum 30-second deadline and 256 KiB input/output ceilings, discards
stderr, kills its process group and reaps the child on failure/timeout, and never
retries. Invalid output, absent identity/usage, nonzero exit, overflow or timeout
raises a sanitized `JevError`. Only trusted tests may inject its command.

### Retained checks

Directory: `.noob-agent/redstone-trials/development-m3-adapters/`.

- `red.txt`: before production files existed, behavioral test collection failed
  with two missing-module errors. This is missing-interface red evidence, not a
  reproduced failure in previously implemented behavior.
- `green-initial.txt`: first implementation, 20 tests passed.
- `green.txt`: `.venv/bin/pytest -q tests/test_redstone_jev.py
  tests/test_redstone_planner.py tests/test_redstone_actions.py
  tests/test_redstone_trial.py` — **34 passed**, 10 pytest-asyncio/Python 3.14
  deprecation warnings, 0.72s.
- `node.txt`: `npm run test:jev` — **4 passed**, no failures.
- `ruff.txt`: `.venv/bin/ruff check src/noob_agent/redstone/jev.py
  src/noob_agent/redstone/planner.py tests/test_redstone_jev.py
  tests/test_redstone_planner.py tests/fixtures/redstone/jev_process.py` — passed.
- `mypy.txt`: `.venv/bin/mypy src/noob_agent/redstone/jev.py
  src/noob_agent/redstone/planner.py` — passed, 2 files.
- `format.txt` and `whitespace.txt`: focused Ruff format check and
  `git diff --check` outputs.

Tests cover strict intention/choice rejection, action-count validation, subprocess
failure/malformed output/deadline/output overflow, child reaping, oversized input
refusal before dispatch, fresh context isolation, actual feedback serialization,
metadata retention, and W&B SDK retry/timeout configuration. These are adapter
checks, **not** feedback-driven execution/repair or trial budget-exhaustion proof.

### Next bounded integration chunk

Implement `loop.py` with tests first. Freeze the contract hash and all budgets in
`TrialManifest`; persist credential-free planner requests/responses/identity and
Jev requests/responses/usage, intentions and observations before dependent work.
Charge planner/Jev attempts before dispatch, including failures; enforce frozen
wall, intention-time, action, model-call and repair ceilings. Bound each provider
await/subprocess by remaining time. Dispatch only the validated chosen offer to
existing `Actions`, feed actual results and public module-check failures back into
the same trial context, and stop after uncertainty with partial outcomes durable.
Test limit exhaustion, invalid choices causing no action, failure accounting,
unknown outcomes, isolated trials and feedback-driven repair before live use.

Define an explicit `--trial` fixture/provider selection in the canonical script;
fixtures must never silently substitute for real providers. The dedicated-server
live proof should use existing `TrustedReset` to verify the initial baseline,
then have a private fixture planner build a lever/dust/lamp module with a deliberate
missing dust link. Independently read the off state, interact normally, observe the
failed powered-lamp check, return those observations to the fixture planner, place
the missing dust through a Jev-selected bounded offer, and independently verify
powered and off-again states. Restore/verify the full baseline afterward. Keep the
scripted layout in clearly labeled fixtures, never in production planner prompts.
Retain the failed check, repair choice, actual readbacks and both resets under one
trial ID. Label this **fixture-driven live integration evidence**, never real-model
success. Use only dedicated 127.0.0.1:25567 / RCON 25577, preserving the other world.
Real W&B/Jev verification, required-module/full-machine grading, recording and
milestone acceptance remain pending. No live check was attempted in this chunk.

This is a safe adapter-boundary handoff under the conservative context checkpoint,
not milestone acceptance. No subagents, controller edits, credit assignments,
Git stash/reset or shared Jev/package/env edits occurred. Caller estimate: 120
credits, not a cap. Actual billed credits and precise context occupancy are not
exposed; the caller must reconcile usage. All pre-existing edits/deletions remain.

## Connected-loop handoff

This bounded milestone-3 chunk implements `TrialLoop` and extends intentions with
`max_actions`/`max_seconds`. Jev selects multiple offers within one intention,
receives actual results after each selection, consumes each offer once and can
select reserved `__finish__` to return early. All internal Actions charges count
against the intention cap; ordering/dependencies belong in offer criteria.
Planner feedback stays in one fresh context. Failed public checks and observed
effect mismatches charge the next repair round. Provider failures, invalid choices,
interruptions, unknown outcomes and exhausted limits durably stop the runtime.
Attempts are charged and journaled before dispatch; responses retain identity and
usage before dependent operations. Planner awaits and Jev subprocess deadlines
are bounded by remaining trial/intention time. Public checker callbacks must use
Actions for observations; only the wall/action limits apply outside intentions.
Checkpoint completion leaves `model_success=false` and completion incomplete.

Evidence: `.noob-agent/redstone-trials/development-m3-loop/`.
`red.txt` records the initial missing-loop collection error;
`red-exhausted-dispatch.txt` records a behavioral regression (one failed test)
showing an extra Jev call after primitive exhaustion, fixed before handoff.
`green.txt` retains the focused Python suite; `node.txt`, `ruff.txt`, `mypy.txt`,
`format.txt` and `whitespace.txt` retain the corresponding checks. Exact final
results are reported in the worker return. Shared Jev handler/package/env files,
controller and pre-existing work were preserved.

Remaining work in the **same milestone**: wire explicit fixture/provider selections
into `scripts/run_redstone_trial.py`, instantiate the existing W&B adapter and
shared-handler Jev adapter only for explicit provider mode, and freeze their public
configuration without credentials. Add clearly labeled fixture planners/checkers
and fixture subprocess selection, plus canonical-command tests. Run the previously
defined missing-dust live repair proof between two complete TrustedReset restores
under one ID, using only dedicated ports 25567/25577 and local unprinted credentials.
No Minecraft operations or live providers were invoked in this chunk. There is no
new live evidence; recording, real-provider and full-machine verification remain
pending. This is a safe code/test boundary handoff, not milestone acceptance.
Caller estimate: 120 credits; actual billing and exact context occupancy unavailable.

## Canonical connected trial continuation, 2026-09-23 UTC

This supersedes the command/live-proof tasks in the connected-loop handoff above.
Milestone 3 implementation only; acceptance is not claimed.

```sh
.venv/bin/python scripts/run_redstone_trial.py --trial fixture
```

This is a **live Minecraft** fixture run, not an offline dry run. It uses only
127.0.0.1:25567 / RCON 25577 and reads the local dedicated server's RCON password
without printing it. It verifies the initial TrustedReset, runs a fresh TrialLoop,
and attempts a final full restoration even if the loop stops or fails. Each
invocation owns a unique manifest, fresh planner context, checker and adapters;
there is no resume or cross-trial memory. Unknown/failed events remain durable.
A failed initial reset prevents planner dispatch. Failed final restoration is
recorded and must not be mistaken for a clean world. No external server or world
is touched.

Selection is mandatory: `--trial fixture` and `--trial provider` cannot be mixed
with each other or the infrastructure modes. Missing selection, invalid options
and fixture/provider option mixing fail before dispatch. Provider mode never
falls back to a fixture:

```sh
# Documented interface only; NOT executed in this continuation.
.venv/bin/python scripts/run_redstone_trial.py --trial provider --planner-model MODEL_ID
```

Provider mode requires explicitly exported `WANDB_API_KEY` and
`AI_GATEWAY_API_KEY` in the process environment, plus the explicit model ID;
optional `--planner-project` sets the W&B inference project. Python does not load
`.env` for this preflight. Missing credentials produce a unique incomplete
manifest before any Minecraft access. The W&B endpoint is fixed to
`https://api.inference.wandb.ai/v1`; model/project identifiers accept a restricted
character set. The immutable public selection records identity, endpoint,
project, token cap, temperature, reasoning setting, deadlines and zero retries,
never key values or the environment. The contract hash and limits are frozen
before connection. Providers use the existing TrialWandbClient and the unmodified
shared `scripts/jev_handler.mjs` through JevSubprocess. Provider mode has no fixture
layout or scripted checker; until public module graders are implemented, it runs
to a declared limit and cannot establish module or machine success. Do not use
it as an acceptance command yet.

The only new scripted layout is in `src/noob_agent/redstone/fixtures.py`, labeled
as fixture-only and imported only by explicit fixture mode. Its planner uses
actual public feedback to decide its next intention; its deterministic subprocess
stands in for Jev using the same request/response adapter and offered-ID validation.
It is **not** the external Jev model and never imports the shared provider handler.
A missing-dust repair is refused unless the observed lever is on, lamp is off,
and the link is air following a failed powered check. No layout is injected into
provider prompts.

### Retained live proof

Command exit **2**, intentionally incomplete:
`.noob-agent/redstone-trials/20260923T033641-db91a6ad11b846db92b9ed6f9f8ba412/manifest.json`.

One ID contains two verified full-region resets against baseline
`24de052bea41617b7d685d59e3668b6c4d1929193d8b7331800d9faead7fe5ff`,
including all 387,072 cells, effective settings, inventory and player pose:

1. Initial reset verified; bounded fixture construction places lever and lamp,
   deliberately leaving the intervening cell as air. Initial off check passes.
2. A validated Jev-fixture selection activates the lever normally. Direct readbacks
   show lever on, missing dust, lamp off. `missing_dust_powered` **fails** and stays
   in the manifest; it is not rewritten as success.
3. The next planner request receives that failure. Its repair offer
   `repair_missing_dust` is selected by the subprocess, validated against offered
   IDs and applied by Actions. Direct dust/lamp readbacks are **15/true**.
4. Another selected normal lever interaction produces direct **0/false** readbacks.
   The public checkpoint completes, then the final reset verifies the same baseline.

There were **4 fixture planner calls, 5 fixture Jev calls, 1 repair round and
35 charged operations**. No errors; elapsed 9.115 seconds. All provider counts
here refer to protocol fixtures, not external model calls. `model_success=false`,
completion incomplete, recording missing. The final world is verified restored.
The run preceded only the cleanup-error-stage regression fix, which does not
change the successful execution path. No extra live run was needed for that fix.

### Focused verification and handoff

Artifacts: `.noob-agent/redstone-trials/development-m3-command/`.

- `red.txt`: 8 failing new command/configuration tests before implementation.
- `red-cleanup.txt`: 1 failed / 10 passed, reproducing loop error incorrectly
  attributed to final reset; fixed with original stage preserved.
- `green.txt`: `.venv/bin/pytest -q tests/test_redstone_*.py` — **128 passed,
  172 pytest-asyncio/Python 3.14 deprecation warnings, 2.51s**, exit 0.
- `node.txt`: `node --test tests/jev_handler.test.mjs tests/redstone_sidecar.test.cjs`
  — **12 passed** (4 shared handler, 8 sidecar), no failures/skips, exit 0.
- `ruff.txt`: `.venv/bin/ruff check src/noob_agent/redstone
  scripts/run_redstone_trial.py tests/test_redstone_*.py` — passed.
- `mypy.txt`: `.venv/bin/mypy src/noob_agent/redstone scripts/run_redstone_trial.py`
  — **12 source files passed**.
- `format.txt`: Ruff format check on the five changed Python files — passed.
- `whitespace.txt`: `git diff --check` — passed.
- `live-command.txt`: exact live manifest path; command exit 2.
- `live-verification.json`: assertions on the retained resets, checks, selected
  repair, direct readbacks, budgets and false model-success grade — passed.

Remaining blockers: real W&B/Jev execution is deliberately unverified; general
public module graders, full-machine verification and recording remain pending.
A fresh independent review of this milestone's combined implementation/evidence
is required; this worker does not advance milestones. Continue from this section,
not the superseded command tasks above. Inspect failure/cleanup handling and the
provider preflight/limits before authorizing any real-provider trial. No keys were
requested or external providers invoked.

This bounded implementation and live-proof chunk preserves all pre-existing edits,
shared handler/package/env files and the other world. No controller changes,
subagents, credit assignments, Git stash/reset or commits. Caller estimate:
**120 credits**, not a cap; actual billed credits and exact context occupancy are
unavailable. Return at this safe test/evidence boundary within the twelve-minute
checkpoint; caller must reconcile usage.

## Milestone 4 interface foundation checkpoint (2026-09-22)

This bounded chunk implements **interface inspection, not behavioral module
grading**. It does not meet milestone 4 acceptance. The frozen machine contract
and its register/arithmetic/storage/output requirements are unchanged.

`modules.py` validates planner-selected coordinates without giving a circuit
layout. Required probe arrays are MSB first: register `a[4]`; arithmetic `a[4]`
and `o[4]`; storage `words[48]`, `readout[6]`, `address[3]`; output `o[4]` and
`strobe[1]`. Stored bits flatten addresses 0 through 7, each bits 5 through 0.
Each probe has `position` and `block` (wire or lamp); O requires lamps. Controls
have unique `id`, `role` and `position`; roles are `reset`, `step`, `programming`
and `test_input`. Exactly one reset and STEP are required, storage requires at
least one programming control, and there are at most 66 controls. All coordinates
are strict integer triples inside the frozen prism, and no probe/control aliases
are accepted. Controls must read back as levers. This first interface version
does **not** yet define programming recipes, input encoding, polarity options or
behavioral stimulus sequences. Those must be validated before controls can run.

Planner intentions may include `module_inspection` containing that declaration;
`actions` may be empty for an inspection. The provider-capable loop charges each
world read, records the declaration and detailed failures, and returns results
to the same planner conversation. Failed inspection requests consume repair
rounds on continuation. Valid inspection does not end the trial or pass a module.
No control is operated by inspection. `behavioral_passed` is always false;
`valid` means only that the declared interfaces could be read. Final independent
checks remain unevaluated and `model_success=false`.

`structural_identity` preserves coordinates, block type and configuration while
removing only block-specific mutable signal fields (including repeater locked
state). It preserves delay, mode, facing and wire shape. Inspection collects
identity only for valid declared cells; it is **not** a complete hardware audit
or same-machine proof. A later full-region scan must use this distinction.

### Manual live check and retained evidence

From the repository root, run the existing canonical command with a declaration:

```sh
uv run python scripts/run_redstone_trial.py --inspect-module path/to/declaration.json
```

This is a read-only dedicated-server operation on 127.0.0.1:25567 (RCON 25577).
It never restores or edits either world, supplies a design, calls a provider or
requests credentials. It returns exit 2 to indicate incomplete trial evidence.
For a rejection check, declare lamp/wire/lever probes at loaded empty cells;
verify raw air readings, failed target details, charged reads and false success.
For future real modules, use planner-declared interfaces and separately run the
still-pending behavioral graders; passing inspection will not suffice.

Evidence root: `.noob-agent/redstone-trials/development-m4-interface/`.

- `red.txt`: new module tests initially failed collection because implementation
  did not exist. `red-loop.txt`: two behavioral failures before loop/command
  implementation (inspection stopped planning; canonical option was absent).
- `green-targeted.txt`: 51 tests passed after integration, before two additional
  budget/control tests. `python.txt`: final focused suite, 152 passed; installed
  pytest-asyncio emitted 181 Python 3.14 event-loop-policy deprecation warnings.
- `node.txt`: 12 passed, zero failed (dedicated sidecar and shared Jev handler).
- `lint.txt`: Ruff passed; `format.txt`: 24 files already formatted;
  `types.txt`: mypy passed for 12 source files. `whitespace.txt`: Git diff check.
- First live command: `live-command.txt`, declaration `declaration.json`.
  Manifest `.noob-agent/redstone-trials/20260923T034826-c3e9c6bf9989487f983cd16a07368257/manifest.json`:
  one charged read, sidecar failure at a remote cell, runtime stopped with unknown
  transport outcome. No completed inspection or invented air evidence.
- Second live command: `live-loaded-command.txt`, declaration
  `declaration-loaded.json`. Manifest
  `.noob-agent/redstone-trials/20260923T034905-1f38fcd9dd504faebc5946e08041c463/manifest.json`:
  eight charged world reads, eight detailed failures, no errors. All five probe
  cells and three control cells actually read air. `valid=false`,
  `behavioral_passed=false`, `model_success=false`. No blocks were modified.

Controlled worlds cover all four interface shapes, missing/ambiguous readings,
invalid declarations, action/time budgets, mutable-state identity separation,
and failed-inspection/repair/continued-planning feedback. They establish only
inspection infrastructure, **not four behavioral graders or built modules**.

### Same-milestone continuation

Implement a trusted bounded control path and actual server-tick timing first.
The legacy reach-limited lever operation waits ten ticks; it cannot deliver the
frozen two-tick STEP. Do not repurpose it, accelerate ticks, or compute circuit
outputs externally. Resolve unloaded-cell access for arbitrary declared probes
and controls within the build area. Then define bounded programming/input
recipes and the four public behavioral graders: register all 16 load/hold/reset
values; arithmetic frozen pairs with O hold; all 48 stored bits and all addresses;
output all values, persistence and reset. Preserve failed checks and repair
feedback and keep public checkpoints separate from final independent grading.
Use failing behavior tests before those changes. Complete circuit/live-provider
success, full-region same-hardware checks, recordings and milestone acceptance
remain pending. No subagents, controller edits or budget assignments were made.
Caller estimate: 150 credits; actual billed usage is unavailable to this worker
and must be reconciled by the caller. This is a continuation in milestone 4.

### Milestone 4 trusted control continuation (2026-09-22)

This bounded chunk implements harness control prerequisites, not any of the four
behavioral graders. No controller, legacy sidecar, planner/provider integration,
or frozen contract artifact was changed. Live model-designed module success and
milestone acceptance remain pending.

Canonical manual/live control proof, from the repository root:

```sh
uv run python scripts/run_redstone_trial.py --control-proof
```

Exit 2 deliberately means an incomplete infrastructure trial, including when the
control proof succeeds. Inspect the printed manifest's `checks`, `errors`, events
and cleanup readbacks. It requires seven empty cells on existing grass at the
near and far edges; occupied fixture cells cause refusal before placement. It
places temporary levers/dust, reads both ends of the region, tests near/far levels
and a distant STEP, deliberately removes a distant control to test rejection,
exhausts its frozen 210 scheduled-tick allowance, then removes only its fixtures
using a fresh cleanup connection and journals air predicates. No world reset,
external model calls, circuit layout or externally computed outputs are involved.

Verified dedicated-world evidence:

- `.noob-agent/redstone-trials/20260923T035940-caa5a16556d14693ab8da3f459924c30/manifest.json`:
  STEP at `[95,64,95]` changed high at server game time **97338**, low at **97340**;
  server command success scores `on=1`, `off=1`, delta **2**. Near dust `[1,64,0]`
  and distant dust `[93,64,95]` independently read power **15** under their
  declared controls, then all four declared probes read **0**. Separate scheduled
  wait measured **198** server ticks (97351 through 97549). All seven cleanup
  air predicates returned `Test passed`; the already-removed failure fixture
  correctly reported no setblock change and still verified air.
- Four intentional ValueErrors record undeclared control, direct STEP level,
  oversized wait and missing distant lever. The fifth intentional error is
  ActionLimit at 210 scheduled ticks, with stopped state recorded. Final counts:
  **24 charged operations**, **274 bounded harness commands**, **210/210**
  scheduled ticks, **100** shared action limit, **4096** command limit.
- Earlier successful proof:
  `20260923T035748-b029360dbd084c2893ba803936d3ed61/manifest.json`, STEP 95060→95062.
- Retained failures: `20260923T035626-7c4fb24a9d804481ad3ef2cee374c82d/manifest.json`
  exposed the server's `Test passed` reply; no fixture was placed.
  `20260923T035649-b8d3ffb6c71d47b1b3814ec264b0ee85/manifest.json` recorded an
  uncertain RCON exchange after reload and failed cleanup on that closed
  connection. Recovery `20260923T035747-2493345dfb0644d2854a958ee7ac74d9/manifest.json`
  removed exactly those six fixtures and verified every cell as air. Reload now
  uses its own disposable dedicated RCON connection; cleanup uses a fresh one.

All IDs above are under `.noob-agent/redstone-trials/`. Generated scheduling
functions are retained per operation beside each manifest. Dedicated-world packs
and timing objectives use unique `ng...` namespaces and remain installed as
passive evidence (no load/tick entry points). Never expose function invocation or
raw RCON to runtime agents. The templates in `scenarios/minecraft/redstone-grader/`
contain only validated lever transitions, scheduling and command/game-time
measurement. Scoreboards hold harness timestamps/status, never program storage,
arithmetic, expected outputs or circuit results. Tick rate is never changed.

The next chunk's exact trusted Python interface is:

```python
from noob_agent.redstone.grading import GraderControl

g = GraderControl(actions, validated_module_declaration, max_ticks=1600)
g.probe("a", 0)                 # declared role/index; actual server signal state
# g.probe("o", 0) reads lamp lit; wire probes read integer power 0..15.
g.set_control("declared_id", True)  # non-STEP lever only; preserves face/facing
g.pulse_step()                  # fixed two-server-tick high; requires initially low
g.wait_ticks(200)               # integer 1..200, recorded actual server delta
```

Each operation charges the existing Actions budget and invokes its runtime/time/
intention guards. Rejected attempts and raw command responses are retained;
uncertain transport outcomes stop the shared runtime without retries. A manifest
has one frozen scheduled-tick allowance (1..1600), shared across new control
objects, and a 4096-command ceiling. Every declaration is revalidated before
access. Probes are server block predicates and do not depend on the client's
chunk cache or reach. They read only the declared signal property, not a full
hardware identity snapshot. Missing/unloaded/unrecognized evidence fails closed.
The existing `Sidecar.request`/Node dispatch allowlists expose no privileged
operation. Normal `interact` still uses player reach and ten **client** ticks.

Limitations for the next milestone-4 chunk:

- `pulse_step(); wait_ticks(198)` is **not an exact 200-tick instruction cycle**:
  reload/transport/polling gaps add server ticks. Only each recorded scheduled
  interval is exact. Add a single server timeline for the frozen 200-tick cycle
  and any exact sampling boundary before claiming frozen instruction timing.
  Server predicates sample sequentially, not as one atomic multi-bit snapshot.
- Tick budgets currently count reserved scheduled intervals; incidental game
  ticks are not included. Actual end-to-end program deadlines and the eight-step
  limit must be enforced by the upcoming behavioral/program grader. Wall time
  and Actions guards remain active now. A fresh manifest is a fresh budget.
- Keep recipe control IDs/levels/finite waits declarative; do not accept commands,
  arbitrary positions or grader function names from planner/Jev. Add bounded
  programming/input recipes and all four behavioral graders: register all 16
  load/hold/reset values; arithmetic frozen pairs plus O hold; storage all 48
  bits and all addresses; output all values, persistence and reset. Feed actual
  failed checks back for repair and keep public checks separate from final ones.
- Build-area chunks are already server-loaded in the dedicated world. This
  path does not load chunks or fabricate air when that precondition fails.
  Existing `--inspect-module` still uses its original client readback path;
  integrate the new trusted probe API when implementing behavioral checks.

Tests-first logs (initial missing API, actual server reply regression, budget
replenishment/mutated bounds and denied-attempt regressions) are retained under
`.noob-agent/redstone-trials/development-m4-control/`. Final focused Python command:

```sh
uv run pytest -q tests/test_redstone_grading.py tests/test_redstone_modules.py tests/test_redstone_sidecar.py tests/test_redstone_actions.py tests/test_redstone_loop.py tests/test_redstone_command.py
node --test tests/redstone_sidecar.test.cjs
```

Python: **84 passed, 172 deprecation warnings**. Node: **10 passed, 0 failed**.
Ruff check/format and mypy passed for the new grader and changed Python files;
`git diff --check` passed. No external model calls or subagents. Caller estimate
**120 credits**; billed usage is unavailable and must be reconciled by the caller.
This is a same-pool milestone-4 handoff, with no credit assignment or acceptance.

### Milestone 4 single-timeline checkpoint (2026-09-23 UTC)

This continuation completes the bounded timeline prerequisite only. Four independent
behavioral graders, their programming case schema, and planner repair integration
remain unfinished. No milestone acceptance or model-built circuit success is claimed.

Canonical dedicated-server fixture command (intentional exit 2):

```sh
uv run python scripts/run_redstone_trial.py --timeline-proof
```

`GraderControl.timeline(recipe, cycles=1..8)` executes one server timeline. A recipe
is a list of at most 128 dictionaries, each exactly `{"control":"declared_id",
"level":true}` or `{"wait":1}`. Levels must be booleans; waits strict integers
1..200. STEP levels, unknown IDs, extra fields, commands and arbitrary coordinates
are rejected. Recipe preparation precedes the cycles. Each cycle raises STEP,
samples all declared probe bits together at +2 before lowering STEP, then samples
settled bits at +200 before the next rising edge. Every frame records actual game
time; any incorrect delta, missing probe or unverified lever effect fails closed.
Functions only change declared stimuli, schedule frames and record observed block
properties. Snapshot scores contain raw observed powers/levels, never expected
values or computed arithmetic. Python comparisons are separate.

The entire duration is reserved before server access and never refunded. Existing
manifest tick and command counters are shared, including across GraderControl
instances. Generated function lines are conservatively charged against the same
4096-command allowance as RCON polling/readback. Each bounded timeline charges one
Actions operation and obeys wall-time/intention guards. Cleanup has a separately
journaled finite safety allowance (`number of functions + 3`), cannot run grading,
and never replenishes runtime budgets. Preparation counts against aggregate ticks;
this is **not yet** a full-program eight-retirement/deadline grader.

Live evidence:
`.noob-agent/redstone-trials/20260923T040947-dd0f4781a9614eb8becd0cc70497f204/manifest.json`.
A four-tick preparation preceded rising edges **109427, 109627**, falling edges
**109429, 109629**, and settled boundaries **109627, 109827**. Both cycles are exactly
200 ticks; both pulses are exactly two. Both pulse snapshots observed `[15,14,0,0]`,
both settled snapshots `[0,0,0,0]`, with four passing independent Python comparisons.
A deliberately wrong expected vector remains a failed comparison. A second timeline
with a physically removed dust probe recorded missing state (`-1`) and rejected it;
the shared runtime stopped and refused the next timeline. Final reserved ticks
**604/604**, runtime commands **760/4096**. All six fixture cleanup air checks passed.
This is a passing/broken dust timing fixture, not a register/arithmetic circuit.
Earlier successful timeline runs are retained at `20260923T040820-b0cc9405a5bc40d09e8f35fd4c1b2ea2`
and `20260923T040920-433097ce2c4f44eb9eda35223876d53b` under the same evidence root.

Timeline packs have no load/tick entry points. Before installation a manifest marks
resources pending. Success and failure both cancel all owned schedules, invoke the
retained abort function to return STEP low, remove the objective, delete the owned
pack and reload on a disposable connection. Generated functions remain beside the
manifest. Cleanup failure leaves `recovery_required`; new graders and timelines
refuse pending resources. After terminating the original writer, recover with:

```sh
uv run python scripts/run_redstone_trial.py --recover-timeline PATH/manifest.json
```

Recovery is idempotent, touches only the recorded validated namespace and does not
resume the trial or restore allowance. A hard crash may leave STEP high until this
recovery runs. Never recover concurrently with the active writer. Legacy
`pulse_step`/`wait_ticks` scheduling still uses the earlier passive-pack lifecycle;
consolidate that lifecycle or migrate callers before milestone completion. Old
control-proof packs were not deleted by this continuation.

Tests-first and focused artifacts:
`.noob-agent/redstone-trials/development-m4-timeline/`. `red.txt`: **4 failed,
16 passed** before production implementation. Final `python.txt`: **95 passed,
172 deprecation warnings** across grading/modules/sidecar/actions/loop/command.
`node.txt`: **10 passed, zero failures**. Ruff, mypy (2 source files), format and
Git whitespace checks passed. Tests cover strict rejection, timing and missing
probe/control failures, aggregate limits, cleanup after failure, interrupted
cleanup recovery and pending-resource rejection. Live manifests retain raw RCON
responses and generated functions; `live-final.txt` and `live-verification.json`
identify the final fixture evidence. No providers were invoked.

Exact next work in the same milestone pool: add tests-first, value-addressed
programming/input recipes using this restricted operation vocabulary; establish
one aggregate module-test allowance large enough for the frozen public cases,
separate from per-program eight-step/1600-tick counters (never create fresh graders
per case). Current conservative 1600 aggregate ceiling cannot fit all module cases.
Implement all-16 register load/400-tick hold/reset, frozen arithmetic pairs with O
hold, all storage words/addresses/reset preservation, and all visible-output values,
persistence and reset. Compare independent Python expectations with raw snapshots;
feed actual failed checks into existing planner repair feedback. Add meaningful
passing/failing circuit fixtures and live evidence for those graders. Address full
hardware identity, legacy scheduling cleanup, and external-provider demonstrations
separately. The current fixture is not evidence those modules work.

Caller estimate **115 credits**; actual billed credits and exact context occupancy
are unavailable. No credits assigned, subagents, controller edits, Git stash/reset,
or milestone acceptance. Shared user changes and legacy sidecar behavior preserved.

### Milestone 4 behavioral checkpoint (2026-09-23 UTC)

Implemented all four public module graders in `behavior.py`. Register checks all
16 values, reset before/after each load and a 400-tick no-STEP hold. Arithmetic
checks the six frozen pairs with modulo-16 expectations and a nonzero O=9 sentinel
that must survive LOAD/ADD and hold. Storage checks two complementary eight-word
patterns, all 48 observed bits, reset preservation, all addresses and their readout.
Output checks every value, OUT strobe toggling, persistence, LOAD/ADD preservation
and reset. Expectations are Python values; world functions only operate declared
controls, schedule time and capture raw signals. Missing/ambiguous snapshots fail.

A declaration optionally adds `recipes`, a finite mapping from value keys to lists
of at most 128 operations. Each operation is exactly a declared programming/input
lever level (`{"control":"id","level":true}`) or a strict integer wait
(`{"wait":1}` through 200). No commands, positions, reset or STEP are accepted in
these supplied recipes. Register requires `load:0`..`load:15`; arithmetic/output
also require `add:0`..`add:15` and `out`. These prepare one instruction for the
trusted STEP. Storage requires `address:0`..`address:7` and `write:ADDRESS:WORD`
for `[0,63,21,42,15,48,32,16]` and `[63,0,42,21,48,15,31,47]`. Writes execute with
reset held. Missing required keys reject before world access. No wiring is supplied.
Submitting recipes with the planner's module declaration runs behavioral checks;
without recipes it remains read-only inspection. Observed failed checks, including
raw snapshots, enter the same planner conversation and charge repair rounds.

Public module grading uses a fixed aggregate allowance of 96,000 scheduled ticks
and 1,000,000 conservatively counted commands, shared across cases, repairs, modules
and grader instances in one manifest. Existing 20,000-action/3,600-second trial
limits remain active and may stop a suite earlier. These are allowances, not a
promise that every repair fits. Profiles cannot change once a manifest budget is
established. The public planner requirements disclose the allowance. Timeline
`sample_only=True` observes reset/hold/address states without STEP. Optional
`program_id` timelines retain separate eight-step/1,600-execution-tick/120-second
counters across calls and instances. Recipe preparation charges aggregate ticks,
not execution ticks; program wall time conservatively includes preparation.
Full-machine invocation and final program verification remain milestone 5 work.

Canonical actual-world negative check (intentional exit 2):

```sh
uv run python scripts/run_redstone_trial.py --behavior-negative-proof
```

Evidence: `.noob-agent/redstone-trials/20260923T041729-dd0b950f9d5f46e0817e33da1983e649/manifest.json`.
A disconnected, constantly powered non-register fails the real public register
reset check: expected A=0, observed A=8. This fail-fast proof intentionally leaves
remaining cases unexecuted (`complete=false`, `behavioral_passed=false`). It used
400 ticks, 281 runtime commands, no errors; timeline resources are clean and all
seven fixture-cell removal predicates passed. It is actual failure evidence, not
a passing register or a planner-designed circuit. The prior actual-server exact
STEP/snapshot evidence at `20260923T040947-dd0f4781a9614eb8becd0cc70497f204` remains
applicable. No external models were called.

Tests-first evidence: `development-m4-behavior/red.txt` retains **9 failed** before
production implementation. Controlled sequential world-reader fixtures exercise
passing and failing paths for every grader, plus secondary O/strobe/address/readout
corruption, strict recipe rejection, shared counters, program deadlines and actual
failed-check repair feedback. These are software fixtures, not Minecraft circuits.
Focused results are retained in `development-m4-behavior/python.txt`.

Precise remaining milestone-4 code: consolidate legacy `pulse_step`/`wait_ticks`
(and `_schedule`) onto the journaled timeline cleanup lifecycle, or retire those
legacy paths and migrate the old control proof. Public behavioral grading already
uses the recoverable timeline path. Full hardware identity auditing is still absent;
current observations cover declared interfaces only. Passing model-designed live
module evidence is deferred until provider use; no handcrafted passing computer
was added. Independent review/acceptance and full-machine/recording work remain
pending. Caller estimate: 60 credits; actual billed usage and exact context usage
are unavailable. No credits assigned, controller changes, subagents or Git resets.

Final focused command:

```sh
uv run pytest -q tests/test_redstone_behavior.py tests/test_redstone_grading.py tests/test_redstone_modules.py tests/test_redstone_loop.py tests/test_redstone_planner.py tests/test_redstone_command.py tests/test_redstone_actions.py tests/test_redstone_sidecar.py
```

Result: **122 passed, 190 deprecation warnings, 9.13 seconds**. Node sidecar:
**10 passed, zero failures**. Ruff check and format (10 files), mypy (5 source
files), and `git diff --check` passed. The live command returned intentional exit
2 with the failure and cleanup evidence above.

### Milestone 4 bounded continuation: compact mappings and prompt failure feedback

This continuation implements compact declarations and production fail-fast feedback;
**runtime acceptance is still blocked**. No allowances, frozen cases, STEP timing,
controller files or independent Jev handler work changed. No external model calls,
passing model-designed circuit claim, milestone acceptance or credit assignment.
Caller estimate: **85 credits**; actual billed usage is unavailable.

`module_inspection.recipe_templates` maps `load`, `add`, `address`, or `write`
to at most 128 control/wait operations. A control level is either a boolean or
`{"parameter":"value","bit":0,"invert":false}`. `invert` defaults to false;
bits count from the least significant bit. Load/add accept value bits 0..3;
address accepts address bits 0..2; write accepts address bits 0..2 and word bits
0..5. Only declared programming/test_input controls are allowed. Templates cannot
operate reset/STEP, select positions, evaluate expressions, or provide layouts.
Unknown fields, wrong types, invalid parameter/bit combinations, excessive waits,
and overlapping explicit/template families reject before world access. Explicit
recipes remain supported; `out` remains explicit. A templates-only declaration
also requests behavioral grading. Required cases must all resolve before execution.

The production planner loop now calls `grade_module(..., fail_fast=True)`. The
first observed mismatch is saved, returned for repair, and charged as a repair
round. `complete=false`, `behavioral_passed=false`, and `stopped_after_failure=true`
mean remaining cases were **not run**. Direct diagnostic callers can explicitly
use `grade_module(..., fail_fast=False)` for exhaustive results.

Complete interface intentions (including all probes, controls, preparation recipes,
summary and empty action list) were validated and serialized as compact JSON. Local
`tiktoken 0.14.0` measurements:

| Module | Bytes | cl100k_base tokens | o200k_base tokens |
| --- | ---: | ---: | ---: |
| Register | 1400 | 429 | 434 |
| Arithmetic | 2301 | 692 | 700 |
| Storage | 5012 | 1391 | 1399 |
| Output | 2130 | 647 | 655 |

These demonstrate complete declarations well below the 4096-token output limit for
both measured encodings, with no circuit layouts supplied. They do not guarantee
counts for every configurable provider tokenizer or arbitrary declaration. The
fixtures cover all nine input bits, storage programming strobes and waits; tests
prove identical expanded operations versus the earlier explicit declarations and
exercise all 512 address/word combinations. Artifacts and reproducible measurement
script: `.noob-agent/redstone-trials/development-m4-compact/` (`measure.py`,
`measurements.json`, and the eight complete compact/explicit JSON intentions).
Run from the repository root with
`uv run --with tiktoken python .noob-agent/redstone-trials/development-m4-compact/measure.py`.
No dependency/lockfile changes were needed.

Deterministic audit of the **unchanged** suites (one 200-tick exact STEP per cycle,
plus every explicit wait; storage fixture writes wait one tick each):

| Module | Timelines | Scheduled ticks | Seconds at 20 TPS |
| --- | ---: | ---: | ---: |
| Register | 64 | 22400 | 1120 |
| Arithmetic | 36 | 9600 | 480 |
| Storage | 38 | 4816 | 240.8 |
| Output | 128 | 35200 | 1760 |
| Total | 266 | 72016 | 3600.8 |

There is already **negative 0.8 seconds headroom** under the shared 3600-second
cap before server overhead, construction or the two final programs. This is an
open blocker, not a usable duration budget. The audit is a local measurement
script, not yet a production preflight duration estimator. Next work must remove
only non-required scheduling, add deterministic production accounting and reserve
explicit construction, overhead and two-program headroom; increasing allowances
cannot solve it. Candidate redundancies to review against the frozen contract:
register's initial reset immediately after the preceding final reset, arithmetic's
extra hold400, and output's extra hold400/repeated reset/second OUT for every value.
Preserve all sixteen register loads and 400-tick holds, all six arithmetic pairs
and O preservation, all storage words/address/reset checks, all sixteen output
values and LOAD/ADD/reset behavior, exact two-tick high/200-tick retirement timing,
and shared charging. No scheduling reduction was made in this chunk.

Bounded live check: `uv run python scripts/run_redstone_trial.py --behavior-negative-proof`
returned intentional **exit 2**. Evidence:
`.noob-agent/redstone-trials/20260923T042603-92da332e925d42a9841885284cb08a41/manifest.json`.
The first reset failed with expected A=0, observed A=8; remaining cases were not
run. Actual game times were 128951, 129151, 129351 (exact 200/400 tick offsets).
The timeline operation ran from 04:26:03.988393 to 04:26:25.217755 UTC:
**21.229362 seconds**, versus nominal **20 seconds**, an observed **1.229362-second
excess** including transport, installation, polling, evidence and cleanup. Whole
proof startup through last cleanup observation was 21.632987 seconds. This single
four-probe sample does not establish a storage-probe or full-suite overhead bound.
The manifest records 400 ticks, 283 charged commands, zero errors, clean timeline
resources, and seven successful fixture-removal predicates. No passing circuit
was constructed.

Tests first: the production failure-feedback regression failed because `complete`
was true (1 failed); four compact-equivalence cases failed because declarations
rejected `recipe_templates` (4 failed, 6 validation cases passed). Final focused
command is the eight-file command above: **145 passed, 199 deprecation warnings,
12.90 seconds**, retained in `development-m4-compact/python.txt`. After adding a
stronger persisted-feedback assertion and fixing a lint-only variable shadow,
the targeted loop regression passed **2 tests, 19 deselected, 19 warnings, 0.22
seconds**. Ruff check/format passed for seven touched Python files; mypy passed
for four touched source files; `git diff --check` passed.

Handoff: compact declarations and fail-fast repair are implemented; scheduling
reduction, production duration accounting, positive construction/two-program
headroom, and consolidation/retirement of legacy `pulse_step`/`wait_ticks`/
`_schedule` with migrated recoverable-cleanup proof remain unfinished. Public
behavioral grading already uses recoverable timelines. Passing model-designed
module evidence stays deferred. Full-machine identity auditing is milestone 5.
