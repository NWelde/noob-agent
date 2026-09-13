"""The only capabilities generated skills receive and the result they return."""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from noob_agent.domain.model import Observation, StepResult

EvidenceKind = Literal["observation_sequence", "action_id", "object_id", "message"]
SkillStatusClaim = Literal["succeeded", "failed", "inconclusive"]


class SkillContractModel(BaseModel):
    """Immutable, JSON-safe base for values crossing the skill boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceRef(SkillContractModel):
    """One public record reference supporting a skill result."""

    kind: EvidenceKind
    value: str = Field(min_length=1)


class SkillBudget(SkillContractModel):
    """The stricter remaining limits available to one skill invocation."""

    primitive_actions: int = Field(ge=0)
    wall_time_seconds: float = Field(ge=0)


class SkillResult(SkillContractModel):
    """The durable result of one bounded skill invocation."""

    status: SkillStatusClaim
    summary: str = Field(min_length=1)
    evidence: tuple[EvidenceRef, ...] = ()
    outputs: dict[str, JsonValue] = Field(default_factory=dict)
    primitive_actions_used: int = Field(ge=0)

    @model_validator(mode="after")
    def success_requires_evidence(self) -> SkillResult:
        """Refuse a success claim that an independent reader cannot check."""
        if self.status == "succeeded" and not self.evidence:
            raise ValueError("A succeeded skill result must cite public evidence.")
        return self


@runtime_checkable
class SkillContext(Protocol):
    """The complete capability surface exposed to generated code."""

    async def observe(self) -> Observation:
        """Return the latest complete public snapshot."""
        ...

    async def call(self, tool_name: str, **arguments: object) -> StepResult:
        """Call one primitive declared by the episode connector manifest."""
        ...

    def remaining_budget(self) -> SkillBudget:
        """Return the currently available primitive and wall-time budget."""
        ...

    def log(self, event: str, fields: dict[str, JsonValue]) -> None:
        """Record one bounded public skill event."""
        ...
