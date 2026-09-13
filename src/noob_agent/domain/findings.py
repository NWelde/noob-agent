"""Finding and Reproduction records from `hackathon_plan.md` section 9.

A finding is what the model reported: expected behavior and its public basis,
actual behavior, counts, and public evidence references. It is a public record
and, like every other public record, forbids undeclared fields, so a build
label cannot be smuggled onto it.

A verdict and a reproduction are private grader output. They are stored beside
the finding but never enter a prompt or a public record.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from noob_agent.skills.contract import EvidenceRef

ScenarioBuildVariant = Literal["clean", "faulty"]
Verification = Literal["confirmed", "rejected"]
ReproductionResult = Literal["reproduced", "not_reproduced", "infrastructure_failure"]


class FindingModel(BaseModel):
    """Immutable, JSON-safe base for every finding-related record."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class FindingReport(FindingModel):
    """A candidate defect as the reporting model described it.

    `expected_basis` is the public reason the model expected what it expected,
    such as an earlier result in the same attempt. A report with no counts or
    no evidence is recorded as reported, but the grader will not verify it.
    """

    expected_behavior: str = Field(min_length=1)
    expected_basis: str = Field(min_length=1)
    actual_behavior: str = Field(min_length=1)
    expected_count: int | None = Field(default=None, ge=0)
    actual_count: int | None = Field(default=None, ge=0)
    evidence: tuple[EvidenceRef, ...] = ()


def parse_finding_report(payload: object) -> FindingReport | None:
    """Read a finding out of a reply's `finding` object, or `None` if it is not one."""
    if not isinstance(payload, dict):
        return None
    try:
        return FindingReport.model_validate(payload)
    except ValidationError:
        return None


class FindingRecord(FindingModel):
    """One reported finding, pinned to the episode and decision it came from."""

    finding_id: str = Field(min_length=1)
    episode_id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    reporting_model_id: str = Field(min_length=1)
    report: FindingReport
    reported_at: datetime


class FindingVerdict(FindingModel):
    """The private grader's written-once decision about one finding."""

    finding_id: str = Field(min_length=1)
    verification: Verification
    reason_code: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    grader_version: str = Field(min_length=1)
    decided_at: datetime


class ReplayStep(FindingModel):
    """One ordinary primitive call in the minimal evidence sequence."""

    tool_name: str = Field(min_length=1)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class ReplayOutcome(FindingModel):
    """One replayed primitive: what was recorded originally and what happened now."""

    tool_name: str = Field(min_length=1)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    recorded_status: str = Field(min_length=1)
    observed_status: str = Field(min_length=1)
    observed_code: str = Field(min_length=1)


class ReproductionRecord(FindingModel):
    """One fresh-reset reproduction attempt and its private predicate result."""

    reproduction_id: str = Field(min_length=1)
    finding_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    seed: int
    build: ScenarioBuildVariant
    attempted: tuple[ReplayOutcome, ...] = ()
    result: ReproductionResult
    predicate_result: bool | None = None
    first_mismatch: str | None = None
    attempted_at: datetime


class StoredFinding(FindingModel):
    """A finding read back with whatever the grader has recorded about it."""

    finding: FindingRecord
    verdict: FindingVerdict | None = None
    reproductions: tuple[ReproductionRecord, ...] = ()
