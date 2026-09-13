"""The Action agent's prompt: public goal, public state, ordinary controls.

This is the evaluation prompt `hackathon_plan.md` section 7.3 holds fixed
across compared models. It carries the public task, the current observation,
a bounded recent history, the primitive tools, and any accepted skill's name,
purpose, input schema, and result shape. It never carries training traces,
Builder discussion, validation fixtures, grader state, or clean/faulty identity.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from noob_agent.domain.model import Observation, ToolDefinition
from noob_agent.domain.skills import SkillVersion

ACTION_SYSTEM = (
    "You are a new player in an unfamiliar game, controlling a character through a "
    "small set of ordinary controls. Each turn you choose exactly one action: one "
    "primitive tool, or one learned skill if any is listed. Use only what you can see. "
    "The purpose of unfamiliar objects is not documented, so treat visible results as "
    "evidence. Before acting, state your current subgoal and the evidence you expect "
    "the action to produce, so that a repeated guess is visible as one. Reply with a "
    "single JSON object and nothing else."
)

_REPLY_FORMAT = """Reply with exactly one JSON object in one of these two shapes:

{"subgoal": "...", "expected_evidence": "...", "tool": "<primitive name>", "arguments": {...}}

{"subgoal": "...", "expected_evidence": "...", "skill": "<skill name>", "inputs": {...}}

Use a tool name or skill name exactly as listed. Do not invent controls."""

_SKILL_RESULT_SHAPE = (
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


def _render_tools(tools: Sequence[ToolDefinition]) -> str:
    lines = []
    for tool in tools:
        preconditions = (
            f" Preconditions: {'; '.join(tool.preconditions)}" if tool.preconditions else ""
        )
        lines.append(
            f"- {tool.name}: {tool.description} Arguments: "
            f"{json.dumps(tool.argument_schema, sort_keys=True)}.{preconditions}"
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
            f"{_SKILL_RESULT_SHAPE}"
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
    snapshot = observation.model_dump(mode="json")
    return f"""Task: {public_goal}

Primitive tools:
{_render_tools(tools)}

Learned skills:
{_render_skills(skills)}

{_render_history(history)}

Current observation (sequence {observation.sequence}):
{json.dumps(snapshot, sort_keys=True)}

Choose exactly one action for this turn.

{_REPLY_FORMAT}"""
