"""The Builder's refinement prompt: an accepted skill and its public practice results.

Section 22 step 22.D of `hackathon_plan.md`. A refinement sees the incumbent
skill's source, the public outcome of each practice attempt that used it (how
the episode ended, the skill's own reported results, and the recorded steps with
the public state after each), and the public practice score. It never sees a
private grade, a seed, or anything from a held-out scenario.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from noob_agent.agents.evidence import TraceEvidence
from noob_agent.domain.model import ToolDefinition
from noob_agent.domain.skills import SkillVersion
from noob_agent.prompts.builder import (
    _REPLY_FORMAT,
    BUILDER_SYSTEM,
    PROMPT_TOKEN_LIMIT,
    _render_observation,
    _render_steps,
    _render_tools,
    estimated_tokens,
)


@dataclass(frozen=True)
class PracticeAttempt:
    """One practice episode as a player could describe it."""

    stop_reason: str
    decisions: int
    primitives: int
    skill_results: tuple[str, ...]
    evidence: TraceEvidence


def render_refine_prompt(
    *,
    incumbent: SkillVersion,
    attempts: Sequence[PracticeAttempt],
    primitive_names: Iterable[str],
    tools: Sequence[ToolDefinition],
) -> str:
    """Ask for one improved version of an accepted skill, bounded like a build prompt."""
    total = len(attempts)
    ended = sum(1 for attempt in attempts if attempt.stop_reason == "terminal_state")
    primitives = sum(attempt.primitives for attempt in attempts)
    required = f"""Your skill `{incumbent.name}` (version {incumbent.version}) was accepted and then
played in {total} practice attempts of the same task from new starting positions,
with a fresh player who could call it.

Public practice score: {ended} of {total} practice attempts reached a terminal state,
using {primitives} primitive actions in total. A higher count of attempts reaching a
terminal state wins; with an equal count, fewer primitive actions win.

The current source:

```python
{incumbent.package.source.strip()}
```

Write one improved version of this skill that makes more practice attempts reach
the end of the task, or reaches it with fewer primitive actions. Keep the skill
name `{incumbent.name}` in the metadata. The new version must still pass every
automated validation check, and it is kept only if its practice score is better.

{_REPLY_FORMAT}"""

    names = tuple(primitive_names)
    sections = [f"Primitive tools:\n{_render_tools(tools, names)}"]
    for number, attempt in enumerate(attempts, start=1):
        uses = "\n".join(f"- {result}" for result in attempt.skill_results) or "- (not used)"
        start = _render_observation(attempt.evidence.reset_observation)
        sections.append(
            f"Practice attempt {number}: ended with {attempt.stop_reason} after "
            f"{attempt.decisions} decisions and {attempt.primitives} primitive actions.\n"
            f"Skill results:\n{uses}\n"
            f"Public state at the start: {start}\n"
            f"Recorded steps, with the public state after each:\n{_render_steps(attempt.evidence)}"
        )

    budget = PROMPT_TOKEN_LIMIT - estimated_tokens(BUILDER_SYSTEM)
    kept: list[str] = []
    for section in sections:
        candidate = "\n\n".join([*kept, section, required])
        if estimated_tokens(candidate) <= budget:
            kept.append(section)
    return "\n\n".join([*kept, required])
