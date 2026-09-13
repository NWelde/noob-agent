"""Bounded public-trace selection for the Builder.

The Builder sees a small, fixed selection of what a player could have seen:
observations, actions, errors, and the outcome. It never sees private grader
state, held-out configuration, clean/faulty identity, or a scenario answer —
none of which the durable records carry in the first place, which is what makes
this selection safe rather than merely careful.

Selection is bounded because a whole episode is more evidence than a prompt
should carry, and it is deterministic so the same episode always yields the
same prompt.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from noob_agent.domain.records import StopReason, StoredEpisode

DEFAULT_MAX_STEPS = 8


class EvidenceStep(BaseModel):
    """One recorded request and what came back, reduced to what a Builder needs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int = Field(ge=1)
    tool_name: str = Field(min_length=1)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    status: str = Field(min_length=1)
    code: str = Field(min_length=1)
    message: str
    state_changed: bool | None
    primitive_actions_charged: int = Field(ge=0)


class TraceEvidence(BaseModel):
    """The bounded public selection handed to the Builder."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    episode_id: str = Field(min_length=1)
    game_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    public_goal: str = Field(min_length=1)
    primitive_names: tuple[str, ...] = Field(min_length=1)
    steps: tuple[EvidenceStep, ...] = ()
    stop_reason: StopReason | None = None
    terminal: bool = False
    total_primitives: int = Field(default=0, ge=0)


def select_evidence(stored: StoredEpisode, *, max_steps: int = DEFAULT_MAX_STEPS) -> TraceEvidence:
    """Reduce one recorded episode to a bounded, deterministic evidence set.

    When the episode is longer than `max_steps`, the steps that did not simply
    succeed are kept first: a rejection or failure is what tells the Builder
    where the capability gap is, while a run of successes mostly restates the
    manifest. Ties keep the later step, which is the one closest to where the
    attempt stopped.
    """
    if max_steps < 1:
        raise ValueError("max_steps must be at least 1.")

    ranked = sorted(
        stored.steps,
        key=lambda step: (step.result.status == "succeeded", -step.sequence),
    )
    kept = sorted(ranked[:max_steps], key=lambda step: step.sequence)

    return TraceEvidence(
        episode_id=stored.episode.episode_id,
        game_id=stored.episode.game_id,
        scenario_id=stored.episode.scenario_id,
        public_goal=stored.episode.reset_observation.public_goal,
        primitive_names=tuple(tool.name for tool in stored.episode.manifest.tools),
        steps=tuple(
            EvidenceStep(
                sequence=step.sequence,
                tool_name=step.request.tool_name,
                arguments=step.request.arguments,
                status=step.result.status,
                code=step.result.code,
                message=step.result.message,
                state_changed=step.result.state_changed,
                primitive_actions_charged=step.result.primitive_actions_charged,
            )
            for step in kept
        ),
        stop_reason=None if stored.outcome is None else stored.outcome.stop_reason,
        terminal=False if stored.outcome is None else stored.outcome.terminal,
        total_primitives=0 if stored.outcome is None else stored.outcome.total_primitives,
    )
