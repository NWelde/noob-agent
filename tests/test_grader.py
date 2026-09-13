"""The private grader: a finding is verified by predicates and a clean twin, never by confidence."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from noob_agent.agents.action import parse_decision
from noob_agent.domain.findings import FindingRecord, FindingReport, FindingVerdict
from noob_agent.domain.records import EpisodeOutcome, EpisodeRecord, ExperimentRecord, StepRecord
from noob_agent.grading.grader import (
    GRADER_VERSION,
    MatchedPair,
    PrivateScenarioState,
    ScenarioBuild,
    grade_episode,
    verify_finding,
)
from noob_agent.prompts.action import render_action_prompt
from noob_agent.skills.contract import EvidenceRef
from noob_agent.storage import (
    SCHEMA_VERSION,
    DuplicateRecordError,
    EpisodeStore,
    UnknownRecordError,
)

REPORTED_AT = datetime(2026, 9, 12, 19, 30, 0, tzinfo=UTC)
DECIDED_AT = datetime(2026, 9, 12, 19, 31, 0, tzinfo=UTC)


def private(**overrides: object) -> PrivateScenarioState:
    """A faulty-build state after a full successful run, unless overridden."""
    values: dict[str, object] = {
        "build": "faulty",
        "found_device": True,
        "supplied_first_input": True,
        "started_process": True,
        "created_key": True,
        "goal_completed": True,
        "inputs_consumed": 2,
        "outputs_created": 2,
        "unrelated_state_unchanged": True,
    }
    return PrivateScenarioState.model_validate(values | overrides)


def report(**overrides: object) -> FindingReport:
    values: dict[str, object] = {
        "expected_behavior": "Activating the device with two shards yields one Charged Key.",
        "expected_basis": "The training attempt produced exactly one key from two shards.",
        "actual_behavior": "Two Charged Keys appeared after one activation.",
        "expected_count": 1,
        "actual_count": 2,
        "evidence": (
            EvidenceRef(kind="action_id", value="a_0002"),
            EvidenceRef(kind="observation_sequence", value="3"),
        ),
    }
    return FindingReport.model_validate(values | overrides)


def finding(**overrides: object) -> FindingRecord:
    values: dict[str, object] = {
        "finding_id": "fnd_0001",
        "episode_id": "ep_0001",
        "action_id": "a_0003",
        "reporting_model_id": "fake-model-a",
        "report": report(),
        "reported_at": REPORTED_AT,
    }
    return FindingRecord.model_validate(values | overrides)


@pytest.fixture
def stored_episode(
    store: EpisodeStore,
    experiment: ExperimentRecord,
    episode: EpisodeRecord,
    outcome: EpisodeOutcome,
    step_factory: Callable[..., StepRecord],
) -> EpisodeStore:
    store.create_experiment(experiment)
    store.create_episode(episode)
    store.append_step(step_factory(1))
    store.append_step(step_factory(2, tool_name="use_object"))
    store.append_step(step_factory(3))
    store.finalize_episode(outcome)
    return store


def verify(
    store: EpisodeStore,
    record: FindingRecord,
    *,
    state: PrivateScenarioState,
    clean_twin: PrivateScenarioState | None,
) -> FindingVerdict:
    return verify_finding(
        record,
        store.read_episode(record.episode_id),
        private=state,
        clean_twin=clean_twin,
        decided_at=DECIDED_AT,
    )


def test_a_claimed_defect_with_no_predicate_hit_is_rejected(stored_episode: EpisodeStore) -> None:
    """However confidently it is reported, no predicate hit means no defect."""
    confident = finding(
        report=report(
            actual_behavior="Definitely two keys; I am certain the device is broken.",
        )
    )

    verdict = verify(
        stored_episode,
        confident,
        state=private(outputs_created=1),
        clean_twin=private(build="clean", outputs_created=1),
    )

    assert verdict.verification == "rejected"
    assert verdict.reason_code == "no_predicate_hit"
    assert verdict.grader_version == GRADER_VERSION


def test_a_defect_in_the_faulty_build_and_absent_in_the_clean_twin_is_confirmed(
    stored_episode: EpisodeStore,
) -> None:
    verdict = verify(
        stored_episode,
        finding(),
        state=private(outputs_created=2),
        clean_twin=private(build="clean", outputs_created=1),
    )

    assert verdict.verification == "confirmed"
    assert verdict.reason_code == "verified"
    assert verdict.finding_id == "fnd_0001"
    assert verdict.decided_at == DECIDED_AT


def test_a_defect_that_also_fires_in_the_clean_twin_is_normal_behavior(
    stored_episode: EpisodeStore,
) -> None:
    verdict = verify(
        stored_episode,
        finding(),
        state=private(outputs_created=2),
        clean_twin=private(build="clean", outputs_created=2),
    )

    assert verdict.verification == "rejected"
    assert verdict.reason_code == "present_in_clean_twin"


def test_without_a_clean_twin_nothing_is_confirmed(stored_episode: EpisodeStore) -> None:
    verdict = verify(stored_episode, finding(), state=private(), clean_twin=None)

    assert verdict.verification == "rejected"
    assert verdict.reason_code == "clean_twin_unavailable"


@pytest.mark.parametrize(
    "overrides",
    [
        {"expected_count": None},
        {"actual_count": None},
        {"evidence": ()},
    ],
)
def test_an_incomplete_report_is_rejected(
    stored_episode: EpisodeStore, overrides: dict[str, object]
) -> None:
    """A report needs an expected count, an actual count, and evidence references."""
    verdict = verify(
        stored_episode,
        finding(report=report(**overrides)),
        state=private(),
        clean_twin=private(build="clean", outputs_created=1),
    )

    assert verdict.verification == "rejected"
    assert verdict.reason_code == "incomplete_report"


def test_evidence_must_resolve_to_this_episodes_public_records(
    stored_episode: EpisodeStore,
) -> None:
    unresolved = finding(report=report(evidence=(EvidenceRef(kind="action_id", value="a_9999"),)))

    verdict = verify(
        stored_episode,
        unresolved,
        state=private(),
        clean_twin=private(build="clean", outputs_created=1),
    )

    assert verdict.verification == "rejected"
    assert verdict.reason_code == "unresolved_evidence"


def test_the_reported_counts_must_match_the_private_observation(
    stored_episode: EpisodeStore,
) -> None:
    verdict = verify(
        stored_episode,
        finding(report=report(actual_count=3)),
        state=private(outputs_created=2),
        clean_twin=private(build="clean", outputs_created=1),
    )

    assert verdict.verification == "rejected"
    assert verdict.reason_code == "count_mismatch"


def test_progress_counts_ordered_predicates(stored_episode: EpisodeStore) -> None:
    stored = stored_episode.read_episode("ep_0001")

    partial = grade_episode(
        stored,
        private(
            found_device=True,
            supplied_first_input=True,
            started_process=False,
            created_key=True,
            goal_completed=False,
            outputs_created=0,
        ),
    )
    complete = grade_episode(stored, private())

    assert partial.progress_score == 2
    assert partial.milestones == ("found_device", "supplied_first_input")
    assert partial.goal_completed is False
    assert complete.progress_score == 5
    assert complete.goal_completed is True
    assert complete.behavior_consistent is True
    assert complete.episode_id == "ep_0001"
    assert complete.grader_version == GRADER_VERSION


def test_public_records_carry_no_private_state() -> None:
    with pytest.raises(ValidationError):
        FindingReport.model_validate(report().model_dump() | {"build": "faulty"})
    with pytest.raises(ValidationError):
        FindingRecord.model_validate(finding().model_dump() | {"variant": "faulty"})

    serialized = finding().model_dump_json().lower()
    for private_word in ("faulty", "clean", "predicate", "grader", "outputs_created"):
        assert private_word not in serialized


def test_a_scenario_id_never_encodes_clean_or_faulty_identity() -> None:
    clean = ScenarioBuild(scenario_id="mc_resonator_layout_a", build="clean", fingerprint="c1")
    faulty = ScenarioBuild(scenario_id="mc_resonator_layout_a", build="faulty", fingerprint="f1")
    pair = MatchedPair(clean=clean, faulty=faulty)
    assert pair.scenario_id == "mc_resonator_layout_a"

    with pytest.raises(ValidationError):
        MatchedPair(
            clean=clean,
            faulty=ScenarioBuild(
                scenario_id="mc_resonator_layout_b", build="faulty", fingerprint="f"
            ),
        )
    with pytest.raises(ValidationError):
        ScenarioBuild(scenario_id="mc_resonator_layout_a_faulty", build="faulty", fingerprint="f")
    with pytest.raises(ValidationError):
        ScenarioBuild(scenario_id="mc_clean_layout", build="clean", fingerprint="c")
    with pytest.raises(ValidationError):
        MatchedPair(clean=faulty, faulty=clean)


def test_findings_and_verdicts_are_persisted_once(stored_episode: EpisodeStore) -> None:
    record = finding()
    stored_episode.record_finding(record)
    verdict = verify(
        stored_episode,
        record,
        state=private(),
        clean_twin=private(build="clean", outputs_created=1),
    )
    stored_episode.record_verdict(verdict)

    read = stored_episode.read_finding("fnd_0001")
    assert read.finding == record
    assert read.verdict == verdict
    assert read.reproductions == ()
    assert stored_episode.findings_for_episode("ep_0001") == (record,)

    with pytest.raises(DuplicateRecordError):
        stored_episode.record_finding(record)
    with pytest.raises(DuplicateRecordError):
        stored_episode.record_verdict(verdict)
    with pytest.raises(UnknownRecordError):
        stored_episode.record_finding(finding(finding_id="fnd_0002", episode_id="ep_missing"))
    with pytest.raises(UnknownRecordError):
        stored_episode.record_verdict(verdict.model_copy(update={"finding_id": "fnd_missing"}))
    with pytest.raises(UnknownRecordError):
        stored_episode.read_finding("fnd_missing")


def test_a_version_1_database_is_upgraded_in_place(
    stored_episode: EpisodeStore, database_path: object
) -> None:
    """An existing store gains the new tables; its recorded episodes are untouched."""
    connection = stored_episode._connection  # noqa: SLF001 - simulating an older file on disk.
    for table in ("reproduction", "finding_verdict", "finding"):
        connection.execute(f"DROP TABLE {table}")
    connection.execute("UPDATE schema_version SET version = 1")
    stored_episode.close()

    with EpisodeStore.open(str(database_path)) as reopened:
        version = reopened._connection.execute("SELECT version FROM schema_version").fetchone()
        assert version[0] == SCHEMA_VERSION == 5
        assert reopened.read_episode("ep_0001").outcome is not None
        reopened.record_finding(finding())
        assert reopened.read_finding("fnd_0001").finding == finding()


def test_the_action_agent_can_attach_a_finding_to_a_decision() -> None:
    reply = json.dumps(
        {
            "subgoal": "Report the extra key.",
            "expected_evidence": "The inventory shows two keys.",
            "tool": "observe",
            "arguments": {"radius": 8},
            "finding": report().model_dump(mode="json"),
        }
    )

    parsed = parse_decision(reply)

    assert parsed.kind == "primitive"
    assert parsed.finding == report()

    malformed = parse_decision(json.dumps({"tool": "observe", "finding": {"nonsense": True}}))
    assert malformed.kind == "primitive"
    assert malformed.finding is None


def test_the_prompt_tells_the_agent_how_to_report_but_not_what_to_find(
    observation_factory: Callable[..., object],
) -> None:
    from noob_agent.domain.model import Observation

    observation = observation_factory(0)
    assert isinstance(observation, Observation)
    from noob_agent.prompts.action import ACTION_SYSTEM

    # The finding instructions reach the model through the fixed system prompt.
    rendered = (
        ACTION_SYSTEM
        + "\n"
        + render_action_prompt(
            public_goal=observation.public_goal,
            observation=observation,
            tools=(),
            skills=(),
            history=(),
        )
    )

    assert '"finding"' in rendered
    assert "expected_count" in rendered
    for private_word in ("faulty", "clean", "predicate", "grader", "planted", "bug"):
        assert private_word not in rendered.lower()
