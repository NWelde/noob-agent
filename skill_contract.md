# Generated Skill Contract

## Purpose

A skill is a model-written Python function that packages a useful procedure the
agent learned during training. It is the only executable artifact that may
survive the reset between training and held-out evaluation.

A skill is not a connector change, a hidden prompt, a grader edit, or a model
weight update. It can do only what a player could already do through the frozen
primitive tools.

## Submission package

The Builder submits one package per candidate:

```text
skill.py          exactly one exported async function
skill.json        metadata and declared limits
```

The Python entry point has this form:

```python
async def run(context: SkillContext, inputs: dict[str, object]) -> SkillResult:
    """Plain-language purpose and required starting conditions."""
```

The BDP supports one accepted skill at a time. A candidate source file may be at
most 12 KiB, metadata at most 4 KiB, and the entry point may accept at most eight
input fields.

## Runtime API

Generated code receives only this capability object:

```python
class SkillContext(Protocol):
    async def observe(self) -> Observation: ...
    async def call(self, tool_name: str, **arguments: object) -> StepResult: ...
    def remaining_budget(self) -> SkillBudget: ...
    def log(self, event: str, fields: dict[str, JsonValue]) -> None: ...

class SkillResult(BaseModel):
    status: Literal["succeeded", "failed", "inconclusive"]
    summary: str
    evidence: list[EvidenceRef]
    outputs: dict[str, JsonValue]
    primitive_actions_used: int
```

`EvidenceRef` may point only to an observation sequence, action ID, public
object ID, or public message from the current episode. A skill cannot declare
success without at least one evidence reference captured after its final
state-changing action.

`context.call()` accepts only tools in the episode's connector manifest. A
skill cannot call another generated skill in the BDP. It may branch, loop within
its declared bound, inspect results, and return a clear failure.

## Metadata

`skill.json` contains:

```json
{
  "name": "operate_resonator",
  "version": 1,
  "parent_version": null,
  "purpose": "Use the unfamiliar device and verify its output.",
  "input_schema": {},
  "required_tools": ["observe", "move_to", "use_object", "wait"],
  "max_primitive_actions": 8,
  "max_wall_time_seconds": 30,
  "success_claim": "The expected output is visible after activation.",
  "api_version": "noob-agent.skill.v1"
}
```

Names use lowercase letters, numbers, and underscores and cannot shadow a
primitive name. The registry, not the Builder, assigns the final immutable
version number and source hash.

## Permissions

Candidate code may use a small allowlist of Python built-ins and pure standard
library helpers such as `math`, `statistics`, and `dataclasses`. All interaction
with the game goes through `SkillContext`.

It may not:

- Read or write files, environment variables, process state, or credentials.
- Use network, sockets, subprocesses, threads, dynamic imports, reflection,
  bytecode, native extensions, `eval`, `exec`, or serialization that can execute
  code.
- Import the connector, grader, registry, W&B clients, or sandbox client.
- Read scenario source, held-out fixtures, other episodes, private state, or the
  model conversation.
- Change budgets, suppress tracing, retry an unknown action, or catch a
  cancellation in order to continue.

Source scanning is only an early rejection step; runtime isolation in a fresh
CoreWeave Sandbox is the actual security boundary. The container receives no
secrets and has networking disabled.

## Limits and accounting

One skill invocation counts as one Action-agent decision. Every primitive it
calls is recorded as a nested step and charged to the episode primitive budget.
The stricter of the skill declaration and remaining episode budget always wins.

Default candidate limits are eight primitive calls, 30 seconds wall time,
256 MiB memory, 64 KiB combined logs/output, and no network. The Builder may
request lower limits, never higher ones. A timeout returns `inconclusive` to the
agent and fails validation.

No automatic retry occurs after an `unknown` primitive result. A skill must stop
and surface the ambiguity. At most one initial build and one repair call are
allowed per learning run.

## Required behavior

A useful skill must:

1. State its purpose, inputs, and starting conditions.
2. Validate its inputs before changing the environment.
3. Use only public observations and primitive results.
4. Bound every loop and recovery path.
5. Check postconditions in the environment rather than trusting that a tool call
   succeeded.
6. Return `failed` for a known unmet condition and `inconclusive` for ambiguous
   evidence.
7. Produce evidence that an independent reader can connect to recorded steps.

A skill may recover from a known, confirmed failure once if its remaining budget
permits. It may not repeatedly guess or silently substitute a different goal.

## Validation pipeline

Validation uses only training evidence plus a small validation variation. It
never uses held-out evaluation scenarios.

1. **Package check:** names, sizes, schemas, API version, and source hash.
2. **Static policy check:** parse the AST and reject forbidden constructs or
   imports.
3. **Load check:** import in a fresh isolated runtime and find the exact entry
   point.
4. **Contract check:** validate input/output types and declared tool access.
5. **Training replay:** run against recorded public examples using a fake
   connector with the recorded responses.
6. **Negative cases:** missing target, invalid input, failed primitive, unknown
   delivery, and exhausted budget.
7. **Repeatability:** run each deterministic fixture three times and require the
   same status and primitive call sequence.
8. **Validation variation:** run one changed layout or object ID mapping not
   shown to the Builder.
9. **Live smoke test:** optional before the demo; run in the training world only.

Acceptance requires every mandatory check to pass, zero policy violations, no
timeout, and a correct visible postcondition in the validation variation. A pass
means fit for this evaluation, not universally correct or production-safe.

## Repair and versioning

On rejection, the Builder receives only public validation errors: failing check,
public inputs, public results, and its own logs. It never receives private grader
predicates or held-out data.

One repair is allowed. It creates a new candidate with `parent_version` pointing
to the rejected version. Nothing is overwritten.

Registry states are `proposed`, `validating`, `rejected`, `accepted`, and
`retired`. Only `accepted` versions appear in held-out tool lists. Every version
stores its source, content hash, parent, authoring episode and model, evidence
selection, validation records, model usage, creation time, API version, and
status reason.

An accepted artifact is frozen for the complete comparison cell. Any source,
metadata, runtime, connector, or validation-policy change produces a new hash
and requires a new evaluation run.

## Held-out rules

Before held-out evaluation, the game and model conversation reset. The Action
agent receives the public task, primitive manifest, and accepted skill's name,
purpose, input schema, and result schema. It does not receive training traces,
Builder discussion, validation fixtures, or private scores.

Held-out failures do not trigger repair. Otherwise evaluation data would leak
back into learning. A skill is credited only when it is invoked, stays within
budget, and the independent grader verifies the task result.
