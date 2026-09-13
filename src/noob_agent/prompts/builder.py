"""The Builder's prompts: evidence in, one skill candidate out.

These prompts describe ordinary controls and a reply format. They never carry
grader predicates, held-out data, clean/faulty identity, or the answer to an
unfamiliar mechanic, and they never invite the model to change the connector,
the budgets, the scenario, or its own acceptance rules.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from noob_agent.agents.evidence import TraceEvidence
from noob_agent.skills.errors import SkillValidationIssue
from noob_agent.skills.metadata import API_VERSION

BUILDER_SYSTEM = (
    "You write one small Python skill for a game-playing agent. You see only what "
    "an ordinary player could see. Write a skill that is useful beyond the single "
    "attempt you were shown: it must declare its inputs, call only the listed "
    "primitive tools, check a visible success condition, and return a clear failure "
    "rather than guessing. You may not change the connector, the budgets, the "
    "scenario, or the rules by which your skill is accepted."
)

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
is refused."""


def _render_steps(evidence: TraceEvidence) -> str:
    if not evidence.steps:
        return "No actions were recorded before the attempt ended."
    lines = []
    for step in evidence.steps:
        detail = f"{step.status}/{step.code}"
        if step.message:
            detail += f" - {step.message}"
        lines.append(
            f"{step.sequence}. {step.tool_name}({step.arguments}) -> {detail} "
            f"[charged {step.primitive_actions_charged}]"
        )
    return "\n".join(lines)


def render_builder_prompt(evidence: TraceEvidence, *, primitive_names: Iterable[str]) -> str:
    """Ask for one candidate skill from a bounded public trace."""
    names = ", ".join(sorted(primitive_names))
    outcome = evidence.stop_reason or "not recorded"
    return f"""A model attempted this task using only primitive tools, and the attempt ended.

Game: {evidence.game_id}
Scenario: {evidence.scenario_id}
Public goal: {evidence.public_goal}
Primitive tools available: {names}

What happened, in recorded order:

{_render_steps(evidence)}

The attempt stopped because: {outcome}
Primitive actions used: {evidence.total_primitives}

Name the capability that was missing and write one skill that supplies it.

{_REPLY_FORMAT}"""


def render_repair_prompt(*, previous_source: str, issues: Iterable[SkillValidationIssue]) -> str:
    """Ask for one repair, given only the public validation errors.

    The rejected candidate receives its own failing checks and nothing else: no
    private grader predicate and no held-out data ever reaches a repair.
    """
    listed = []
    for issue in issues:
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
            line += f"\n  Public failure evidence: {json.dumps(evidence, sort_keys=True)}"
        listed.append(line)
    if not listed:
        raise ValueError("A repair prompt needs at least one validation issue.")
    return f"""Your candidate was rejected by automated validation.

Failing checks:

{chr(10).join(listed)}

The rejected source was:

```python
{previous_source.strip()}
```

Fix exactly these problems and return the whole skill again. Do not work around a
check, and do not change what the skill claims to do in order to pass.

{_REPLY_FORMAT}"""
