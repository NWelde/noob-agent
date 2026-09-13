"""Independent reproduction: a fresh reset, ordinary primitives, and the private predicate."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from fakes.connector import SCENARIO_ID, FakeClock, ScriptedConnector, ScriptedStep

from noob_agent.domain.findings import FindingRecord, FindingReport, ReplayStep, ReproductionRecord
from noob_agent.domain.records import EpisodeOutcome, EpisodeRecord, ExperimentRecord, StepRecord
from noob_agent.grading.grader import PrivateScenarioState, verify_finding
from noob_agent.grading.reproduction import (
    REPRODUCTION_PRIMITIVE_BUDGET,
    ReproductionRunner,
    evidence_sequence,
)
from noob_agent.skills.contract import EvidenceRef
from noob_agent.storage import EpisodeStore

REPORTED_AT = datetime(2026, 9, 12, 19, 30, 0, tzinfo=UTC)
ATTEMPTED_AT = datetime(2026, 9, 12, 19, 40, 0, tzinfo=UTC)
TRAINING_SEED = 7
FRESH_SEED = 8


class FakePrivateState:
    """Hands the grader a prepared private state; counts how often it was read."""

    def __init__(self, state: PrivateScenarioState) -> None:
        self._state = state
        self.reads = 0

    async def read(self) -> PrivateScenarioState:
        self.reads += 1
        return self._state


def private(build: str, outputs_created: int) -> PrivateScenarioState:
    return PrivateScenarioState(
        build=build,  # type: ignore[arg-type]
        found_device=True,
        supplied_first_input=True,
        started_process=True,
        created_key=True,
        goal_completed=False,
        inputs_consumed=2,
        outputs_created=outputs_created,
        unrelated_state_unchanged=True,
    )


def finding(evidence: tuple[EvidenceRef, ...] | None = None) -> FindingRecord:
    return FindingRecord(
        finding_id="fnd_0001",
        episode_id="ep_0001",
        action_id="a_0004",
        reporting_model_id="fake-model-a",
        report=FindingReport(
            expected_behavior="One Charged Key after activation.",
            expected_basis="The training attempt produced one key from two shards.",
            actual_behavior="Two Charged Keys after activation.",
            expected_count=1,
            actual_count=2,
            evidence=(
                (EvidenceRef(kind="action_id", value="a_0003"),) if evidence is None else evidence
            ),
        ),
        reported_at=REPORTED_AT,
    )


@pytest.fixture
def stored_episode(
    store: EpisodeStore,
    experiment: ExperimentRecord,
    episode: EpisodeRecord,
    outcome: EpisodeOutcome,
    step_factory: Callable[..., StepRecord],
) -> EpisodeStore:
    """observe, a rejected guess, use_object, then a trailing observe."""
    store.create_experiment(experiment)
    store.create_episode(episode)
    store.append_step(step_factory(1))
    store.append_step(step_factory(2, tool_name="charge_keystone", status="rejected", code="INVALID_TOOL"))
    store.append_step(step_factory(3, tool_name="use_object"))
    store.append_step(step_factory(4))
    store.finalize_episode(outcome)
    store.record_finding(finding())
    return store


def test_the_evidence_sequence_is_minimal_and_uses_ordinary_primitives(
    stored_episode: EpisodeStore,
) -> None:
    stored = stored_episode.read_episode("ep_0001")

    sequence = evidence_sequence(finding(), stored)

    assert sequence == (
        ReplayStep(tool_name="observe", arguments={"radius": 8}),
        ReplayStep(tool_name="use_object", arguments={"radius": 8}),
    )
    whole = evidence_sequence(
        finding(evidence=(EvidenceRef(kind="observation_sequence", value="4"),)), stored
    )
    assert [step.tool_name for step in whole] == ["observe", "use_object", "observe"]


async def reproduce(
    store: EpisodeStore,
    connector: ScriptedConnector,
    state: FakePrivateState,
    *,
    build: str = "faulty",
    seed: int = FRESH_SEED,
    reproduction_id: str = "rep_0001",
    primitive_budget: int = REPRODUCTION_PRIMITIVE_BUDGET,
) -> ReproductionRecord:
    runner = ReproductionRunner(store=store, clock=FakeClock(wall=ATTEMPTED_AT))
    return await runner.reproduce(
        finding(),
        store.read_episode("ep_0001"),
        connector=connector,
        private_state=state,
        build=build,  # type: ignore[arg-type]
        seed=seed,
        reproduction_id=reproduction_id,
        primitive_budget=primitive_budget,
    )


async def test_a_reproduction_replays_the_sequence_from_a_fresh_reset(
    stored_episode: EpisodeStore,
) -> None:
    connector = ScriptedConnector(ScriptedStep(), ScriptedStep(state_changed=True))
    state = FakePrivateState(private("faulty", outputs_created=2))

    record = await reproduce(stored_episode, connector, state)

    assert connector.reset_calls == [(SCENARIO_ID, FRESH_SEED)]
    assert [request.tool_name for request in connector.requests] == ["observe", "use_object"]
    assert connector.closed is True
    assert record.result == "reproduced"
    assert record.predicate_result is True
    assert record.first_mismatch is None
    assert record.build == "faulty"
    assert record.seed == FRESH_SEED
    assert record.finding_id == "fnd_0001"
    assert [step.observed_status for step in record.attempted] == ["succeeded", "succeeded"]
    assert state.reads == 1
    assert stored_episode.read_finding("fnd_0001").reproductions == (record,)


async def test_a_reproduction_needs_a_fresh_seed(stored_episode: EpisodeStore) -> None:
    connector = ScriptedConnector(ScriptedStep(), ScriptedStep())

    with pytest.raises(ValueError):
        await reproduce(
            stored_episode,
            connector,
            FakePrivateState(private("faulty", 2)),
            seed=TRAINING_SEED,
        )
    assert connector.reset_calls == []


async def test_a_failed_replay_records_the_first_mismatch(stored_episode: EpisodeStore) -> None:
    connector = ScriptedConnector(
        ScriptedStep(),
        ScriptedStep(status="failed", code="UNREACHABLE", state_changed=False),
    )
    state = FakePrivateState(private("faulty", 2))

    record = await reproduce(stored_episode, connector, state)

    assert record.result == "not_reproduced"
    assert record.first_mismatch is not None
    assert "step 2" in record.first_mismatch
    assert "use_object" in record.first_mismatch
    assert "UNREACHABLE" in record.first_mismatch
    assert record.predicate_result is None
    assert state.reads == 0
    assert len(connector.requests) == 2


async def test_the_private_predicate_decides_a_matching_replay(
    stored_episode: EpisodeStore,
) -> None:
    connector = ScriptedConnector(ScriptedStep(), ScriptedStep(state_changed=True))

    record = await reproduce(stored_episode, connector, FakePrivateState(private("faulty", 1)))

    assert record.result == "not_reproduced"
    assert record.predicate_result is False
    assert record.first_mismatch is not None
    assert "predicate" in record.first_mismatch.lower()


async def test_the_clean_twin_replay_is_recorded_under_its_build(
    stored_episode: EpisodeStore,
) -> None:
    connector = ScriptedConnector(ScriptedStep(), ScriptedStep(state_changed=True))

    record = await reproduce(
        stored_episode,
        connector,
        FakePrivateState(private("clean", 1)),
        build="clean",
        reproduction_id="rep_clean",
    )

    assert record.build == "clean"
    assert record.result == "not_reproduced"
    assert record.predicate_result is False


async def test_an_unknown_result_is_an_infrastructure_failure(
    stored_episode: EpisodeStore,
) -> None:
    connector = ScriptedConnector(
        ScriptedStep(status="unknown", code="TIMEOUT_UNKNOWN", state_changed=None),
        ScriptedStep(),
    )
    state = FakePrivateState(private("faulty", 2))

    record = await reproduce(stored_episode, connector, state)

    assert record.result == "infrastructure_failure"
    assert record.predicate_result is None
    assert record.first_mismatch is not None
    assert "unknown" in record.first_mismatch.lower()
    assert len(connector.requests) == 1
    assert state.reads == 0


async def test_the_replay_stays_within_the_reproduction_budget(
    stored_episode: EpisodeStore,
) -> None:
    connector = ScriptedConnector(ScriptedStep(), ScriptedStep())

    record = await reproduce(
        stored_episode, connector, FakePrivateState(private("faulty", 2)), primitive_budget=1
    )

    assert record.result == "infrastructure_failure"
    assert record.first_mismatch is not None
    assert "budget" in record.first_mismatch.lower()
    assert len(connector.requests) == 1

    with pytest.raises(ValueError):
        await reproduce(
            stored_episode,
            ScriptedConnector(),
            FakePrivateState(private("faulty", 2)),
            primitive_budget=REPRODUCTION_PRIMITIVE_BUDGET + 1,
        )


async def test_both_builds_reproduce_and_the_finding_is_verified(
    stored_episode: EpisodeStore,
) -> None:
    faulty = FakePrivateState(private("faulty", 2))
    clean = FakePrivateState(private("clean", 1))

    faulty_record = await reproduce(
        stored_episode,
        ScriptedConnector(ScriptedStep(), ScriptedStep(state_changed=True)),
        faulty,
        build="faulty",
        reproduction_id="rep_faulty",
    )
    clean_record = await reproduce(
        stored_episode,
        ScriptedConnector(ScriptedStep(), ScriptedStep(state_changed=True)),
        clean,
        build="clean",
        reproduction_id="rep_clean",
    )
    verdict = verify_finding(
        finding(),
        stored_episode.read_episode("ep_0001"),
        private=await faulty.read(),
        clean_twin=await clean.read(),
        decided_at=ATTEMPTED_AT,
    )
    stored_episode.record_verdict(verdict)

    assert faulty_record.result == "reproduced"
    assert clean_record.result == "not_reproduced"
    assert verdict.verification == "confirmed"
    read = stored_episode.read_finding("fnd_0001")
    assert read.verdict == verdict
    assert {record.reproduction_id for record in read.reproductions} == {"rep_faulty", "rep_clean"}


def test_reproduction_records_never_reach_public_records() -> None:
    """Public episode and step records have no field that could carry a build label."""
    from noob_agent.domain.records import EpisodeRecord as PublicEpisode

    assert "build" not in PublicEpisode.model_fields
    assert "variant" not in PublicEpisode.model_fields
    assert "build" in ReproductionRecord.model_fields
