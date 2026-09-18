"""Run one Doom learning sequence at demo-trial budgets, escalating on a stop.

Build-order step 26.3 from `hackathon_plan.md`: demo trials get large enough
token, call, decision, and primitive limits to actually complete the sample
Doom task, and when a fresh sequence still stops on one of those limits (or
the whole-run safety deadline) before the task completes, this script reruns
a fresh sequence with only the exhausted limit doubled, up to a fixed cap and
a fixed number of escalations.

This is explicitly **not benchmark evidence**. `DemoTrialLimits` never
changes the frozen budget constants in `noob_agent.runtime.sequence` or
`noob_agent.runtime.heldout`; it only supplies larger optional overrides that
those modules already accept. Every attempt is labeled `RUN_KIND` and
recorded in a separate, Git-ignored database under `.noob-agent/`.

Settings come only from the process environment. Load the local `.env` with:

    uv run --env-file .env python scripts/demo_trial.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from noob_agent.connectors.doom import DoomConnector, DoomSettings
from noob_agent.domain.records import StoredEpisode
from noob_agent.grading.doom import DoomEpisodeGrade, DoomPrivateOutcome, grade_doom_episode
from noob_agent.models.client import build_model_client
from noob_agent.observability.tracing import build_trace_sink, close_trace
from noob_agent.runtime.heldout import HELD_OUT_DECISION_BUDGET, HELD_OUT_PRIMITIVE_BUDGET
from noob_agent.runtime.sequence import (
    LEARNING_CALL_BUDGET,
    TRAINING_DECISION_BUDGET,
    TRAINING_PRIMITIVE_BUDGET,
    HeldOutCell,
    LearningSequence,
    LearningSequenceResult,
)
from noob_agent.settings import IntegrationSettings
from noob_agent.skills.executor import build_skill_executor
from noob_agent.skills.registry import SkillRegistry
from noob_agent.storage import EpisodeStore

RUN_KIND = "non-benchmark-demo-trial"
DEFAULT_MANIFEST = Path("scenarios/doom/basic-v1/manifest.json")
DEFAULT_DATABASE = Path(".noob-agent/demo-trial.sqlite3")
SUMMARY_DIR = Path(".noob-agent/demo-trials")

# The section 26.3 escalation caps. Only the token budget and the whole-run
# deadline have an explicit numeric cap; every other limit is bounded solely
# by MAX_ESCALATIONS.
TOKEN_BUDGET_CAP = 8_000_000
DEADLINE_CAP_SECONDS = 7_200.0
MAX_ESCALATIONS = 4

LimitKey = Literal[
    "learning_token",
    "learning_call",
    "training_decision",
    "training_primitive",
    "heldout_decision",
    "heldout_primitive",
    "deadline",
]

_FIELD_FOR_KEY: dict[LimitKey, str] = {
    "learning_token": "learning_token_budget",
    "learning_call": "learning_call_budget",
    "training_decision": "training_decision_budget",
    "training_primitive": "training_primitive_budget",
    "heldout_decision": "heldout_decision_budget",
    "heldout_primitive": "heldout_primitive_budget",
    "deadline": "deadline_seconds",
}

_CAP_FOR_KEY: dict[LimitKey, float] = {
    "learning_token": TOKEN_BUDGET_CAP,
    "deadline": DEADLINE_CAP_SECONDS,
}


@dataclass(frozen=True)
class DemoTrialLimits:
    """One demo trial's runtime limits.

    Every default here is a raised, hard-coded value, never the frozen
    benchmark constant it stands in for: `LearningSequence` and
    `HeldOutRunner` already default to the frozen constants on their own, so
    this dataclass only exists to carry the section 26.3 demo-run limits.
    """

    learning_token_budget: int = 1_000_000
    learning_call_budget: int = LEARNING_CALL_BUDGET * 3
    training_decision_budget: int = TRAINING_DECISION_BUDGET * 3
    training_primitive_budget: int = TRAINING_PRIMITIVE_BUDGET * 3
    heldout_decision_budget: int = HELD_OUT_DECISION_BUDGET * 3
    heldout_primitive_budget: int = HELD_OUT_PRIMITIVE_BUDGET * 3
    deadline_seconds: float = 3_600.0


def escalate(limits: DemoTrialLimits, exhausted: LimitKey) -> DemoTrialLimits:
    """Double only the exhausted limit, capped at the section 26.3 maximum."""
    if exhausted not in _FIELD_FOR_KEY:
        raise ValueError(f"{exhausted!r} is not an escalatable limit.")
    field = _FIELD_FOR_KEY[exhausted]
    current = getattr(limits, field)
    cap = _CAP_FOR_KEY.get(exhausted)
    doubled = current * 2
    new_value = doubled if cap is None else min(doubled, cap)
    return replace(limits, **{field: new_value})


def is_at_cap(limits: DemoTrialLimits, exhausted: LimitKey) -> bool:
    """True when `exhausted` is already at its section 26.3 cap and cannot rise further."""
    cap = _CAP_FOR_KEY.get(exhausted)
    if cap is None:
        return False
    return getattr(limits, _FIELD_FOR_KEY[exhausted]) >= cap


def classify_stop(
    *,
    timed_out: bool,
    training_stop_reason: str,
    builder_accepted: bool,
    builder_stop_reason: str,
    heldout_stop_reasons: Sequence[str],
    learning_tokens_spent: int,
    learning_token_budget: int,
    learning_calls_spent: int,
    learning_call_budget: int,
) -> LimitKey | None:
    """Which limit key, if any, stopped this attempt short of the sample task.

    Returns `None` when nothing here matches a token, call, decision,
    primitive, or deadline limit (for example a repair-budget exhaustion or a
    connector loss): those stops are reported but never escalated.
    """
    if timed_out:
        return "deadline"
    if training_stop_reason == "decision_limit":
        return "training_decision"
    if training_stop_reason == "primitive_limit":
        return "training_primitive"
    if not builder_accepted:
        if builder_stop_reason == "learning_budget_exhausted":
            if learning_tokens_spent >= learning_token_budget:
                return "learning_token"
            if learning_calls_spent >= learning_call_budget:
                return "learning_call"
            # The budget check tripped on the next call's projected cost
            # before either counter reached its own ceiling; tokens are the
            # more common cause, so escalate them first.
            return "learning_token"
        return None
    for reason in heldout_stop_reasons:
        if reason == "decision_limit":
            return "heldout_decision"
        if reason == "primitive_limit":
            return "heldout_primitive"
    return None


def task_completed(
    *,
    training_stop_reason: str,
    builder_accepted: bool,
    heldout_skipped_reason: str | None,
    heldout_count: int,
) -> bool:
    """The sample task is complete: training succeeded, a skill was accepted, and
    every held-out cell was graded."""
    return (
        training_stop_reason in ("goal_completed", "terminal_state")
        and builder_accepted
        and heldout_skipped_reason is None
        and heldout_count > 0
    )


# --- Everything below this line touches a live connector, model, or store. ---


def _cells(manifest_path: Path) -> tuple[HeldOutCell, tuple[HeldOutCell, ...]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    training: list[HeldOutCell] = []
    heldout: list[HeldOutCell] = []
    for scenario_id, scenario in manifest["scenarios"].items():
        cells = [HeldOutCell(scenario_id, int(seed)) for seed in scenario["seeds"]]
        (training if scenario["split"] == "training" else heldout).extend(cells)
    if len(training) != 1:
        raise ValueError("The Doom manifest must declare exactly one training seed.")
    return training[0], tuple(heldout)


def _grade(stored: StoredEpisode, connector: DoomConnector) -> DoomEpisodeGrade:
    outcome = DoomPrivateOutcome.from_connector(
        stored.episode.episode_id, connector.private_outcome()
    )
    return grade_doom_episode(stored, outcome)


def _spent(store: EpisodeStore, sequence_id: str) -> tuple[int, int]:
    """Total input+output tokens and call count under `{sequence_id}-training`.

    Training Action calls and Builder build/repair calls are both recorded
    under the training experiment id (see `LearningSequence._builder`), so
    this one query covers the whole learning budget.
    """
    calls = store.read_model_calls(experiment_id=f"{sequence_id}-training")
    tokens = sum(call.input_tokens + call.output_tokens for call in calls)
    return tokens, len(calls)


def _weave_url(settings: IntegrationSettings, sequence_id: str) -> str | None:
    if not settings.trace.enabled:
        return None
    entity = settings.wandb.entity or "<entity>"
    return (
        f"https://wandb.ai/{entity}/{settings.wandb.project}/weave/calls"
        f"?filter=%7B%22traceRootsOnly%22%3Atrue%7D&peekPath=&query="
        f"{sequence_id}"
    )


@dataclass
class AttemptRecord:
    sequence_id: str
    limits: dict[str, float | int]
    stop_reason: str | None
    completed: bool
    tokens_spent: int
    calls_spent: int
    heldout_grades: list[dict[str, object]]
    weave_url: str | None
    error: str | None = None


async def _run_attempt(
    *,
    sequence_id: str,
    limits: DemoTrialLimits,
    manifest: Path,
    database: Path,
    settings: IntegrationSettings,
) -> tuple[AttemptRecord, LimitKey | None]:
    training, heldout = _cells(manifest)
    executor = build_skill_executor(settings.sandbox)
    trace = build_trace_sink(settings.trace, wandb=settings.wandb)
    timed_out = False
    result: LearningSequenceResult[DoomEpisodeGrade] | None = None
    try:
        with EpisodeStore.open(database) as store:
            sequence = LearningSequence(
                connector_factory=lambda: DoomConnector(DoomSettings()),
                client=build_model_client(settings.model, settings.wandb),
                model_id=settings.model.inference_model or "",
                store=store,
                registry=SkillRegistry(),
                executor=executor,
                grade=_grade,
                trace=trace,
                action_max_output_tokens=settings.model.action_max_output_tokens,
                builder_max_output_tokens=settings.model.builder_max_output_tokens,
                condition=RUN_KIND,
                learning_token_budget=limits.learning_token_budget,
                learning_call_budget=limits.learning_call_budget,
                training_decision_budget=limits.training_decision_budget,
                training_primitive_budget=limits.training_primitive_budget,
                heldout_decision_budget=limits.heldout_decision_budget,
                heldout_primitive_budget=limits.heldout_primitive_budget,
            )
            run = sequence.run(
                sequence_id=sequence_id,
                training_scenario_id=training.scenario_id,
                training_seed=training.seed,
                heldout=heldout,
            )
            try:
                result = await asyncio.wait_for(run, timeout=limits.deadline_seconds)
            except TimeoutError:
                timed_out = True
            tokens_spent, calls_spent = _spent(store, sequence_id)
    finally:
        try:
            close_trace(trace)
        except Exception:  # noqa: BLE001 - tracing must never break the summary
            pass

    weave_url = _weave_url(settings, sequence_id)
    if timed_out or result is None:
        stop_key = classify_stop(
            timed_out=True,
            training_stop_reason="unknown_result",
            builder_accepted=False,
            builder_stop_reason="",
            heldout_stop_reasons=(),
            learning_tokens_spent=tokens_spent,
            learning_token_budget=limits.learning_token_budget,
            learning_calls_spent=calls_spent,
            learning_call_budget=limits.learning_call_budget,
        )
        return (
            AttemptRecord(
                sequence_id=sequence_id,
                limits=asdict(limits),
                stop_reason="deadline",
                completed=False,
                tokens_spent=tokens_spent,
                calls_spent=calls_spent,
                heldout_grades=[],
                weave_url=weave_url,
            ),
            stop_key,
        )

    heldout_reasons = [episode.stop_reason for episode in result.heldout]
    stop_key = classify_stop(
        timed_out=False,
        training_stop_reason=result.training.stop_reason,
        builder_accepted=result.builder.accepted,
        builder_stop_reason=result.builder.stop_reason,
        heldout_stop_reasons=heldout_reasons,
        learning_tokens_spent=tokens_spent,
        learning_token_budget=limits.learning_token_budget,
        learning_calls_spent=calls_spent,
        learning_call_budget=limits.learning_call_budget,
    )
    completed = task_completed(
        training_stop_reason=result.training.stop_reason,
        builder_accepted=result.builder.accepted,
        heldout_skipped_reason=result.heldout_skipped_reason,
        heldout_count=len(result.heldout),
    )
    overall_stop_reason = (
        "completed"
        if completed
        else (result.heldout_skipped_reason or result.training.stop_reason)
    )
    record = AttemptRecord(
        sequence_id=sequence_id,
        limits=asdict(limits),
        stop_reason=overall_stop_reason,
        completed=completed,
        tokens_spent=tokens_spent,
        calls_spent=calls_spent,
        heldout_grades=[
            {
                "episode_id": episode.episode_id,
                "scenario_id": episode.scenario_id,
                "seed": episode.seed,
                "stop_reason": episode.stop_reason,
                "goal_completed": episode.grade.goal_completed,
            }
            for episode in result.heldout
        ],
        weave_url=weave_url,
    )
    return record, (None if completed else stop_key)


async def run_demo_trial(
    *,
    manifest: Path = DEFAULT_MANIFEST,
    database: Path = DEFAULT_DATABASE,
    run_id: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    environment = os.environ if environ is None else environ
    settings = IntegrationSettings.from_environ(environment)
    database.parent.mkdir(parents=True, exist_ok=True)
    run_id = run_id or datetime.now(UTC).strftime("demo-trial-%Y%m%dT%H%M%SZ")

    limits = DemoTrialLimits()
    attempts: list[AttemptRecord] = []
    for attempt_number in range(1, MAX_ESCALATIONS + 2):
        sequence_id = f"{run_id}-a{attempt_number:02d}"
        record, exhausted = await _run_attempt(
            sequence_id=sequence_id,
            limits=limits,
            manifest=manifest,
            database=database,
            settings=settings,
        )
        attempts.append(record)
        if exhausted is None:
            break
        if len(attempts) > MAX_ESCALATIONS:
            break
        if is_at_cap(limits, exhausted):
            break
        limits = escalate(limits, exhausted)

    summary = {
        "run_kind": RUN_KIND,
        "run_id": run_id,
        "database": str(database),
        "completed": attempts[-1].completed if attempts else False,
        "attempts": [asdict(attempt) for attempt in attempts],
    }
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    summary_path = SUMMARY_DIR / f"{run_id}.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    summary["summary_path"] = str(summary_path)
    return summary


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--run-id", default=None)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    summary = asyncio.run(
        run_demo_trial(manifest=args.manifest, database=args.database, run_id=args.run_id)
    )
    print(json.dumps(summary, indent=2))
    return 0 if summary["completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
