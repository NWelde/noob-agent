"""Independent reproduction of a reported defect.

The system, not the reporting model, performs the final check. It starts from
a fresh reset of the same layout with a different precommitted seed, replays
the minimal evidence sequence through ordinary primitives, and then reads the
private predicate. The evaluated model never receives the result.

A reproduction is not an agent attempt: no model is called, no skill runs, and
the replay is recorded on the reproduction record itself rather than as an
episode.
"""

from __future__ import annotations

from noob_agent.connectors import ConnectorLostError, GameConnector
from noob_agent.domain.findings import (
    FindingRecord,
    ReplayOutcome,
    ReplayStep,
    ReproductionRecord,
    ReproductionResult,
    ScenarioBuildVariant,
)
from noob_agent.domain.model import ToolRequest
from noob_agent.domain.records import StepRecord, StoredEpisode
from noob_agent.grading.grader import PrivateStateSource, fault_predicate
from noob_agent.runtime.runner import Clock, SystemClock
from noob_agent.skills.contract import EvidenceRef
from noob_agent.storage import EpisodeStore

# The defect reproduction budgets from connector_contract.md.
REPRODUCTION_DECISION_BUDGET = 8
REPRODUCTION_PRIMITIVE_BUDGET = 16
REPRODUCTION_WALL_TIME_MS = 60_000


def _last_referenced_sequence(
    evidence: tuple[EvidenceRef, ...], stored: StoredEpisode
) -> int | None:
    """The latest step any evidence reference points at, or `None` for all of them."""
    last_step = stored.steps[-1].sequence if stored.steps else 0
    referenced: list[int] = []
    for reference in evidence:
        if reference.kind == "action_id":
            referenced.extend(
                step.sequence for step in stored.steps if step.request.action_id == reference.value
            )
        elif reference.kind == "observation_sequence":
            try:
                referenced.append(int(reference.value))
            except ValueError:
                continue
        else:
            # An object or message is only known to exist by the end of the record.
            referenced.append(last_step)
    return max(referenced) if referenced else None


def _evidence_steps(finding: FindingRecord, stored: StoredEpisode) -> tuple[StepRecord, ...]:
    last = _last_referenced_sequence(finding.report.evidence, stored)
    return tuple(
        step
        for step in stored.steps
        if (last is None or step.sequence <= last) and step.result.status != "rejected"
    )


def evidence_sequence(finding: FindingRecord, stored: StoredEpisode) -> tuple[ReplayStep, ...]:
    """The minimal ordinary-primitive sequence that led to the cited evidence.

    Every delivered primitive up to the last cited record is kept, nested
    skill primitives included, because they are all ordinary controls. Rejected
    requests were never sent, so they are not replayed.
    """
    return tuple(
        ReplayStep(tool_name=step.request.tool_name, arguments=step.request.arguments)
        for step in _evidence_steps(finding, stored)
    )


class ReproductionRunner:
    """Replays a finding's evidence sequence from a fresh reset and checks the predicate."""

    def __init__(self, *, store: EpisodeStore, clock: Clock | None = None) -> None:
        self._store = store
        self._clock = clock if clock is not None else SystemClock()

    async def reproduce(
        self,
        finding: FindingRecord,
        stored: StoredEpisode,
        *,
        connector: GameConnector,
        private_state: PrivateStateSource,
        build: ScenarioBuildVariant,
        seed: int,
        reproduction_id: str,
        primitive_budget: int = REPRODUCTION_PRIMITIVE_BUDGET,
        wall_time_ms: int = REPRODUCTION_WALL_TIME_MS,
    ) -> ReproductionRecord:
        """Run one reproduction on the given build and record it."""
        if finding.episode_id != stored.episode.episode_id:
            raise ValueError("The finding belongs to a different episode than the one supplied.")
        if seed == stored.episode.seed:
            raise ValueError("A reproduction must reset with a different precommitted seed.")
        if primitive_budget > REPRODUCTION_PRIMITIVE_BUDGET:
            raise ValueError(
                f"Reproduction primitive budget is {REPRODUCTION_PRIMITIVE_BUDGET}; "
                f"{primitive_budget} was requested."
            )
        if wall_time_ms > REPRODUCTION_WALL_TIME_MS:
            raise ValueError(
                f"Reproduction wall-time budget is {REPRODUCTION_WALL_TIME_MS} ms; "
                f"{wall_time_ms} was requested."
            )

        recorded_steps = _evidence_steps(finding, stored)
        attempted: list[ReplayOutcome] = []
        result: ReproductionResult = "not_reproduced"
        predicate: bool | None = None
        mismatch: str | None = None

        try:
            await connector.reset(stored.episode.scenario_id, seed)
            started_ms = self._clock.monotonic_ms()
            primitives_used = 0
            completed = True

            for index, recorded in enumerate(recorded_steps, start=1):
                tool_name = recorded.request.tool_name
                if primitives_used >= primitive_budget:
                    result = "infrastructure_failure"
                    mismatch = (
                        f"step {index} ({tool_name}): the reproduction primitive budget "
                        f"({primitive_budget}) was exhausted before the sequence finished."
                    )
                    completed = False
                    break
                if self._clock.monotonic_ms() - started_ms >= wall_time_ms:
                    result = "infrastructure_failure"
                    mismatch = (
                        f"step {index} ({tool_name}): the reproduction wall-time budget "
                        f"({wall_time_ms} ms) was exhausted before the sequence finished."
                    )
                    completed = False
                    break

                request = ToolRequest(
                    action_id=f"r_{index:04d}",
                    tool_name=tool_name,
                    arguments=recorded.request.arguments,
                )
                try:
                    observed = await connector.step(request)
                except ConnectorLostError as error:
                    result = "infrastructure_failure"
                    mismatch = f"step {index} ({tool_name}): the connector was lost: {error}"
                    completed = False
                    break

                primitives_used += observed.primitive_actions_charged
                attempted.append(
                    ReplayOutcome(
                        tool_name=tool_name,
                        arguments=recorded.request.arguments,
                        recorded_status=recorded.result.status,
                        observed_status=observed.status,
                        observed_code=observed.code,
                    )
                )
                if observed.status == "unknown":
                    result = "infrastructure_failure"
                    mismatch = (
                        f"step {index} ({tool_name}): the result is unknown "
                        f"({observed.code}); the replay cannot continue and is never retried."
                    )
                    completed = False
                    break
                if observed.status != recorded.result.status:
                    result = "not_reproduced"
                    mismatch = (
                        f"step {index} ({tool_name}): recorded "
                        f"{recorded.result.status}/{recorded.result.code}, observed "
                        f"{observed.status}/{observed.code}."
                    )
                    completed = False
                    break

            if completed:
                state = await private_state.read()
                predicate = fault_predicate(state)
                if predicate:
                    result = "reproduced"
                else:
                    result = "not_reproduced"
                    mismatch = (
                        "The fault predicate did not hold after the replay "
                        f"(outputs_created={state.outputs_created})."
                    )
        finally:
            await connector.close()

        record = ReproductionRecord(
            reproduction_id=reproduction_id,
            finding_id=finding.finding_id,
            scenario_id=stored.episode.scenario_id,
            seed=seed,
            build=build,
            attempted=tuple(attempted),
            result=result,
            predicate_result=predicate,
            first_mismatch=mismatch,
            attempted_at=self._clock.now(),
        )
        self._store.record_reproduction(record)
        return record
