"""The Builder's prompts: evidence in, one skill candidate out.

These prompts describe ordinary controls, the real skill API, and a reply
format. They never carry grader predicates, held-out data, clean/faulty
identity, or the answer to an unfamiliar mechanic, and they never invite the
model to change the connector, the budgets, the scenario, or its own acceptance
rules.

Section 22 of `hackathon_plan.md` grounds the prompt: the system prompt carries
the skill API reference and one worked example for a fictional tool, and the
user prompt carries each primitive's definition and the public observations
the attempt produced.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence

from noob_agent.agents.evidence import EvidenceObservation, TraceEvidence
from noob_agent.domain.model import ToolDefinition
from noob_agent.skills.errors import SkillValidationIssue
from noob_agent.skills.metadata import API_VERSION

# Rendered system plus user prompt, estimated as characters divided by 4, so a
# build fits the 12,000-token and a repair the 8,000-token protocol ceilings.
PROMPT_TOKEN_LIMIT = 5_000
DEFAULT_BUILD_MAX_OUTPUT_TOKENS = 6_000
DEFAULT_REPAIR_MAX_OUTPUT_TOKENS = 3_000


def estimated_tokens(text: str) -> int:
    """Characters divided by 4, rounded up, so an estimate never undercounts."""
    return -(-len(text) // 4)


SKILL_API_REFERENCE = """Skill API (noob-agent.skill.v1). A skill is one module with:

    async def run(context: SkillContext, inputs: dict[str, object]) -> SkillResult

`context` is the only capability; these methods are the complete surface:

- `await context.observe()` returns the latest public `Observation`. It is free:
  `context.observe()` charges no primitive action and is not the `observe`
  primitive tool. `remaining_budget()` and `log()` are free too.
  - `observation.sequence` (int), `observation.terminal` (bool)
  - `observation.status` (dict of public values such as health)
  - `observation.visible_objects`: a tuple of `VisibleObject`, each with
    `object_id`, `label`, `position` (x, y, z or None), `distance` (float or None),
    and `properties` (dict, for example a screen offset)
  - `observation.messages`: a tuple of messages, each with `kind` and `text`
- `await context.call(tool_name, **arguments)` runs one listed primitive and
  returns a `StepResult`:
  - `result.status`: "succeeded", "rejected", "failed", or "unknown"
  - `result.code` and `result.message`: public text
  - `result.observation`: the `Observation` after the action
  - `result.primitive_actions_charged` (int)
  Arguments are keywords, for example `await context.call("tool_name", amount=3)`.
  Treat "unknown" as inconclusive and stop; never retry it.
- `context.remaining_budget()` returns a `SkillBudget` with `primitive_actions`
  and `wall_time_seconds`. Stop before `primitive_actions` reaches zero.
- `context.log(event, fields)` records one short public event.

Return exactly one `SkillResult(status=..., summary=..., evidence=...,
outputs=..., primitive_actions_used=...)`:
- `status`: "succeeded", "failed", or "inconclusive"
- `summary`: one sentence
- `evidence`: a tuple of `EvidenceRef(kind=..., value=...)`, where `kind` is
  "observation_sequence", "action_id", "object_id", or "message" and `value` is a
  non-empty string from this invocation's own results. A "succeeded" status must
  cite at least one.
- `outputs`: a JSON-safe dict (optional)
- `primitive_actions_used`: `primitive_actions_used` must equal the sum of
  `result.primitive_actions_charged` over this skill's own `context.call` results;
  never count actions by hand

Validation runs your skill against the recorded public trace, then against a
missing target, a failed primitive, an unknown primitive, invalid inputs, and an
exhausted budget, three times each, and against a copy with renamed object IDs.
It must return a well-formed result every time and report failure honestly."""

WORKED_EXAMPLE_SOURCE = '''\
from noob_agent.skills.contract import EvidenceRef, SkillContext, SkillResult


async def run(context: SkillContext, inputs: dict[str, object]) -> SkillResult:
    """Press the visible switch until its light property reads on."""
    used = 0
    if inputs:
        return SkillResult(status="failed", summary="Takes no inputs.",
                           primitive_actions_used=used)
    observation = await context.observe()
    while context.remaining_budget().primitive_actions > 0:
        switches = [o for o in observation.visible_objects if o.label == "switch"]
        if not switches:
            return SkillResult(status="failed", summary="No switch visible.",
                               primitive_actions_used=used)
        if switches[0].properties.get("light") == "on":
            proof = (EvidenceRef(kind="observation_sequence", value=str(observation.sequence)),)
            return SkillResult(status="succeeded", summary="Light is on.", evidence=proof,
                               primitive_actions_used=used)
        result = await context.call("press_switch", object_id=switches[0].object_id)
        used += result.primitive_actions_charged
        if result.status != "succeeded":
            status = "inconclusive" if result.status == "unknown" else "failed"
            return SkillResult(status=status, summary=result.code,
                               primitive_actions_used=used)
        observation = result.observation
    return SkillResult(status="failed", summary="Budget spent; light still off.",
                       primitive_actions_used=used)
'''

WORKED_EXAMPLE_METADATA: dict[str, object] = {
    "name": "turn_on_switch_light",
    "version": 1,
    "parent_version": None,
    "purpose": "Press the visible switch until its light property turns on.",
    "input_schema": {"properties": {}},
    "required_tools": ["press_switch"],
    "max_primitive_actions": 4,
    "max_wall_time_seconds": 10,
    "success_claim": "The switch's visible light property reads on.",
    "api_version": API_VERSION,
}

BUILDER_SYSTEM = f"""You write one small Python skill for a game-playing agent. You see only what \
an ordinary player could see. Write a skill that is useful beyond the single \
attempt you were shown: it must declare its inputs, call only the listed \
primitive tools, check a visible success condition, and return a clear failure \
rather than guessing. You may not change the connector, the budgets, the \
scenario, or the rules by which your skill is accepted.

{SKILL_API_REFERENCE}

A worked example for a fictional game with a `press_switch` primitive. It shows the \
API and the shape of a good skill; it is not a solution for any game you will see.

```python
{WORKED_EXAMPLE_SOURCE.strip()}
```

```json
{json.dumps(WORKED_EXAMPLE_METADATA)}
```"""

_REPLY_FORMAT = f"""Reply with exactly two fenced blocks and nothing that contradicts them.

First, the skill source:

```python
from noob_agent.skills.contract import EvidenceRef, SkillContext, SkillResult


async def run(context: SkillContext, inputs: dict[str, object]) -> SkillResult:
    ...
```

Then its metadata:

```json
{{"name": "lower_snake_case", "version": 1, "parent_version": null,
  "purpose": "...", "input_schema": {{"properties": {{}}}},
  "required_tools": ["..."], "max_primitive_actions": 8,
  "max_wall_time_seconds": 30, "success_claim": "...",
  "api_version": "{API_VERSION}"}}
```

You may import only `math`, `statistics`, `dataclasses`, `typing`, `collections`,
`itertools`, `functools`, and `enum`, plus `EvidenceRef`, `SkillBudget`,
`SkillContext`, and `SkillResult` from `noob_agent.skills.contract`. Every other
import, and any file, environment, process, network, or dynamic-execution access,
is refused. `max_primitive_actions` is at most 8 and `max_wall_time_seconds` at
most 30."""


def _render_observation(observation: EvidenceObservation) -> str:
    objects = ", ".join(
        f"{item.label} ({item.object_id}) {json.dumps(item.properties, sort_keys=True)}"
        for item in observation.visible_objects
    )
    return (
        f"status {json.dumps(observation.status, sort_keys=True)}; "
        f"visible: {objects or 'nothing'}"
        f"{'; terminal' if observation.terminal else ''}"
    )


def _render_tools(tools: Sequence[ToolDefinition], primitive_names: Iterable[str]) -> str:
    if not tools:
        return ", ".join(sorted(primitive_names))
    return "\n".join(
        f"- {tool.name}: {tool.description} Arguments: "
        f"{json.dumps(tool.argument_schema, sort_keys=True)}"
        for tool in tools
    )


def _render_steps(evidence: TraceEvidence) -> str:
    if not evidence.steps:
        return "No actions were recorded before the attempt ended."
    lines = []
    for step in evidence.steps:
        detail = f"{step.status}/{step.code}"
        if step.message:
            detail += f" - {step.message}"
        lines.append(
            f"{step.sequence}. {step.tool_name}({json.dumps(step.arguments, sort_keys=True)})"
            f" -> {detail} [charged {step.primitive_actions_charged}]"
        )
        if step.observation is not None:
            lines.append(f"   after: {_render_observation(step.observation)}")
    return "\n".join(lines)


def render_builder_prompt(
    evidence: TraceEvidence,
    *,
    primitive_names: Iterable[str],
    tools: Sequence[ToolDefinition] = (),
) -> str:
    """Ask for one candidate skill from a bounded public trace."""
    outcome = evidence.stop_reason or "not recorded"
    return f"""A model attempted this task using only primitive tools, and the attempt ended.

Game: {evidence.game_id}
Scenario: {evidence.scenario_id}
Public goal: {evidence.public_goal}

Primitive tools:
{_render_tools(tools, primitive_names)}

Public state at the start: {_render_observation(evidence.reset_observation)}

What happened, in recorded order (with the public state after each action):

{_render_steps(evidence)}

The attempt stopped because: {outcome}
Primitive actions used: {evidence.total_primitives}

Name the capability that was missing and write one skill that supplies it.

{_REPLY_FORMAT}"""


MAX_REPAIR_ISSUES = 3
MAX_ISSUE_EVIDENCE_CHARS = 300


def _issue_lines(issues: Iterable[SkillValidationIssue]) -> list[str]:
    listed = []
    for issue in list(issues)[:MAX_REPAIR_ISSUES]:
        line = f"- [{issue.check}/{issue.code}] {issue.message}"
        evidence = {
            key: value
            for key, value in {
                "fixture": issue.fixture,
                "public_inputs": issue.public_inputs,
                "public_result": issue.public_result,
                "public_logs": issue.public_logs or None,
            }.items()
            if value is not None
        }
        if evidence:
            text = json.dumps(evidence, sort_keys=True, default=str)
            if len(text) > MAX_ISSUE_EVIDENCE_CHARS:
                text = text[: MAX_ISSUE_EVIDENCE_CHARS - 3] + "..."
            line += f"\n  Public failure evidence: {text}"
        listed.append(line)
    return listed


def render_repair_prompt(
    *,
    previous_source: str,
    issues: Iterable[SkillValidationIssue],
    evidence: TraceEvidence | None = None,
    primitive_names: Iterable[str] = (),
    tools: Sequence[ToolDefinition] = (),
    skill_name: str | None = None,
) -> str:
    """Ask for one repair, given only public validation errors and public evidence.

    The rejected candidate receives its own failing checks, the primitive
    definitions, and the same bounded public trace the build saw, and nothing
    else: no private grader predicate and no held-out data ever reaches a
    repair. The source and failing checks are always included; the primitive
    definitions and then the trace are added only while the whole prompt, with
    the system prompt, stays within `PROMPT_TOKEN_LIMIT`.
    """
    listed = _issue_lines(issues)
    if not listed:
        raise ValueError("A repair prompt needs at least one validation issue.")
    keep_name = (
        f"Keep the skill name `{skill_name}` in the metadata; a repair may not rename it.\n\n"
        if skill_name
        else ""
    )
    required = f"""Your candidate was rejected by automated validation.

Failing checks:

{chr(10).join(listed)}

The rejected source was:

```python
{previous_source.strip()}
```

Fix exactly these problems and return the whole skill again. Do not work around a
check, and do not change what the skill claims to do in order to pass.
{keep_name}
{_REPLY_FORMAT}"""
    optional: list[str] = []
    names = tuple(primitive_names)
    if tools or names:
        optional.append(f"Primitive tools:\n{_render_tools(tools, names)}")
    if evidence is not None:
        optional.append(
            f"Public state at the start: {_render_observation(evidence.reset_observation)}\n\n"
            "What happened in the attempt the skill was written from, with the public state "
            f"after each action:\n\n{_render_steps(evidence)}"
        )
    budget = PROMPT_TOKEN_LIMIT - estimated_tokens(BUILDER_SYSTEM)
    context: list[str] = []
    for section in optional:
        candidate = "\n\n".join([*context, section, required])
        if estimated_tokens(candidate) <= budget:
            context.append(section)
    return "\n\n".join([*context, required])
