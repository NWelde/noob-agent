"""Durable records must be immutable, JSON-round-trippable, and closed to extras."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from noob_agent.domain.records import (
    EpisodeOutcome,
    EpisodeRecord,
    ExperimentRecord,
    StepRecord,
)


def test_step_record_round_trips_through_json(step_factory: Callable[..., StepRecord]) -> None:
    record = step_factory(1)

    restored = StepRecord.model_validate_json(record.model_dump_json())

    assert restored == record
    assert restored.result.observation == record.result.observation


def test_episode_record_round_trips_through_json(episode: EpisodeRecord) -> None:
    restored = EpisodeRecord.model_validate_json(episode.model_dump_json())

    assert restored == episode
    assert restored.manifest == episode.manifest
    assert restored.reset_observation == episode.reset_observation


def test_experiment_and_outcome_round_trip_through_json(
    experiment: ExperimentRecord, outcome: EpisodeOutcome
) -> None:
    assert ExperimentRecord.model_validate_json(experiment.model_dump_json()) == experiment
    assert EpisodeOutcome.model_validate_json(outcome.model_dump_json()) == outcome


def test_records_are_frozen(step_factory: Callable[..., StepRecord]) -> None:
    record = step_factory(1)

    with pytest.raises(ValidationError):
        record.sequence = 2


@pytest.mark.parametrize(
    "private_field",
    [
        "grader_state",
        "clean_or_faulty",
        "held_out_configuration",
        "private_answer",
        "defect_ground_truth",
    ],
)
def test_records_reject_private_extra_fields(
    private_field: str,
    experiment: ExperimentRecord,
    episode: EpisodeRecord,
    outcome: EpisodeOutcome,
    step_factory: Callable[..., StepRecord],
) -> None:
    """A private-looking field cannot be smuggled into any durable record."""
    for record in (experiment, episode, outcome, step_factory(1)):
        payload = record.model_dump(mode="json")
        payload[private_field] = "leaked"

        with pytest.raises(ValidationError):
            type(record).model_validate(payload)


def test_nested_public_models_reject_private_extra_fields(
    step_factory: Callable[..., StepRecord],
) -> None:
    """The ban reaches nested observations, not just the top-level record."""
    payload = step_factory(1).model_dump(mode="json")
    payload["result"]["observation"]["faulty_variant"] = True

    with pytest.raises(ValidationError):
        StepRecord.model_validate(payload)


def test_outcome_rejects_an_unknown_stop_reason(outcome: EpisodeOutcome) -> None:
    payload = outcome.model_dump(mode="json")
    payload["stop_reason"] = "because_i_said_so"

    with pytest.raises(ValidationError):
        EpisodeOutcome.model_validate(payload)
