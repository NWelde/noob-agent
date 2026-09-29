"""The demo-trial profile and its escalation loop stay isolated from benchmarks.

`scripts/demo_trial.py` runs one Doom learning sequence at demo-trial budgets
(hackathon_plan.md section 26.3) and reruns with only the exhausted limit
doubled, up to a cap, when the sequence stops before the sample task
completes. These tests use no network and no live model: they exercise the
pure profile and escalation functions directly.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from noob_agent.runtime.heldout import (
    HELD_OUT_DECISION_BUDGET,
    HELD_OUT_PRIMITIVE_BUDGET,
    HELD_OUT_WALL_TIME_MS,
)
from noob_agent.runtime.sequence import (
    LEARNING_CALL_BUDGET,
    LEARNING_TOKEN_BUDGET,
    TRAINING_DECISION_BUDGET,
    TRAINING_PRIMITIVE_BUDGET,
    TRAINING_WALL_TIME_MS,
)


def _module():
    spec = importlib.util.spec_from_file_location("demo_trial", "scripts/demo_trial.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves `from __future__ import annotations` string
    # annotations by looking the module up in sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_run_kind_is_labeled_non_benchmark() -> None:
    m = _module()
    assert m.RUN_KIND == "non-benchmark-demo-trial"


def test_default_profile_raises_only_demo_limits_and_leaves_constants_untouched() -> None:
    before = (
        TRAINING_DECISION_BUDGET,
        TRAINING_PRIMITIVE_BUDGET,
        HELD_OUT_DECISION_BUDGET,
        HELD_OUT_PRIMITIVE_BUDGET,
        LEARNING_TOKEN_BUDGET,
        LEARNING_CALL_BUDGET,
    )
    m = _module()
    limits = m.DemoTrialLimits()

    # The frozen benchmark constants this module imports must be untouched.
    assert (
        TRAINING_DECISION_BUDGET,
        TRAINING_PRIMITIVE_BUDGET,
        HELD_OUT_DECISION_BUDGET,
        HELD_OUT_PRIMITIVE_BUDGET,
        LEARNING_TOKEN_BUDGET,
        LEARNING_CALL_BUDGET,
    ) == before

    assert limits.learning_token_budget >= 1_000_000
    assert limits.training_decision_budget >= 3 * TRAINING_DECISION_BUDGET
    assert limits.training_primitive_budget >= 3 * TRAINING_PRIMITIVE_BUDGET
    assert limits.heldout_decision_budget >= 3 * HELD_OUT_DECISION_BUDGET
    assert limits.heldout_primitive_budget >= 3 * HELD_OUT_PRIMITIVE_BUDGET
    assert limits.training_wall_time_ms >= 3 * TRAINING_WALL_TIME_MS
    assert limits.heldout_wall_time_ms >= 3 * HELD_OUT_WALL_TIME_MS
    assert limits.action_max_output_tokens == 4_000
    assert limits.builder_max_output_tokens == 32_000
    assert 0 < limits.deadline_seconds <= 7_200


def test_profile_never_produces_a_benchmark_sized_limit() -> None:
    """A demo-trial profile must never equal or fall below any frozen benchmark budget."""
    m = _module()
    limits = m.DemoTrialLimits()
    assert limits.training_decision_budget > TRAINING_DECISION_BUDGET
    assert limits.training_primitive_budget > TRAINING_PRIMITIVE_BUDGET
    assert limits.heldout_decision_budget > HELD_OUT_DECISION_BUDGET
    assert limits.heldout_primitive_budget > HELD_OUT_PRIMITIVE_BUDGET
    assert limits.learning_token_budget > LEARNING_TOKEN_BUDGET
    assert limits.learning_call_budget > LEARNING_CALL_BUDGET
    assert limits.training_wall_time_ms > TRAINING_WALL_TIME_MS
    assert limits.heldout_wall_time_ms > HELD_OUT_WALL_TIME_MS


@pytest.mark.parametrize(
    "field",
    [
        "learning_token_budget",
        "learning_call_budget",
        "training_decision_budget",
        "training_primitive_budget",
        "heldout_decision_budget",
        "heldout_primitive_budget",
        "training_wall_time_ms",
        "heldout_wall_time_ms",
        "action_max_output_tokens",
        "builder_max_output_tokens",
        "deadline_seconds",
    ],
)
def test_escalate_doubles_only_the_exhausted_limit(field: str) -> None:
    m = _module()
    key = {
        "learning_token_budget": "learning_token",
        "learning_call_budget": "learning_call",
        "training_decision_budget": "training_decision",
        "training_primitive_budget": "training_primitive",
        "heldout_decision_budget": "heldout_decision",
        "heldout_primitive_budget": "heldout_primitive",
        "training_wall_time_ms": "training_wall_time",
        "heldout_wall_time_ms": "heldout_wall_time",
        "action_max_output_tokens": "action_output_tokens",
        "builder_max_output_tokens": "builder_output_tokens",
        "deadline_seconds": "deadline",
    }[field]
    before = m.DemoTrialLimits(deadline_seconds=1_000)
    after = m.escalate(before, key)
    for other_field in before.__dataclass_fields__:
        before_value = getattr(before, other_field)
        after_value = getattr(after, other_field)
        if other_field == field:
            assert after_value == before_value * 2
        else:
            assert after_value == before_value


def test_escalate_stops_at_the_token_cap() -> None:
    m = _module()
    limits = m.DemoTrialLimits(learning_token_budget=7_000_000)
    escalated = m.escalate(limits, "learning_token")
    assert escalated.learning_token_budget == 8_000_000
    # Already at the cap: escalating again must not exceed it.
    at_cap = m.escalate(escalated, "learning_token")
    assert at_cap.learning_token_budget == 8_000_000
    assert m.is_at_cap(at_cap, "learning_token")
    assert not m.is_at_cap(limits, "learning_token")


def test_escalate_stops_at_the_deadline_cap() -> None:
    m = _module()
    limits = m.DemoTrialLimits(deadline_seconds=5_000)
    escalated = m.escalate(limits, "deadline")
    assert escalated.deadline_seconds == 7_200
    assert m.is_at_cap(escalated, "deadline")


def test_escalate_stops_at_the_action_output_cap() -> None:
    m = _module()
    limits = m.DemoTrialLimits(action_max_output_tokens=15_000)
    escalated = m.escalate(limits, "action_output_tokens")
    assert escalated.action_max_output_tokens == 16_000
    assert m.is_at_cap(escalated, "action_output_tokens")
    assert not m.is_at_cap(limits, "action_output_tokens")


def test_escalate_stops_at_the_builder_output_cap() -> None:
    m = _module()
    limits = m.DemoTrialLimits(builder_max_output_tokens=100_000)
    escalated = m.escalate(limits, "builder_output_tokens")
    assert escalated.builder_max_output_tokens == 128_000
    assert m.is_at_cap(escalated, "builder_output_tokens")


def test_escalate_wall_time_keys_have_no_explicit_cap() -> None:
    m = _module()
    limits = m.DemoTrialLimits(training_wall_time_ms=10_000_000, heldout_wall_time_ms=10_000_000)
    assert not m.is_at_cap(limits, "training_wall_time")
    assert not m.is_at_cap(limits, "heldout_wall_time")
    escalated = m.escalate(limits, "training_wall_time")
    assert escalated.training_wall_time_ms == 20_000_000


def test_escalate_rejects_a_non_escalatable_key() -> None:
    m = _module()
    with pytest.raises(ValueError):
        m.escalate(m.DemoTrialLimits(), "repair_budget")  # type: ignore[arg-type]


def test_classify_stop_maps_each_limit_to_its_key() -> None:
    m = _module()
    base = dict(
        timed_out=False,
        training_stop_reason="goal_completed",
        builder_accepted=True,
        builder_stop_reason="accepted",
        heldout_stop_reasons=(),
        learning_tokens_spent=0,
        learning_token_budget=1_000_000,
        learning_calls_spent=0,
        learning_call_budget=66,
        action_truncated_count=0,
        builder_truncated_count=0,
    )

    assert m.classify_stop(**{**base, "timed_out": True}) == "deadline"
    assert (
        m.classify_stop(**{**base, "training_stop_reason": "decision_limit"}) == "training_decision"
    )
    assert (
        m.classify_stop(**{**base, "training_stop_reason": "primitive_limit"})
        == "training_primitive"
    )
    assert (
        m.classify_stop(**{**base, "training_stop_reason": "wall_time_limit"})
        == "training_wall_time"
    )
    assert (
        m.classify_stop(
            **{
                **base,
                "builder_accepted": False,
                "builder_stop_reason": "learning_budget_exhausted",
                "learning_tokens_spent": 1_000_000,
            }
        )
        == "learning_token"
    )
    assert (
        m.classify_stop(
            **{
                **base,
                "builder_accepted": False,
                "builder_stop_reason": "learning_budget_exhausted",
                "learning_tokens_spent": 0,
                "learning_calls_spent": 66,
            }
        )
        == "learning_call"
    )
    assert (
        m.classify_stop(
            **{
                **base,
                "builder_accepted": False,
                "builder_stop_reason": "repair_budget_exhausted",
            }
        )
        is None
    )
    assert (
        m.classify_stop(**{**base, "heldout_stop_reasons": ("decision_limit",)})
        == "heldout_decision"
    )
    assert (
        m.classify_stop(**{**base, "heldout_stop_reasons": ("primitive_limit",)})
        == "heldout_primitive"
    )
    assert (
        m.classify_stop(**{**base, "heldout_stop_reasons": ("wall_time_limit",)})
        == "heldout_wall_time"
    )
    assert m.classify_stop(**base) is None


def test_classify_stop_reads_the_truncated_role_from_call_counts() -> None:
    m = _module()
    base = dict(
        timed_out=False,
        training_stop_reason="goal_completed",
        builder_accepted=False,
        builder_stop_reason="truncated_reply",
        heldout_stop_reasons=(),
        learning_tokens_spent=0,
        learning_token_budget=1_000_000,
        learning_calls_spent=0,
        learning_call_budget=66,
    )
    assert (
        m.classify_stop(**base, action_truncated_count=0, builder_truncated_count=1)
        == "builder_output_tokens"
    )
    assert (
        m.classify_stop(**base, action_truncated_count=8, builder_truncated_count=0)
        == "action_output_tokens"
    )
    # A truncated build reply is the literal stop reason: prefer it when both
    # roles show truncated calls.
    assert (
        m.classify_stop(**base, action_truncated_count=8, builder_truncated_count=1)
        == "builder_output_tokens"
    )
    # No call-level evidence at all: still escalate the builder, since that
    # is what the stop reason itself reports.
    assert (
        m.classify_stop(**base, action_truncated_count=0, builder_truncated_count=0)
        == "builder_output_tokens"
    )


def _call(*, purpose: str, finish_reason: str | None) -> object:
    from datetime import UTC, datetime

    from noob_agent.domain.records import ModelCallRecord

    return ModelCallRecord(
        call_id=f"call-{purpose}-{finish_reason}",
        experiment_id="seq-training",
        purpose=purpose,  # type: ignore[arg-type]
        episode_id=("episode-1" if purpose == "action" else None),
        provider="fake",
        model_id="fake-model",
        system="",
        prompt="",
        max_output_tokens=100,
        temperature=0.0,
        response_text="...",
        finish_reason=finish_reason,
        latency_ms=0,
        started_at=datetime(2026, 9, 18, tzinfo=UTC),
    )


def test_truncated_counts_reads_purpose_and_finish_reason() -> None:
    m = _module()
    calls = [
        _call(purpose="action", finish_reason="length"),
        _call(purpose="action", finish_reason="stop"),
        _call(purpose="build", finish_reason="length"),
        _call(purpose="repair", finish_reason="length"),
        _call(purpose="refine", finish_reason="stop"),
    ]
    action_count, builder_count = m._truncated_counts(calls)
    assert action_count == 1
    assert builder_count == 2


def test_task_completed_requires_training_success_accepted_skill_and_heldout_graded() -> None:
    m = _module()
    assert m.task_completed(
        training_stop_reason="goal_completed",
        builder_accepted=True,
        heldout_skipped_reason=None,
        heldout_count=6,
    )
    assert not m.task_completed(
        training_stop_reason="decision_limit",
        builder_accepted=True,
        heldout_skipped_reason=None,
        heldout_count=6,
    )
    assert not m.task_completed(
        training_stop_reason="goal_completed",
        builder_accepted=False,
        heldout_skipped_reason="repair_budget_exhausted",
        heldout_count=0,
    )
    assert not m.task_completed(
        training_stop_reason="goal_completed",
        builder_accepted=True,
        heldout_skipped_reason=None,
        heldout_count=0,
    )


def test_max_escalations_is_four_and_token_cap_is_eight_million() -> None:
    m = _module()
    assert m.MAX_ESCALATIONS == 4
    assert m.TOKEN_BUDGET_CAP == 8_000_000


def test_summary_path_lands_under_git_ignored_noob_agent_dir() -> None:
    m = _module()
    assert m.SUMMARY_DIR == Path(".noob-agent/demo-trials")
    assert m.DEFAULT_DATABASE == Path(".noob-agent/demo-trial.sqlite3")
