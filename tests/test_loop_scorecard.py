"""Loop scorecard: the measurements every section 22 optimization is judged by."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from noob_agent.agents.action import UNUSABLE_REPLY_TOOL
from noob_agent.domain.model import Observation
from noob_agent.domain.records import (
    EpisodeOutcome,
    EpisodeRecord,
    ExperimentRecord,
    ModelCallPurpose,
    ModelCallRecord,
    StepRecord,
)
from noob_agent.observability.loop_scorecard import (
    SequenceOutcome,
    load_sequences,
    render_markdown,
    score_run,
    score_sequence,
)
from noob_agent.storage import EpisodeStore

STARTED_AT = datetime(2026, 9, 12, 15, 0, 0, tzinfo=UTC)

SEQUENCE = "bench-s01"


def _experiment(experiment: ExperimentRecord, suffix: str, sequence: str) -> ExperimentRecord:
    return experiment.model_copy(update={"experiment_id": f"{sequence}-{suffix}"})


def _call(
    sequence: str,
    index: int,
    *,
    purpose: ModelCallPurpose = "action",
    experiment_suffix: str = "training",
    episode_id: str | None = None,
    latency_ms: int = 100,
    finish_reason: str | None = "stop",
    input_tokens: int | None = 1_000,
    output_tokens: int | None = 50,
    max_output_tokens: int = 1_024,
    seconds: int = 0,
) -> ModelCallRecord:
    return ModelCallRecord(
        call_id=f"mc_{sequence}_{index:03d}",
        experiment_id=f"{sequence}-{experiment_suffix}",
        purpose=purpose,
        episode_id=episode_id,
        provider="fake",
        model_id="fake-model",
        system="system",
        prompt="prompt",
        max_output_tokens=max_output_tokens,
        temperature=0.0,
        response_text="",
        finish_reason=finish_reason,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        started_at=STARTED_AT + timedelta(seconds=seconds),
    )


def _seed_sequence(
    store: EpisodeStore,
    experiment: ExperimentRecord,
    episode: EpisodeRecord,
    step_factory: Callable[..., StepRecord],
    *,
    sequence: str = SEQUENCE,
    with_heldout: bool = True,
) -> tuple[str, str]:
    """Training: 2 unusable replies and 2 observes; one build and one repair."""
    store.create_experiment(_experiment(experiment, "training", sequence))
    store.create_experiment(_experiment(experiment, "heldout", sequence))

    training_id = f"{sequence}-train-ep"
    store.create_episode(
        episode.model_copy(
            update={
                "episode_id": training_id,
                "experiment_id": f"{sequence}-training",
                "reset_observation": episode.reset_observation.model_copy(
                    update={"episode_id": training_id}
                ),
            }
        )
    )
    tools = (UNUSABLE_REPLY_TOOL, "observe", UNUSABLE_REPLY_TOOL, "observe")
    for number, tool in enumerate(tools, start=1):
        status = "rejected" if tool == UNUSABLE_REPLY_TOOL else "succeeded"
        store.append_step(
            step_factory(number, episode_id=training_id, tool_name=tool, status=status)
        )
    store.finalize_episode(
        EpisodeOutcome(
            episode_id=training_id,
            stop_reason="decision_limit",
            terminal=False,
            total_decisions=4,
            total_primitives=2,
            finished_at=STARTED_AT + timedelta(seconds=40),
        )
    )
    for number, (latency, finish) in enumerate(
        ((100, "length"), (200, "stop"), (300, "length"), (400, "stop")), start=1
    ):
        store.record_model_call(
            _call(
                sequence,
                number,
                episode_id=training_id,
                latency_ms=latency,
                finish_reason=finish,
                seconds=number,
            )
        )
    store.record_model_call(
        _call(
            sequence,
            10,
            purpose="build",
            episode_id=training_id,
            latency_ms=5_000,
            finish_reason="length",
            input_tokens=800,
            output_tokens=12_000,
            max_output_tokens=12_000,
            seconds=45,
        )
    )
    store.record_model_call(
        _call(
            sequence,
            11,
            purpose="repair",
            episode_id=training_id,
            latency_ms=2_000,
            input_tokens=1_500,
            output_tokens=900,
            max_output_tokens=3_000,
            seconds=55,
        )
    )

    heldout_id = f"{sequence}-heldout-ep"
    if with_heldout:
        store.create_episode(
            episode.model_copy(
                update={
                    "episode_id": heldout_id,
                    "experiment_id": f"{sequence}-heldout",
                    "split": "held-out",
                    "reset_observation": episode.reset_observation.model_copy(
                        update={"episode_id": heldout_id}
                    ),
                    "started_at": STARTED_AT + timedelta(seconds=60),
                }
            )
        )
        store.append_step(step_factory(1, episode_id=heldout_id, terminal=True))
        store.finalize_episode(
            EpisodeOutcome(
                episode_id=heldout_id,
                stop_reason="terminal_state",
                terminal=True,
                total_decisions=1,
                total_primitives=1,
                finished_at=STARTED_AT + timedelta(seconds=90),
            )
        )
        store.record_model_call(
            _call(
                sequence,
                20,
                experiment_suffix="heldout",
                episode_id=heldout_id,
                latency_ms=500,
                seconds=61,
            )
        )
    return training_id, heldout_id


def test_a_database_score_counts_unusable_and_capped_decisions_separately(
    store: EpisodeStore,
    database_path: Path,
    experiment: ExperimentRecord,
    episode: EpisodeRecord,
    step_factory: Callable[..., StepRecord],
) -> None:
    _seed_sequence(store, experiment, episode, step_factory)

    (records,) = load_sequences(database_path)
    score = score_sequence(records)

    assert score.sequence_id == SEQUENCE
    assert score.action.decisions == 5
    assert score.action.unusable_replies == 2
    assert score.action.calls == 5
    assert score.action.capped_calls == 2
    assert score.action.latency_p50_ms == 300
    assert score.action.latency_p90_ms == 500
    assert score.builder.builds == 1
    assert score.builder.repairs == 1
    assert score.builder.capped_calls == 1
    assert score.builder.output_tokens == 12_900
    assert score.training is not None
    assert score.training.stop_reason == "decision_limit"
    assert score.training.goal_completed is None
    assert [episode.stop_reason for episode in score.heldout] == ["terminal_state"]
    assert score.wall_time_seconds == 90.0


def test_a_database_score_infers_acceptance_and_says_so(
    store: EpisodeStore,
    database_path: Path,
    experiment: ExperimentRecord,
    episode: EpisodeRecord,
    step_factory: Callable[..., StepRecord],
) -> None:
    _seed_sequence(store, experiment, episode, step_factory, sequence="with-heldout")
    _seed_sequence(
        store, experiment, episode, step_factory, sequence="no-heldout", with_heldout=False
    )

    scores = {
        records.sequence_id: score_sequence(records) for records in load_sequences(database_path)
    }

    assert scores["with-heldout"].builder.accepted is True
    assert scores["no-heldout"].builder.accepted is False
    assert all(score.acceptance_source == "inferred" for score in scores.values())
    assert all(score.builder.stop_reason is None for score in scores.values())


def test_learning_cost_is_checked_against_the_protocol_ceilings(
    store: EpisodeStore,
    database_path: Path,
    experiment: ExperimentRecord,
    episode: EpisodeRecord,
    step_factory: Callable[..., StepRecord],
) -> None:
    _seed_sequence(store, experiment, episode, step_factory)

    score = score_sequence(load_sequences(database_path)[0])

    # Four training Action calls at 1,050 tokens, a 12,800-token build, a 2,400-token repair.
    assert score.learning_calls == 6
    assert score.learning_tokens == 4 * 1_050 + 12_800 + 2_400
    assert score.ceilings_exceeded == ("build_tokens",)


def test_unreported_usage_is_counted_rather_than_treated_as_free(
    store: EpisodeStore,
    database_path: Path,
    experiment: ExperimentRecord,
    episode: EpisodeRecord,
    step_factory: Callable[..., StepRecord],
) -> None:
    training_id, _ = _seed_sequence(store, experiment, episode, step_factory)
    store.record_model_call(
        _call(SEQUENCE, 30, episode_id=training_id, input_tokens=None, output_tokens=None)
    )

    score = score_sequence(load_sequences(database_path)[0])

    assert score.action.unknown_usage_calls == 1
    assert "usage_unknown" in score.ceilings_exceeded


def test_in_process_results_supply_grades_and_the_builder_outcome(
    store: EpisodeStore,
    database_path: Path,
    experiment: ExperimentRecord,
    episode: EpisodeRecord,
    step_factory: Callable[..., StepRecord],
) -> None:
    training_id, heldout_id = _seed_sequence(store, experiment, episode, step_factory)

    score = score_sequence(
        load_sequences(database_path)[0],
        SequenceOutcome(
            builder_accepted=True,
            builder_stop_reason="accepted",
            grades={training_id: False, heldout_id: True},
            skill_uses={heldout_id: 2},
        ),
    )

    assert score.acceptance_source == "result"
    assert score.builder.stop_reason == "accepted"
    assert score.training is not None and score.training.goal_completed is False
    assert score.heldout[0].goal_completed is True
    assert score.heldout[0].skill_uses == 2


def test_loading_never_modifies_the_database_and_reads_newer_schemas(
    store: EpisodeStore,
    database_path: Path,
    experiment: ExperimentRecord,
    episode: EpisodeRecord,
    step_factory: Callable[..., StepRecord],
) -> None:
    _seed_sequence(store, experiment, episode, step_factory)
    store.create_experiment(experiment.model_copy(update={"experiment_id": "unrelated"}))
    store.close()
    with sqlite3.connect(database_path) as connection:
        connection.execute("UPDATE schema_version SET version = 99")
    before = hashlib.sha256(database_path.read_bytes()).hexdigest()

    loaded = load_sequences(database_path)

    assert [records.sequence_id for records in loaded] == [SEQUENCE]
    assert hashlib.sha256(database_path.read_bytes()).hexdigest() == before


def test_a_missing_database_is_refused(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_sequences(tmp_path / "absent.sqlite3")


def test_run_totals_pool_every_sequence(
    store: EpisodeStore,
    database_path: Path,
    experiment: ExperimentRecord,
    episode: EpisodeRecord,
    step_factory: Callable[..., StepRecord],
) -> None:
    _seed_sequence(store, experiment, episode, step_factory, sequence="a")
    _seed_sequence(store, experiment, episode, step_factory, sequence="b", with_heldout=False)

    run = score_run([score_sequence(records) for records in load_sequences(database_path)])

    assert run.totals.sequences == 2
    assert run.totals.sequences_with_accepted_skill == 1
    assert run.totals.action_decisions == 9
    assert run.totals.unusable_replies == 4
    assert run.totals.unusable_rate == pytest.approx(4 / 9)
    assert run.totals.capped_builder_calls == 2
    assert run.totals.heldout_episodes == 1
    assert run.totals.heldout_goal_completed is None


def test_markdown_names_every_target_metric_and_sequence(
    store: EpisodeStore,
    database_path: Path,
    experiment: ExperimentRecord,
    episode: EpisodeRecord,
    step_factory: Callable[..., StepRecord],
    observation_factory: Callable[..., Observation],
) -> None:
    del observation_factory
    _seed_sequence(store, experiment, episode, step_factory)

    text = render_markdown(
        score_run([score_sequence(load_sequences(database_path)[0])]), title="Baseline"
    )

    assert text.startswith("# Baseline")
    for label in (
        "Unusable Action replies",
        "Action decision latency p50",
        "Sequences with an accepted skill",
        "Builder calls ending at their cap",
        "Sequence wall time",
        "Learning tokens",
        SEQUENCE,
    ):
        assert label in text


def test_a_database_from_before_model_call_records_scores_without_calls(
    store: EpisodeStore,
    database_path: Path,
    experiment: ExperimentRecord,
    episode: EpisodeRecord,
    step_factory: Callable[..., StepRecord],
) -> None:
    _seed_sequence(store, experiment, episode, step_factory)
    store.close()
    with sqlite3.connect(database_path) as connection:
        connection.execute("DROP TABLE model_call")

    score = score_sequence(load_sequences(database_path)[0])

    assert score.action.calls == 0
    assert score.action.unusable_replies == 2
    assert score.action.latency_p50_ms is None
