"""The Action agent's prompt: public goal, public state, ordinary controls.

This is the evaluation prompt `hackathon_plan.md` section 7.3 holds fixed
across compared models, as revised by section 22: the fixed instructions live
in the system prompt and the per-turn state in the user prompt. It carries the
public task, the current observation, a bounded recent history, the primitive
tools, and any accepted skill's name, purpose, input schema, and result shape.
It never carries training traces,
Builder discussion, validation fixtures, grader state, or clean/faulty identity.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from noob_agent.domain.model import Observation, ToolDefinition
from noob_agent.domain.skills import SkillVersion

_ROLE = (
    "You are a new player in an unfamiliar game, controlling a character through a "
    "small set of ordinary controls. Each turn you choose exactly one action: one "
    "primitive tool, or one learned skill if any is listed. Use only what you can see. "
    "The purpose of unfamiliar objects is not documented, so treat visible results as "
    "evidence. Before acting, state your current subgoal and the evidence you expect "
    "the action to produce, so that a repeated guess is visible as one."
)

_REPLY_FORMAT = """Reply with exactly one JSON object and nothing else:

{"subgoal": "...", "expected_evidence": "...", "action": "<primitive or skill name>",
 "arguments": {...}, "finding": null}

"subgoal" is your current subgoal and "expected_evidence" the visible evidence you
expect the action to produce. "action" is one primitive or learned skill exactly as
listed, and "arguments" holds that action's arguments. Do not invent controls.

If a visible result contradicts evidence you established earlier in this attempt,
replace null with a "finding" object describing expected and observed behavior
separately, with counts and references to public records from this attempt:

"finding": {"expected_behavior": "...", "expected_basis": "...", "actual_behavior": "...",
            "expected_count": <int>, "actual_count": <int>,
            "evidence": [{"kind": "<action_id | observation_sequence | object_id | message>",
                          "value": "..."}]}

Add a finding only when you have that evidence; a finding without it is not counted."""

# Fixed for every turn and episode, so a provider can reuse its prefix.
ACTION_SYSTEM = f"{_ROLE}\n\n{_REPLY_FORMAT}"

SKILL_RESULT_SHAPE = (
    "Returns status (succeeded, failed, or inconclusive), a summary, public "
    "evidence references, outputs, and the number of primitive actions it used. "
    "Its primitive actions are charged to your budget."
)


class HistoryEntry(BaseModel):
    """One earlier decision in this attempt and what came of it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: str = Field(min_length=1)
    subgoal: str
    chosen: str = Field(min_length=1)
    outcome: str = Field(min_length=1)


def _argument_hint(schema: Mapping[str, JsonValue]) -> str:
    """A compact signature, such as `degrees: integer 1-90, ticks?: integer`."""
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return ""
    required = schema.get("required")
    needed = set(required) if isinstance(required, list) else set()
    parts = []
    for name, spec in properties.items():
        hint = f"{name}{'' if name in needed else '?'}"
        if isinstance(spec, dict):
            kind = spec.get("type")
            if isinstance(kind, str):
                hint += f": {kind}"
            low, high = spec.get("minimum"), spec.get("maximum")
            if low is not None and high is not None:
                hint += f" {low}-{high}"
        parts.append(hint)
    return ", ".join(parts)


def _render_tools(tools: Sequence[ToolDefinition]) -> str:
    lines = []
    for tool in tools:
        preconditions = (
            f" Preconditions: {'; '.join(tool.preconditions)}" if tool.preconditions else ""
        )
        lines.append(
            f"- {tool.name}({_argument_hint(tool.argument_schema)}): "
            f"{tool.description}{preconditions}"
        )
    return "\n".join(lines)


def _render_skills(skills: Sequence[SkillVersion]) -> str:
    if not skills:
        return "No learned skills are available in this attempt."
    lines = []
    for skill in skills:
        metadata = skill.package.metadata
        purpose = metadata.get("purpose", "")
        input_schema = metadata.get("input_schema", {})
        lines.append(
            f"- {skill.name}: {purpose} Inputs: {json.dumps(input_schema, sort_keys=True)}. "
            f"{SKILL_RESULT_SHAPE}"
        )
    return "\n".join(lines)


def _render_history(history: Sequence[HistoryEntry]) -> str:
    if not history:
        return "History: no earlier decisions in this attempt."
    lines = ["History (most recent last):"]
    for entry in history:
        lines.append(
            f"- {entry.action_id}: subgoal {entry.subgoal!r}; chose {entry.chosen}; "
            f"outcome {entry.outcome}"
        )
    return "\n".join(lines)


def render_action_prompt(
    *,
    public_goal: str,
    observation: Observation,
    tools: Sequence[ToolDefinition],
    skills: Sequence[SkillVersion],
    history: Sequence[HistoryEntry],
) -> str:
    """Render one decision's prompt from public information only."""
    # The goal is already the prompt's first line.
    snapshot = observation.model_dump(mode="json", exclude={"public_goal"})
    return f"""Task: {public_goal}

Primitive tools:
{_render_tools(tools)}

Learned skills:
{_render_skills(skills)}

{_render_history(history)}

Current observation (sequence {observation.sequence}):
{json.dumps(snapshot, sort_keys=True)}

Choose exactly one action for this turn."""
