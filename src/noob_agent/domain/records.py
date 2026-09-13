"""Durable local records written to the SQLite source of truth.

These records wrap the public connector models in `model.py` with the episode
bookkeeping the harness needs to replay an attempt later. They deliberately
carry no private grader state: no clean/faulty identity, no scenario answer, no
held-out configuration, and no credential. Like the public models they forbid
undeclared fields, so such a value cannot be smuggled in through an extra key.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from noob_agent.domain.model import (
    ConnectorManifest,
    Observation,
    StepResult,
    ToolRequest,
)

EpisodeSplit = Literal["training", "validation", "held-out"]

# The episode-ending conditions named in connector_contract.md.
StopReason = Literal[
    "goal_completed",
    "terminal_state",
    "decision_limit",
    "primitive_limit",
    "wall_time_limit",
    "unknown_result",
    "connector_lost",
    "repeated_failure",
    "no_progress",
]


class DurableRecord(BaseModel):
    """Immutable, JSON-safe base for every locally persisted record."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ExperimentRecord(DurableRecord):
    """One comparison configuration and its frozen budgets."""

    experiment_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    condition: str = Field(min_length=1)
    connector_version: str = Field(min_length=1)
    decision_budget: int = Field(gt=0)
    primitive_budget: int = Field(gt=0)
    wall_time_budget_ms: int = Field(gt=0)
    created_at: datetime


class EpisodeRecord(DurableRecord):
    """A single attempt, pinned to the manifest and reset state it began from.

    `scenario_id` is the public family ID. The clean-or-faulty identity of a
    scenario is private grader state and is never recorded here.
    """

    episode_id: str = Field(min_length=1)
    experiment_id: str = Field(min_length=1)
    game_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    seed: int
    split: EpisodeSplit
    manifest: ConnectorManifest
    reset_observation: Observation
    started_at: datetime

    @model_validator(mode="after")
    def reset_observation_starts_the_episode(self) -> Self:
        """The recorded reset must be this episode's sequence-0 snapshot."""
        if self.reset_observation.episode_id != self.episode_id:
            raise ValueError("The reset observation belongs to a different episode.")
        if self.reset_observation.sequence != 0:
            raise ValueError("A reset observation must carry sequence 0.")
        return self


class StepRecord(DurableRecord):
    """One durable request/result pair, exactly as it was delivered."""

    episode_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    request: ToolRequest
    result: StepResult

    @model_validator(mode="after")
    def result_matches_its_request(self) -> Self:
        """Every request receives one result carrying the same action ID."""
        if self.request.action_id != self.result.action_id:
            raise ValueError("The result's action_id does not match the request's.")
        if self.result.sequence != self.sequence:
            raise ValueError("The result's sequence does not match the step's.")
        if self.result.observation.episode_id != self.episode_id:
            raise ValueError("The result's observation belongs to a different episode.")
        return self


class EpisodeOutcome(DurableRecord):
    """The final, written-once result of one episode."""

    episode_id: str = Field(min_length=1)
    stop_reason: StopReason
    terminal: bool
    total_decisions: int = Field(ge=0)
    total_primitives: int = Field(ge=0)
    finished_at: datetime


ModelCallPurpose = Literal["action", "build", "repair", "refine"]


class SequenceSummaryRecord(DurableRecord):
    """One harness-only summary of a completed learning sequence.

    This is diagnostic output, never prompt evidence.  It carries compact
    grader results but no private scenario configuration or predicate state.
    """

    sequence_id: str = Field(min_length=1)
    run_kind: str = Field(min_length=1)
    training_episode_id: str = Field(min_length=1)
    training_goal_completed: bool
    builder_stop_reason: str = Field(min_length=1)
    builder_truncated: bool
    accepted_skill_name: str | None = Field(default=None, min_length=1)
    accepted_skill_version: int | None = Field(default=None, ge=1)
    heldout_total: int = Field(ge=0)
    heldout_completed: int = Field(ge=0)
    heldout_skipped_reason: str | None = None
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    finished_at: datetime

    @model_validator(mode="after")
    def accepted_skill_is_complete(self) -> Self:
        if (self.accepted_skill_name is None) != (self.accepted_skill_version is None):
            raise ValueError("An accepted skill needs both name and version.")
        if self.heldout_completed > self.heldout_total:
            raise ValueError("Held-out completions cannot exceed held-out cells.")
        return self


class ModelCallRecord(DurableRecord):
    """One model request and what came back, written after the call returns or fails.

    The episode is the acting episode for an Action call and the authoring
    episode for a Builder call. Token counts are `None` when the provider did
    not report them, never zero. There is no field for a credential. A record
    is diagnostic only: it never enters a prompt, an observation, evidence
    selection, or a grade.
    """

    call_id: str = Field(min_length=1)
    experiment_id: str = Field(min_length=1)
    purpose: ModelCallPurpose
    episode_id: str | None = Field(default=None, min_length=1)
    # The decision an Action reply produced; absent for Builder calls and failures.
    action_id: str | None = Field(default=None, min_length=1)
    provider: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    system: str
    prompt: str
    max_output_tokens: int = Field(gt=0)
    temperature: float
    response_text: str | None = None
    reasoning: str | None = None
    finish_reason: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    latency_ms: int = Field(ge=0)
    error: str | None = None
    started_at: datetime
    # Thinking and the reply schema the request asked for; absent before schema 5.
    request_options: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def links_and_outcome_agree(self) -> Self:
        """A call either returned a reply or failed, and only Action calls act."""
        if (self.response_text is None) == (self.error is None):
            raise ValueError("A model call has exactly one of a response or an error.")
        if self.purpose == "action" and self.episode_id is None:
            raise ValueError("An Action call must name the episode it acted in.")
        if self.action_id is not None and (self.purpose != "action" or self.error is not None):
            raise ValueError("Only a returned Action call produces an action_id.")
        return self


class StoredEpisode(DurableRecord):
    """An episode read back from the store, with its steps in sequence."""

    episode: EpisodeRecord
    steps: tuple[StepRecord, ...] = ()
    outcome: EpisodeOutcome | None = None
