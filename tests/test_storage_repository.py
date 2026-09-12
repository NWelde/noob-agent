"""The SQLite store is the local source of truth for one episode's public record."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from noob_agent.domain.model import Observation
from noob_agent.domain.records import EpisodeOutcome, EpisodeRecord, ExperimentRecord, StepRecord
from noob_agent.storage import (
    DuplicateRecordError,
    EpisodeFinalizedError,
    EpisodeStore,
    InconsistentRecordError,
    StorageError,
    UnknownRecordError,
)
from noob_agent.storage.repository import _classify


def test_round_trips_an_episode_in_sequence(
    store: EpisodeStore,
    experiment: ExperimentRecord,
    episode: EpisodeRecord,
    outcome: EpisodeOutcome,
    step_factory: Callable[..., StepRecord],
) -> None:
    store.create_experiment(experiment)
    store.create_episode(episode)
    # Appended out of order on purpose: ordering must come from the recorded
    # sequence, never from insertion order.
    for sequence in (2, 1, 3):
        store.append_step(step_factory(sequence))
    store.finalize_episode(outcome)

    stored = store.read_episode(episode.episode_id)

    assert stored.episode == episode
    assert stored.outcome == outcome
    assert [step.sequence for step in stored.steps] == [1, 2, 3]
    assert [step.request.action_id for step in stored.steps] == ["a_0001", "a_0002", "a_0003"]


def test_preserves_the_exact_public_payloads(
    store: EpisodeStore,
    experiment: ExperimentRecord,
    episode: EpisodeRecord,
    step_factory: Callable[..., StepRecord],
) -> None:
    """Observations, requests, and results are stored exactly as delivered."""
    original = step_factory(1)
    store.create_experiment(experiment)
    store.create_episode(episode)
    store.append_step(original)

    stored = store.read_episode(episode.episode_id)

    assert stored.steps[0] == original
    assert stored.episode.manifest == episode.manifest
    assert stored.episode.reset_observation == episode.reset_observation


def test_rejects_a_duplicate_episode_id(
    store: EpisodeStore, experiment: ExperimentRecord, episode: EpisodeRecord
) -> None:
    store.create_experiment(experiment)
    store.create_episode(episode)

    with pytest.raises(DuplicateRecordError):
        store.create_episode(episode)


def test_rejects_a_duplicate_experiment_id(
    store: EpisodeStore, experiment: ExperimentRecord
) -> None:
    store.create_experiment(experiment)

    with pytest.raises(DuplicateRecordError):
        store.create_experiment(experiment)


def test_rejects_a_duplicate_action_id_within_an_episode(
    store: EpisodeStore,
    experiment: ExperimentRecord,
    episode: EpisodeRecord,
    step_factory: Callable[..., StepRecord],
) -> None:
    store.create_experiment(experiment)
    store.create_episode(episode)
    store.append_step(step_factory(1, action_id="a_0001"))

    with pytest.raises(DuplicateRecordError):
        store.append_step(step_factory(2, action_id="a_0001"))


def test_rejects_a_duplicate_sequence_within_an_episode(
    store: EpisodeStore,
    experiment: ExperimentRecord,
    episode: EpisodeRecord,
    step_factory: Callable[..., StepRecord],
) -> None:
    store.create_experiment(experiment)
    store.create_episode(episode)
    store.append_step(step_factory(1, action_id="a_0001"))

    with pytest.raises(DuplicateRecordError):
        store.append_step(step_factory(1, action_id="a_0002"))


def test_allows_the_same_action_id_in_a_different_episode(
    store: EpisodeStore,
    experiment: ExperimentRecord,
    episode: EpisodeRecord,
    observation_factory: Callable[..., Observation],
    step_factory: Callable[..., StepRecord],
) -> None:
    """Action IDs are episode-local, so uniqueness must not be global."""
    store.create_experiment(experiment)
    store.create_episode(episode)
    store.append_step(step_factory(1, action_id="a_0001"))

    other = episode.model_copy(
        update={
            "episode_id": "ep_0002",
            "reset_observation": observation_factory(0, episode_id="ep_0002"),
        }
    )
    store.create_episode(other)
    store.append_step(step_factory(1, episode_id="ep_0002", action_id="a_0001"))

    assert len(store.read_episode("ep_0002").steps) == 1


def test_rejects_an_episode_whose_reset_belongs_to_another_episode(
    store: EpisodeStore, experiment: ExperimentRecord, episode: EpisodeRecord
) -> None:
    """model_copy skips validation, so the store must re-check before writing."""
    store.create_experiment(experiment)
    smuggled = episode.model_copy(update={"episode_id": "ep_0002"})

    with pytest.raises(InconsistentRecordError):
        store.create_episode(smuggled)

    with pytest.raises(UnknownRecordError):
        store.read_episode("ep_0002")


def test_create_experiment_rejects_an_internally_inconsistent_record(
    store: EpisodeStore, database_path: Path, experiment: ExperimentRecord
) -> None:
    """model_copy skips validation, so the store must re-check before writing."""
    smuggled = experiment.model_copy(update={"decision_budget": -99})

    with pytest.raises(InconsistentRecordError):
        store.create_experiment(smuggled)

    connection = sqlite3.connect(database_path)
    try:
        row = connection.execute(
            "SELECT 1 FROM experiment WHERE experiment_id = ?", (experiment.experiment_id,)
        ).fetchone()
    finally:
        connection.close()
    assert row is None


def test_finalize_episode_rejects_an_internally_inconsistent_record(
    store: EpisodeStore,
    database_path: Path,
    experiment: ExperimentRecord,
    episode: EpisodeRecord,
    outcome: EpisodeOutcome,
) -> None:
    """model_copy skips validation, so the store must re-check before writing."""
    store.create_experiment(experiment)
    store.create_episode(episode)
    smuggled = outcome.model_copy(update={"total_decisions": -5})

    with pytest.raises(InconsistentRecordError):
        store.finalize_episode(smuggled)

    connection = sqlite3.connect(database_path)
    try:
        row = connection.execute(
            "SELECT 1 FROM episode_outcome WHERE episode_id = ?", (outcome.episode_id,)
        ).fetchone()
    finally:
        connection.close()
    assert row is None


def test_rejects_appending_a_step_after_the_episode_is_finalized(
    store: EpisodeStore,
    experiment: ExperimentRecord,
    episode: EpisodeRecord,
    outcome: EpisodeOutcome,
    step_factory: Callable[..., StepRecord],
) -> None:
    """A finalized episode's outcome must not go stale relative to its steps."""
    store.create_experiment(experiment)
    store.create_episode(episode)
    store.finalize_episode(outcome)

    with pytest.raises(EpisodeFinalizedError):
        store.append_step(step_factory(1))

    assert store.read_episode(episode.episode_id).steps == ()


def test_read_episode_wraps_a_corrupted_row_as_a_storage_error(
    store: EpisodeStore, database_path: Path, experiment: ExperimentRecord, episode: EpisodeRecord
) -> None:
    """A row that bypassed the store's own guards must not leak a raw ValidationError."""
    store.create_experiment(experiment)
    store.create_episode(episode)

    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "UPDATE episode SET scenario_id = '' WHERE episode_id = ?", (episode.episode_id,)
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(InconsistentRecordError):
        store.read_episode(episode.episode_id)


def test_classify_treats_a_non_uniqueness_integrity_error_as_a_generic_storage_error() -> None:
    """NOT NULL and CHECK failures are not duplicates and must not be mislabeled."""
    error = sqlite3.IntegrityError("NOT NULL constraint failed: experiment.model_id")

    classified = _classify(error, "Experiment 'exp_0001'")

    assert type(classified) is StorageError
    assert not isinstance(classified, DuplicateRecordError)


def test_rejects_a_step_whose_result_does_not_match_its_request(
    store: EpisodeStore,
    experiment: ExperimentRecord,
    episode: EpisodeRecord,
    step_factory: Callable[..., StepRecord],
) -> None:
    store.create_experiment(experiment)
    store.create_episode(episode)
    smuggled = step_factory(1).model_copy(update={"sequence": 2})

    with pytest.raises(InconsistentRecordError):
        store.append_step(smuggled)

    assert store.read_episode(episode.episode_id).steps == ()


def test_rejects_a_step_for_an_unknown_episode(
    store: EpisodeStore, step_factory: Callable[..., StepRecord]
) -> None:
    with pytest.raises(UnknownRecordError):
        store.append_step(step_factory(1, episode_id="ep_missing"))


def test_rejects_an_episode_for_an_unknown_experiment(
    store: EpisodeStore, episode: EpisodeRecord
) -> None:
    with pytest.raises(UnknownRecordError):
        store.create_episode(episode)


def test_rejects_finalizing_an_unknown_episode(
    store: EpisodeStore, outcome: EpisodeOutcome
) -> None:
    with pytest.raises(UnknownRecordError):
        store.finalize_episode(outcome)


def test_rejects_finalizing_an_episode_twice(
    store: EpisodeStore,
    experiment: ExperimentRecord,
    episode: EpisodeRecord,
    outcome: EpisodeOutcome,
) -> None:
    store.create_experiment(experiment)
    store.create_episode(episode)
    store.finalize_episode(outcome)

    with pytest.raises(DuplicateRecordError):
        store.finalize_episode(outcome)


def test_reading_an_unknown_episode_raises(store: EpisodeStore) -> None:
    with pytest.raises(UnknownRecordError):
        store.read_episode("ep_missing")


def test_an_unfinished_episode_reads_back_without_an_outcome(
    store: EpisodeStore, experiment: ExperimentRecord, episode: EpisodeRecord
) -> None:
    store.create_experiment(experiment)
    store.create_episode(episode)

    stored = store.read_episode(episode.episode_id)

    assert stored.outcome is None
    assert stored.steps == ()


def test_a_failed_batch_rolls_back_completely(
    store: EpisodeStore,
    experiment: ExperimentRecord,
    episode: EpisodeRecord,
    step_factory: Callable[..., StepRecord],
) -> None:
    """append_steps is one explicit transaction: all rows land, or none do."""
    store.create_experiment(experiment)
    store.create_episode(episode)

    batch = [
        step_factory(1, action_id="a_0001"),
        step_factory(2, action_id="a_0002"),
        step_factory(3, action_id="a_0001"),  # duplicate action ID
    ]

    with pytest.raises(DuplicateRecordError):
        store.append_steps(batch)

    assert store.read_episode(episode.episode_id).steps == ()


def test_a_successful_batch_lands_completely(
    store: EpisodeStore,
    experiment: ExperimentRecord,
    episode: EpisodeRecord,
    step_factory: Callable[..., StepRecord],
) -> None:
    store.create_experiment(experiment)
    store.create_episode(episode)

    store.append_steps([step_factory(1), step_factory(2)])

    assert [step.sequence for step in store.read_episode(episode.episode_id).steps] == [1, 2]


def test_records_survive_reopening_the_database(
    database_path: Path,
    experiment: ExperimentRecord,
    episode: EpisodeRecord,
    step_factory: Callable[..., StepRecord],
) -> None:
    """Durability is the point: a closed store's rows are still there."""
    with EpisodeStore.open(database_path) as first:
        first.create_experiment(experiment)
        first.create_episode(episode)
        first.append_step(step_factory(1))

    with EpisodeStore.open(database_path) as second:
        assert [step.sequence for step in second.read_episode(episode.episode_id).steps] == [1]


def test_persisted_rows_contain_no_private_state(
    store: EpisodeStore,
    database_path: Path,
    experiment: ExperimentRecord,
    episode: EpisodeRecord,
    outcome: EpisodeOutcome,
    step_factory: Callable[..., StepRecord],
) -> None:
    """End-to-end leak check over the bytes actually written to disk."""
    store.create_experiment(experiment)
    store.create_episode(episode)
    store.append_step(step_factory(1))
    store.finalize_episode(outcome)

    banned = ("grader", "faulty", "held_out", "ground_truth", "secret", "api_key")
    connection = sqlite3.connect(database_path)
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        ]
        contents = json.dumps(
            {table: connection.execute(f"SELECT * FROM {table}").fetchall() for table in tables}
        ).lower()
    finally:
        connection.close()

    for token in banned:
        assert token not in contents, f"persisted rows leak {token!r}"
