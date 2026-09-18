"""Optional per-episode wall-time overrides never change the frozen defaults.

`training_experiment`/`heldout_experiment`/`check_heldout_budgets` already
accept optional decision/primitive overrides (section 26.3). This adds the
matching optional wall-time overrides they need for the demo-trial profile's
`training_wall_time`/`heldout_wall_time` escalation keys, requested after a
live trial showed a training episode stopped on `wall_time_limit` with no
escalation available for it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from noob_agent.runtime.heldout import (
    HELD_OUT_WALL_TIME_MS,
    check_heldout_budgets,
    heldout_experiment,
)
from noob_agent.runtime.sequence import TRAINING_WALL_TIME_MS, training_experiment

CREATED_AT = datetime(2026, 9, 18, 0, 0, 0, tzinfo=UTC)


def test_training_experiment_defaults_to_the_frozen_wall_time_budget() -> None:
    record = training_experiment(
        experiment_id="seq-training",
        model_id="fake-model",
        connector_version="v1",
        created_at=CREATED_AT,
    )
    assert record.wall_time_budget_ms == TRAINING_WALL_TIME_MS


def test_training_experiment_accepts_a_higher_wall_time_override() -> None:
    record = training_experiment(
        experiment_id="seq-training",
        model_id="fake-model",
        connector_version="v1",
        created_at=CREATED_AT,
        wall_time_budget_ms=TRAINING_WALL_TIME_MS * 3,
    )
    assert record.wall_time_budget_ms == TRAINING_WALL_TIME_MS * 3
    # The frozen constant itself is untouched.
    assert TRAINING_WALL_TIME_MS == 180_000


def test_heldout_experiment_defaults_to_the_frozen_wall_time_budget() -> None:
    record = heldout_experiment(
        experiment_id="seq-heldout",
        model_id="fake-model",
        connector_version="v1",
        created_at=CREATED_AT,
    )
    assert record.wall_time_budget_ms == HELD_OUT_WALL_TIME_MS


def test_check_heldout_budgets_refuses_a_raised_wall_time_without_a_raised_maximum() -> None:
    record = heldout_experiment(
        experiment_id="seq-heldout",
        model_id="fake-model",
        connector_version="v1",
        created_at=CREATED_AT,
        wall_time_budget_ms=HELD_OUT_WALL_TIME_MS * 3,
    )
    with pytest.raises(ValueError, match="wall-time"):
        check_heldout_budgets(record)


def test_check_heldout_budgets_admits_a_raised_wall_time_with_a_matching_maximum() -> None:
    record = heldout_experiment(
        experiment_id="seq-heldout",
        model_id="fake-model",
        connector_version="v1",
        created_at=CREATED_AT,
        wall_time_budget_ms=HELD_OUT_WALL_TIME_MS * 3,
    )
    # Must not raise.
    check_heldout_budgets(record, max_wall_time_budget_ms=HELD_OUT_WALL_TIME_MS * 3)
