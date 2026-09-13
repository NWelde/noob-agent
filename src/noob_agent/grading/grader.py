"""The independent grader for the Resonator scenario family.

The grader reads scenario-controlled private state that never appears in a
prompt or a public observation, and it decides only after an attempt. Its
rules come from `minecraft_scenario.md`:

- Progress is the number of ordered predicates that hold, 0 to 5.
- A defect is verified only when the report carries a relevant expected count,
  an actual count, and evidence references that resolve to this episode's
  public records; the fault predicate holds in the reported episode; and the
  matched clean build does not show the behavior under the same sequence.

The model's statement that it found a bug is never sufficient by itself, and
a report is rejected the same way however confidently it was written.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from noob_agent.domain.findings import (
    FindingRecord,
    FindingVerdict,
    ScenarioBuildVariant,
)
from noob_agent.domain.records import StoredEpisode
from noob_agent.skills.contract import EvidenceRef

GRADER_VERSION = "resonator-grader-0.1.0"

# The clean rule produces one Charged Key; the planted fault produces two.
CLEAN_OUTPUT_COUNT = 1
FAULT_OUTPUT_COUNT = 2

# Ordered progress predicates; the score counts how many hold from the start.
PROGRESS_PREDICATES: tuple[str, ...] = (
    "found_device",
    "supplied_first_input",
    "started_process",
    "created_key",
    "goal_completed",
)

_IDENTITY_WORDS = ("clean", "faulty")


class GraderModel(BaseModel):
    """Immutable base for grader-owned values."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ScenarioBuild(GraderModel):
    """One concrete world build behind a public scenario ID.

    The scenario ID is what the model sees. It identifies the layout and must
    not identify the build, so an ID that names either identity is refused.
    """

    scenario_id: str = Field(min_length=1)
    build: ScenarioBuildVariant
    fingerprint: str = Field(min_length=1)

    @field_validator("scenario_id")
    @classmethod
    def scenario_id_hides_identity(cls, value: str) -> str:
        lowered = value.lower()
        if any(word in lowered for word in _IDENTITY_WORDS):
            raise ValueError("A scenario ID must not encode clean or faulty identity.")
        return value


class MatchedPair(GraderModel):
    """A clean build and its faulty twin, presented to the model under one ID."""

    clean: ScenarioBuild
    faulty: ScenarioBuild

    @model_validator(mode="after")
    def twins_share_a_public_identity(self) -> Self:
        if self.clean.build != "clean" or self.faulty.build != "faulty":
            raise ValueError("A matched pair is one clean build and one faulty build.")
        if self.clean.scenario_id != self.faulty.scenario_id:
            raise ValueError("Matched builds must share the same public scenario ID.")
        return self

    @property
    def scenario_id(self) -> str:
        return self.clean.scenario_id


class PrivateScenarioState(GraderModel):
    """Scenario-controlled state the grader reads after an attempt.

    This is the only place the build identity and the predicates live. It is
    never serialized into an episode, a step, a finding, or a prompt.
    """

    build: ScenarioBuildVariant
    found_device: bool
    supplied_first_input: bool
    started_process: bool
    created_key: bool
    goal_completed: bool
    inputs_consumed: int = Field(ge=0)
    outputs_created: int = Field(ge=0)
    unrelated_state_unchanged: bool


class PrivateStateSource(Protocol):
    """Reads the private state of the world a connector is attached to."""

    async def read(self) -> PrivateScenarioState:
        """Return the current private state; called only after an attempt."""
        ...


class EpisodeGrade(GraderModel):
    """What the grader concluded about one attempt."""

    episode_id: str = Field(min_length=1)
    goal_completed: bool
    progress_score: int = Field(ge=0, le=len(PROGRESS_PREDICATES))
    milestones: tuple[str, ...] = ()
    behavior_consistent: bool
    grader_version: str = Field(min_length=1)


def fault_predicate(private: PrivateScenarioState) -> bool:
    """The planted behavior: one processing event created two outputs."""
    return private.outputs_created == FAULT_OUTPUT_COUNT


def grade_episode(
    stored: StoredEpisode,
    private: PrivateScenarioState,
    *,
    grader_version: str = GRADER_VERSION,
) -> EpisodeGrade:
    """Score progress and completion from private predicates, after the fact."""
    milestones: list[str] = []
    for name in PROGRESS_PREDICATES:
        if not getattr(private, name):
            break
        milestones.append(name)
    return EpisodeGrade(
        episode_id=stored.episode.episode_id,
        goal_completed=private.goal_completed,
        progress_score=len(milestones),
        milestones=tuple(milestones),
        behavior_consistent=(private.inputs_consumed == 2 and private.unrelated_state_unchanged),
        grader_version=grader_version,
    )


def _resolves(reference: EvidenceRef, stored: StoredEpisode) -> bool:
    """Whether one evidence reference points at a public record of this episode."""
    observations = [stored.episode.reset_observation] + [
        step.result.observation for step in stored.steps
    ]
    if reference.kind == "action_id":
        return any(step.request.action_id == reference.value for step in stored.steps)
    if reference.kind == "observation_sequence":
        try:
            cited = int(reference.value)
        except ValueError:
            return False
        return any(observation.sequence == cited for observation in observations)
    if reference.kind == "object_id":
        return any(
            visible.object_id == reference.value
            for observation in observations
            for visible in observation.visible_objects
        )
    return any(
        message.text == reference.value
        for observation in observations
        for message in observation.messages
    )


def verify_finding(
    finding: FindingRecord,
    stored: StoredEpisode,
    *,
    private: PrivateScenarioState,
    clean_twin: PrivateScenarioState | None,
    decided_at: datetime,
    grader_version: str = GRADER_VERSION,
) -> FindingVerdict:
    """Decide whether a reported defect is real, from predicates and the clean twin.

    `private` is the state of the world the finding was reported in.
    `clean_twin` is the state of the matched clean build after the same
    action sequence, or `None` when no such run exists yet.
    """
    if finding.episode_id != stored.episode.episode_id:
        raise ValueError("The finding belongs to a different episode than the one supplied.")

    def rejected(code: str, reason: str) -> FindingVerdict:
        return FindingVerdict(
            finding_id=finding.finding_id,
            verification="rejected",
            reason_code=code,
            reason=reason,
            grader_version=grader_version,
            decided_at=decided_at,
        )

    report = finding.report
    if report.expected_count is None or report.actual_count is None or not report.evidence:
        return rejected(
            "incomplete_report",
            "A verifiable report needs an expected count, an actual count, and at least "
            "one public evidence reference.",
        )
    unresolved = [ref for ref in report.evidence if not _resolves(ref, stored)]
    if unresolved:
        return rejected(
            "unresolved_evidence",
            "Evidence must reference this episode's own public records; "
            f"{len(unresolved)} reference(s) do not.",
        )
    if not fault_predicate(private):
        return rejected(
            "no_predicate_hit",
            "The private fault predicate did not hold in the reported attempt, so the "
            "reported behavior did not occur however confidently it was described.",
        )
    if report.actual_count != private.outputs_created:
        return rejected(
            "count_mismatch",
            f"The report claims {report.actual_count} output(s); the scenario recorded "
            f"{private.outputs_created}.",
        )
    if clean_twin is None:
        return rejected(
            "clean_twin_unavailable",
            "The matched clean build has not been run under the same sequence, so the "
            "behavior cannot be distinguished from normal behavior.",
        )
    if fault_predicate(clean_twin):
        return rejected(
            "present_in_clean_twin",
            "The matched clean build shows the same behavior under the same sequence, "
            "so it is normal behavior, not a defect.",
        )
    if report.expected_count != clean_twin.outputs_created:
        return rejected(
            "count_mismatch",
            f"The report expects {report.expected_count} output(s); the clean build "
            f"produces {clean_twin.outputs_created}.",
        )
    return FindingVerdict(
        finding_id=finding.finding_id,
        verification="confirmed",
        reason_code="verified",
        reason=(
            "The fault predicate holds in the reported attempt, the reported counts "
            "match, and the matched clean build does not show the behavior."
        ),
        grader_version=grader_version,
        decided_at=decided_at,
    )
