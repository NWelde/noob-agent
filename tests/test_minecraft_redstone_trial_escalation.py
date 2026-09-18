"""CLI limits, escalation classification, and doubling for the redstone demo trial.

All tests here exercise pure functions and a fake attempt runner: no model,
connector, database, or asyncio event loop is involved.
"""

import importlib.util
import sys


def load_demo():
    spec = importlib.util.spec_from_file_location(
        "redstone_demo_escalation", "scripts/run_minecraft_redstone_demo.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _record(demo, **overrides):
    fields = dict(
        stop_reasons=frozenset(),
        action_finish_reasons=(),
        builder_finish_reason=None,
        run_status="completed",
        budget_exhausted=None,
        builder_accepted=True,
        reuse_lit=True,
    )
    fields.update(overrides)
    return demo.AttemptRecord(**fields)


# --- CLI defaults stay exactly as today -------------------------------------


def test_default_limits_match_todays_hard_coded_values():
    demo = load_demo()
    assert demo.DEFAULT_LIMITS == demo.Limits(
        decisions=12,
        primitives=24,
        wall_time_ms=120_000,
        action_cap=4_000,
        builder_cap=1_000_000,
        repairs=1,
        deadline_s=600,
        token_ceiling=1_100_000,
        call_budget=30,
    )


def test_no_flags_parse_to_the_default_limits_and_no_trial_or_escalate():
    demo = load_demo()
    args = demo._parse_args([])
    limits = demo._limits_from_args(args)
    assert limits == demo.DEFAULT_LIMITS
    assert args.trial is False
    assert args.escalate is False


def test_trial_flag_selects_the_trial_profile():
    demo = load_demo()
    args = demo._parse_args(["--trial"])
    limits = demo._limits_from_args(args)
    assert limits == demo.Limits(
        decisions=36,
        primitives=72,
        wall_time_ms=360_000,
        action_cap=8_000,
        builder_cap=32_000,
        repairs=3,
        deadline_s=1_800,
        token_ceiling=1_100_000,
        call_budget=90,
    )


def test_escalate_implies_trial():
    demo = load_demo()
    args = demo._parse_args(["--escalate"])
    assert args.trial is True or args.escalate is True
    limits = demo._limits_from_args(args)
    assert limits == demo.TRIAL_LIMITS


def test_an_explicit_flag_overrides_the_trial_default():
    demo = load_demo()
    args = demo._parse_args(["--trial", "--builder-cap", "5000"])
    limits = demo._limits_from_args(args)
    assert limits.builder_cap == 5000
    assert limits.decisions == demo.TRIAL_LIMITS.decisions


# --- classification -----------------------------------------------------


def test_completed_attempt_classifies_to_no_exhausted_limit():
    demo = load_demo()
    record = _record(demo, builder_accepted=True, reuse_lit=True)
    assert demo.attempt_completed(record) is True
    assert demo.classify_exhausted_limit(record) is None


def test_cold_lamp_lit_but_no_skill_is_not_completion():
    demo = load_demo()
    record = _record(demo, builder_accepted=False, reuse_lit=False, run_status="completed")
    assert demo.attempt_completed(record) is False


def test_wall_time_limit_takes_priority():
    demo = load_demo()
    record = _record(
        demo,
        builder_accepted=False,
        reuse_lit=False,
        stop_reasons=frozenset({"wall_time_limit"}),
        run_status="TimeoutError",
        action_finish_reasons=("length", "length"),
    )
    assert demo.classify_exhausted_limit(record) is demo.LimitKind.WALL_TIME


def test_decision_limit_is_classified():
    demo = load_demo()
    record = _record(
        demo, builder_accepted=False, reuse_lit=False, stop_reasons=frozenset({"decision_limit"})
    )
    assert demo.classify_exhausted_limit(record) is demo.LimitKind.DECISION


def test_primitive_limit_is_classified():
    demo = load_demo()
    record = _record(
        demo, builder_accepted=False, reuse_lit=False, stop_reasons=frozenset({"primitive_limit"})
    )
    assert demo.classify_exhausted_limit(record) is demo.LimitKind.PRIMITIVE


def test_action_truncation_on_the_final_call_is_classified():
    demo = load_demo()
    record = _record(
        demo,
        builder_accepted=False,
        reuse_lit=False,
        action_finish_reasons=("stop", "stop", "length"),
    )
    assert demo.classify_exhausted_limit(record) is demo.LimitKind.ACTION_CAP


def test_action_truncation_by_majority_is_classified():
    demo = load_demo()
    record = _record(
        demo,
        builder_accepted=False,
        reuse_lit=False,
        action_finish_reasons=("length", "length", "stop"),
    )
    assert demo.classify_exhausted_limit(record) is demo.LimitKind.ACTION_CAP


def test_a_minority_of_truncated_non_final_calls_is_not_classified_as_action_cap():
    demo = load_demo()
    record = _record(
        demo,
        builder_accepted=False,
        reuse_lit=False,
        action_finish_reasons=("length", "stop", "stop"),
        run_status="unknown_result",
    )
    assert demo.classify_exhausted_limit(record) is None


def test_builder_truncated_reply_is_classified():
    demo = load_demo()
    record = _record(
        demo, builder_accepted=False, reuse_lit=False, builder_finish_reason="length"
    )
    assert demo.classify_exhausted_limit(record) is demo.LimitKind.BUILDER_CAP


def test_whole_run_timeout_is_classified_as_deadline():
    demo = load_demo()
    record = _record(demo, builder_accepted=False, reuse_lit=False, run_status="TimeoutError")
    assert demo.classify_exhausted_limit(record) is demo.LimitKind.DEADLINE


def test_token_budget_exhaustion_is_classified():
    demo = load_demo()
    record = _record(
        demo,
        builder_accepted=False,
        reuse_lit=False,
        run_status="EasyBudgetExhausted",
        budget_exhausted="token",
    )
    assert demo.classify_exhausted_limit(record) is demo.LimitKind.TOKEN_CEILING


def test_call_budget_exhaustion_is_classified():
    demo = load_demo()
    record = _record(
        demo,
        builder_accepted=False,
        reuse_lit=False,
        run_status="EasyBudgetExhausted",
        budget_exhausted="call",
    )
    assert demo.classify_exhausted_limit(record) is demo.LimitKind.CALL_BUDGET


def test_the_live_evidence_record_classifies_as_wall_time():
    """From minecraft-redstone-20260918T081617Z: stop_reason=wall_time_limit after
    6 of 12 decisions, 2 of 6 action calls truncated, Builder never returned, and
    the whole run ended on the 600s asyncio.timeout."""
    demo = load_demo()
    record = _record(
        demo,
        builder_accepted=False,
        reuse_lit=False,
        stop_reasons=frozenset({"wall_time_limit"}),
        action_finish_reasons=("stop", "stop", "stop", "stop", "length", "length"),
        builder_finish_reason=None,
        run_status="TimeoutError",
    )
    assert demo.classify_exhausted_limit(record) is demo.LimitKind.WALL_TIME


# --- doubling, with caps -------------------------------------------------


def test_action_cap_doubles_and_is_capped_at_32000():
    demo = load_demo()
    assert demo.double_limit(demo.LimitKind.ACTION_CAP, 4_000) == 8_000
    assert demo.double_limit(demo.LimitKind.ACTION_CAP, 20_000) == 32_000
    assert demo.double_limit(demo.LimitKind.ACTION_CAP, 32_000) == 32_000


def test_builder_cap_doubles_and_is_capped_at_128000():
    demo = load_demo()
    assert demo.double_limit(demo.LimitKind.BUILDER_CAP, 32_000) == 64_000
    assert demo.double_limit(demo.LimitKind.BUILDER_CAP, 100_000) == 128_000


def test_deadline_doubles_and_is_capped_at_7200():
    demo = load_demo()
    assert demo.double_limit(demo.LimitKind.DEADLINE, 1_800) == 3_600
    assert demo.double_limit(demo.LimitKind.DEADLINE, 5_000) == 7_200


def test_token_ceiling_doubles_and_is_capped_at_8_000_000():
    demo = load_demo()
    assert demo.double_limit(demo.LimitKind.TOKEN_CEILING, 1_100_000) == 2_200_000
    assert demo.double_limit(demo.LimitKind.TOKEN_CEILING, 6_000_000) == 8_000_000


def test_wall_time_decision_primitive_and_call_budget_double_uncapped():
    demo = load_demo()
    assert demo.double_limit(demo.LimitKind.WALL_TIME, 360_000) == 720_000
    assert demo.double_limit(demo.LimitKind.DECISION, 36) == 72
    assert demo.double_limit(demo.LimitKind.PRIMITIVE, 72) == 144
    assert demo.double_limit(demo.LimitKind.CALL_BUDGET, 90) == 180


def test_limits_escalate_doubles_only_the_named_field():
    demo = load_demo()
    limits = demo.TRIAL_LIMITS
    escalated = limits.escalate(demo.LimitKind.WALL_TIME)
    assert escalated.wall_time_ms == limits.wall_time_ms * 2
    assert escalated.decisions == limits.decisions
    assert escalated.action_cap == limits.action_cap
    assert escalated.builder_cap == limits.builder_cap


# --- escalation driver, with a fake single-attempt runner -----------------


def test_escalation_stops_as_soon_as_an_attempt_completes():
    demo = load_demo()
    calls = []

    def run_one(attempt_id, limits):
        calls.append((attempt_id, limits))
        completed = len(calls) == 2
        record = _record(
            demo,
            builder_accepted=completed,
            reuse_lit=completed,
            stop_reasons=frozenset() if completed else frozenset({"wall_time_limit"}),
        )
        return demo.AttemptOutcome(attempt_id, limits, record, {"status": "ok"})

    attempts = demo.run_escalating_attempts(
        run_one, base_run_id="base", limits=demo.TRIAL_LIMITS, escalate=True
    )
    assert [a.attempt_id for a in attempts] == ["base-a01", "base-a02"]
    assert attempts[-1].limits.wall_time_ms == demo.TRIAL_LIMITS.wall_time_ms * 2
    assert demo.attempt_completed(attempts[-1].record) is True


def test_escalation_stops_after_at_most_four_escalations():
    demo = load_demo()

    def run_one(attempt_id, limits):
        record = _record(
            demo, builder_accepted=False, reuse_lit=False,
            stop_reasons=frozenset({"wall_time_limit"}),
        )
        return demo.AttemptOutcome(attempt_id, limits, record, {"status": "ok"})

    attempts = demo.run_escalating_attempts(
        run_one, base_run_id="base", limits=demo.TRIAL_LIMITS, escalate=True
    )
    assert len(attempts) == 5
    assert [a.attempt_id for a in attempts] == [f"base-a{n:02d}" for n in range(1, 6)]


def test_without_escalate_only_one_attempt_runs_with_the_base_run_id():
    demo = load_demo()
    calls = []

    def run_one(attempt_id, limits):
        calls.append(attempt_id)
        record = _record(demo, builder_accepted=False, reuse_lit=False)
        return demo.AttemptOutcome(attempt_id, limits, record, {"status": "ok"})

    attempts = demo.run_escalating_attempts(
        run_one, base_run_id="base", limits=demo.DEFAULT_LIMITS, escalate=False
    )
    assert calls == ["base"]
    assert len(attempts) == 1


def test_an_unclassifiable_non_completion_stops_escalation_early():
    demo = load_demo()

    def run_one(attempt_id, limits):
        record = _record(demo, builder_accepted=False, reuse_lit=False, run_status="mystery")
        return demo.AttemptOutcome(attempt_id, limits, record, {"status": "ok"})

    attempts = demo.run_escalating_attempts(
        run_one, base_run_id="base", limits=demo.TRIAL_LIMITS, escalate=True
    )
    assert len(attempts) == 1
