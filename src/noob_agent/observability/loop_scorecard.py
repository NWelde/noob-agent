"""Loop scorecard: what one learning sequence cost and how well its loop worked.

This is the measuring instrument for `hackathon_plan.md` section 22. It reads a
recorded database through a read-only connection, so scoring never changes a
recorded run and works on databases written by newer schema versions. It is
diagnostic only: nothing here is read back into a prompt, a skill decision, or
a grade.

A database alone does not hold grades or the Builder's verdict. Scores built
from a database therefore report grades as unknown and infer acceptance from
whether held-out episodes ran, and say so with `acceptance_source`. The bench
passes in-process results to fill both in.
"""

from __future__ import annotations

import math
import sqlite3
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from noob_agent.agents.action import UNUSABLE_REPLY_TOOL
from noob_agent.domain.model import ConnectorManifest, Observation, StepResult, ToolRequest
from noob_agent.domain.records import (
    EpisodeOutcome,
    EpisodeRecord,
    ModelCallRecord,
    StepRecord,
    StoredEpisode,
)

TRAINING_SUFFIX = "-training"
HELDOUT_SUFFIX = "-heldout"

# Per-sequence learning ceilings from eval_protocol.md, Budgets.
TRAINING_ACTION_TOKEN_CEILING = 40_000
BUILD_TOKEN_CEILING = 12_000
REPAIR_TOKEN_CEILING = 8_000
LEARNING_TOKEN_CEILING = 60_000
LEARNING_CALL_CEILING = 22

CAPPED_FINISH_REASON = "length"


class ScoreModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ActionStats(ScoreModel):
    calls: int
    decisions: int
    unusable_replies: int
    capped_calls: int
    unknown_usage_calls: int
    latency_p50_ms: int | None
    latency_p90_ms: int | None
    input_tokens: int
    output_tokens: int


class BuilderStats(ScoreModel):
    builds: int
    repairs: int
    capped_calls: int
    input_tokens: int
    output_tokens: int
    latency_ms: int
    accepted: bool | None
    stop_reason: str | None


class EpisodeStats(ScoreModel):
    episode_id: str
    scenario_id: str
    seed: int
    split: str
    stop_reason: str | None
    decisions: int
    primitives: int
    goal_completed: bool | None
    skill_uses: int | None


class SequenceScore(ScoreModel):
    sequence_id: str
    acceptance_source: Literal["result", "inferred"]
    training: EpisodeStats | None
    heldout: tuple[EpisodeStats, ...]
    action: ActionStats
    builder: BuilderStats
    wall_time_seconds: float | None
    learning_calls: int
    learning_tokens: int
    ceilings_exceeded: tuple[str, ...]


class RunTotals(ScoreModel):
    sequences: int
    sequences_with_accepted_skill: int
    action_calls: int
    action_decisions: int
    unusable_replies: int
    unusable_rate: float | None
    capped_action_calls: int
    action_latency_p50_ms: int | None
    action_latency_p90_ms: int | None
    builder_calls: int
    capped_builder_calls: int
    median_wall_time_seconds: float | None
    max_learning_tokens: int
    sequences_over_a_ceiling: int
    heldout_episodes: int
    heldout_goal_completed: int | None


class RunScore(ScoreModel):
    sequences: tuple[SequenceScore, ...]
    totals: RunTotals
    # Pooled Action latencies, kept so run totals are exact rather than averaged.
    action_latencies_ms: tuple[int, ...] = ()


@dataclass(frozen=True)
class SequenceRecords:
    """Everything one sequence recorded, grouped by its experiment IDs."""

    sequence_id: str
    training: tuple[StoredEpisode, ...]
    heldout: tuple[StoredEpisode, ...]
    model_calls: tuple[ModelCallRecord, ...]


@dataclass(frozen=True)
class SequenceOutcome:
    """In-process facts a database does not hold."""

    builder_accepted: bool
    builder_stop_reason: str
    grades: Mapping[str, bool] = field(default_factory=dict)
    skill_uses: Mapping[str, int] = field(default_factory=dict)


def load_sequences(database: Path) -> tuple[SequenceRecords, ...]:
    """Read every `<sequence>-training` / `<sequence>-heldout` pair, read-only."""
    if not database.is_file():
        raise FileNotFoundError(database)
    connection = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        experiment_ids = [
            str(row["experiment_id"])
            for row in connection.execute("SELECT experiment_id FROM experiment ORDER BY rowid")
        ]
        sequence_ids: list[str] = []
        for experiment_id in experiment_ids:
            for suffix in (TRAINING_SUFFIX, HELDOUT_SUFFIX):
                if experiment_id.endswith(suffix):
                    sequence_id = experiment_id[: -len(suffix)]
                    if sequence_id and sequence_id not in sequence_ids:
                        sequence_ids.append(sequence_id)
        return tuple(_read_sequence(connection, sequence_id) for sequence_id in sequence_ids)
    finally:
        connection.close()


def _read_sequence(connection: sqlite3.Connection, sequence_id: str) -> SequenceRecords:
    experiments = (sequence_id + TRAINING_SUFFIX, sequence_id + HELDOUT_SUFFIX)
    # Schema versions before 3 have no model_call table.
    has_calls = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'model_call'"
    ).fetchone()
    calls = (
        tuple(
            _model_call(row)
            for row in connection.execute(
                "SELECT * FROM model_call WHERE experiment_id IN (?, ?) ORDER BY rowid",
                experiments,
            )
        )
        if has_calls
        else ()
    )
    return SequenceRecords(
        sequence_id=sequence_id,
        training=_read_episodes(connection, experiments[0]),
        heldout=_read_episodes(connection, experiments[1]),
        model_calls=calls,
    )


def _read_episodes(connection: sqlite3.Connection, experiment_id: str) -> tuple[StoredEpisode, ...]:
    episodes = []
    for row in connection.execute(
        "SELECT * FROM episode WHERE experiment_id = ? ORDER BY rowid", (experiment_id,)
    ):
        episode_id = row["episode_id"]
        steps = tuple(
            StepRecord(
                episode_id=step["episode_id"],
                sequence=step["sequence"],
                request=ToolRequest.model_validate_json(step["request_json"]),
                result=StepResult.model_validate_json(step["result_json"]),
            )
            for step in connection.execute(
                "SELECT * FROM step WHERE episode_id = ? ORDER BY sequence", (episode_id,)
            )
        )
        outcome_row = connection.execute(
            "SELECT * FROM episode_outcome WHERE episode_id = ?", (episode_id,)
        ).fetchone()
        episodes.append(
            StoredEpisode(
                episode=EpisodeRecord(
                    episode_id=episode_id,
                    experiment_id=row["experiment_id"],
                    game_id=row["game_id"],
                    scenario_id=row["scenario_id"],
                    seed=row["seed"],
                    split=row["split"],
                    manifest=ConnectorManifest.model_validate_json(row["manifest_json"]),
                    reset_observation=Observation.model_validate_json(
                        row["reset_observation_json"]
                    ),
                    started_at=row["started_at"],
                ),
                steps=steps,
                outcome=None
                if outcome_row is None
                else EpisodeOutcome(
                    episode_id=outcome_row["episode_id"],
                    stop_reason=outcome_row["stop_reason"],
                    terminal=bool(outcome_row["terminal"]),
                    total_decisions=outcome_row["total_decisions"],
                    total_primitives=outcome_row["total_primitives"],
                    finished_at=outcome_row["finished_at"],
                ),
            )
        )
    return tuple(episodes)


def _model_call(row: sqlite3.Row) -> ModelCallRecord:
    return ModelCallRecord(
        call_id=row["call_id"],
        experiment_id=row["experiment_id"],
        purpose=row["purpose"],
        episode_id=row["episode_id"],
        action_id=row["action_id"],
        provider=row["provider"],
        model_id=row["model_id"],
        system=row["system_text"],
        prompt=row["prompt_text"],
        max_output_tokens=row["max_output_tokens"],
        temperature=row["temperature"],
        response_text=row["response_text"],
        reasoning=row["reasoning"],
        finish_reason=row["finish_reason"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        latency_ms=row["latency_ms"],
        error=row["error"],
        started_at=row["started_at"],
    )


def _percentile(values: Sequence[int], fraction: float) -> int | None:
    """Nearest-rank percentile, so every reported value is an observed one."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def _usage_unknown(call: ModelCallRecord) -> bool:
    # A failed call never returned usage; only a returned call with none is unknown.
    return call.error is None and call.input_tokens is None


def _tokens(call: ModelCallRecord) -> int:
    return (call.input_tokens or 0) + (call.output_tokens or 0)


def _episode_stats(stored: StoredEpisode, outcome: SequenceOutcome | None) -> EpisodeStats:
    episode_id = stored.episode.episode_id
    finished = stored.outcome
    return EpisodeStats(
        episode_id=episode_id,
        scenario_id=stored.episode.scenario_id,
        seed=stored.episode.seed,
        split=stored.episode.split,
        stop_reason=None if finished is None else finished.stop_reason,
        decisions=0 if finished is None else finished.total_decisions,
        primitives=0 if finished is None else finished.total_primitives,
        goal_completed=None if outcome is None else outcome.grades.get(episode_id),
        skill_uses=None if outcome is None else outcome.skill_uses.get(episode_id),
    )


def score_sequence(
    records: SequenceRecords, outcome: SequenceOutcome | None = None
) -> SequenceScore:
    """Score one sequence from its records and, when known, its in-process outcome."""
    episodes = records.training + records.heldout
    action_calls = [call for call in records.model_calls if call.purpose == "action"]
    builder_calls = [call for call in records.model_calls if call.purpose != "action"]
    training_experiment = records.sequence_id + TRAINING_SUFFIX
    training_action_calls = [
        call for call in action_calls if call.experiment_id == training_experiment
    ]

    action = ActionStats(
        calls=len(action_calls),
        decisions=sum(e.outcome.total_decisions for e in episodes if e.outcome is not None),
        unusable_replies=sum(
            1 for e in episodes for step in e.steps if step.request.tool_name == UNUSABLE_REPLY_TOOL
        ),
        capped_calls=sum(1 for call in action_calls if call.finish_reason == CAPPED_FINISH_REASON),
        unknown_usage_calls=sum(1 for call in action_calls if _usage_unknown(call)),
        latency_p50_ms=_percentile([call.latency_ms for call in action_calls], 0.5),
        latency_p90_ms=_percentile([call.latency_ms for call in action_calls], 0.9),
        input_tokens=sum(call.input_tokens or 0 for call in action_calls),
        output_tokens=sum(call.output_tokens or 0 for call in action_calls),
    )

    if outcome is not None:
        accepted: bool | None = outcome.builder_accepted
        stop_reason: str | None = outcome.builder_stop_reason
        source: Literal["result", "inferred"] = "result"
    else:
        accepted = True if records.heldout else (False if builder_calls else None)
        stop_reason = None
        source = "inferred"
    builder = BuilderStats(
        builds=sum(1 for call in builder_calls if call.purpose == "build"),
        repairs=sum(1 for call in builder_calls if call.purpose == "repair"),
        capped_calls=sum(1 for call in builder_calls if call.finish_reason == CAPPED_FINISH_REASON),
        input_tokens=sum(call.input_tokens or 0 for call in builder_calls),
        output_tokens=sum(call.output_tokens or 0 for call in builder_calls),
        latency_ms=sum(call.latency_ms for call in builder_calls),
        accepted=accepted,
        stop_reason=stop_reason,
    )

    learning = training_action_calls + builder_calls
    learning_tokens = sum(_tokens(call) for call in learning)
    exceeded: list[str] = []
    if sum(_tokens(call) for call in training_action_calls) > TRAINING_ACTION_TOKEN_CEILING:
        exceeded.append("training_action_tokens")
    if any(_tokens(c) > BUILD_TOKEN_CEILING for c in builder_calls if c.purpose == "build"):
        exceeded.append("build_tokens")
    if any(_tokens(c) > REPAIR_TOKEN_CEILING for c in builder_calls if c.purpose == "repair"):
        exceeded.append("repair_tokens")
    if learning_tokens > LEARNING_TOKEN_CEILING:
        exceeded.append("learning_tokens")
    if len(learning) > LEARNING_CALL_CEILING:
        exceeded.append("learning_calls")
    if any(_usage_unknown(call) for call in records.model_calls):
        exceeded.append("usage_unknown")

    return SequenceScore(
        sequence_id=records.sequence_id,
        acceptance_source=source,
        training=_episode_stats(records.training[0], outcome) if records.training else None,
        heldout=tuple(_episode_stats(stored, outcome) for stored in records.heldout),
        action=action,
        builder=builder,
        wall_time_seconds=_wall_time(episodes, records.model_calls),
        learning_calls=len(learning),
        learning_tokens=learning_tokens,
        ceilings_exceeded=tuple(exceeded),
    )


def _wall_time(episodes: Iterable[StoredEpisode], calls: Iterable[ModelCallRecord]) -> float | None:
    starts = []
    ends = []
    for stored in episodes:
        starts.append(stored.episode.started_at.timestamp())
        if stored.outcome is not None:
            ends.append(stored.outcome.finished_at.timestamp())
    for call in calls:
        started = call.started_at.timestamp()
        starts.append(started)
        ends.append(started + call.latency_ms / 1000)
    if not starts or not ends:
        return None
    return round(max(ends) - min(starts), 3)


def score_run(
    sequences: Sequence[SequenceScore], action_latencies_ms: Sequence[int] = ()
) -> RunScore:
    """Pool sequence scores into run totals."""
    decisions = sum(score.action.decisions for score in sequences)
    unusable = sum(score.action.unusable_replies for score in sequences)
    walls = [score.wall_time_seconds for score in sequences if score.wall_time_seconds is not None]
    heldout = [episode for score in sequences for episode in score.heldout]
    grades = [episode.goal_completed for episode in heldout]
    latencies = tuple(action_latencies_ms)
    return RunScore(
        sequences=tuple(sequences),
        action_latencies_ms=latencies,
        totals=RunTotals(
            sequences=len(sequences),
            sequences_with_accepted_skill=sum(
                1 for score in sequences if score.builder.accepted is True
            ),
            action_calls=sum(score.action.calls for score in sequences),
            action_decisions=decisions,
            unusable_replies=unusable,
            unusable_rate=None if decisions == 0 else unusable / decisions,
            capped_action_calls=sum(score.action.capped_calls for score in sequences),
            action_latency_p50_ms=_percentile(latencies, 0.5)
            if latencies
            else _median_or_none([s.action.latency_p50_ms for s in sequences]),
            action_latency_p90_ms=_percentile(latencies, 0.9)
            if latencies
            else _max_or_none([s.action.latency_p90_ms for s in sequences]),
            builder_calls=sum(s.builder.builds + s.builder.repairs for s in sequences),
            capped_builder_calls=sum(score.builder.capped_calls for score in sequences),
            median_wall_time_seconds=statistics.median(walls) if walls else None,
            max_learning_tokens=max((score.learning_tokens for score in sequences), default=0),
            sequences_over_a_ceiling=sum(1 for score in sequences if score.ceilings_exceeded),
            heldout_episodes=len(heldout),
            heldout_goal_completed=None
            if any(grade is None for grade in grades)
            else sum(1 for grade in grades if grade),
        ),
    )


def run_latencies(records: Iterable[SequenceRecords]) -> tuple[int, ...]:
    """Every Action call latency across sequences, for exact pooled percentiles."""
    return tuple(
        call.latency_ms
        for sequence in records
        for call in sequence.model_calls
        if call.purpose == "action"
    )


def _median_or_none(values: Sequence[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return int(statistics.median(present)) if present else None


def _max_or_none(values: Sequence[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _format(value: object) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, float):
        return f"{value:.3g}"
    return str(value)


def render_markdown(score: RunScore, *, title: str) -> str:
    """A human-readable scorecard with the section 22 target metrics first."""
    totals = score.totals
    rate = None if totals.unusable_rate is None else f"{totals.unusable_rate:.1%}"
    lines = [
        f"# {title}",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Unusable Action replies | {totals.unusable_replies} of {totals.action_decisions}"
        f" ({_format(rate)}) |",
        f"| Action calls ending at their cap | {totals.capped_action_calls} of"
        f" {totals.action_calls} |",
        f"| Action decision latency p50 / p90 (ms) | {_format(totals.action_latency_p50_ms)} /"
        f" {_format(totals.action_latency_p90_ms)} |",
        f"| Sequences with an accepted skill | {totals.sequences_with_accepted_skill} of"
        f" {totals.sequences} |",
        f"| Builder calls ending at their cap | {totals.capped_builder_calls} of"
        f" {totals.builder_calls} |",
        f"| Sequence wall time, median (s) | {_format(totals.median_wall_time_seconds)} |",
        f"| Learning tokens, largest sequence | {totals.max_learning_tokens} |",
        f"| Sequences over a protocol ceiling | {totals.sequences_over_a_ceiling} |",
        f"| Held-out goals completed | {_format(totals.heldout_goal_completed)} of"
        f" {totals.heldout_episodes} |",
        "",
        "| Sequence | Accepted | Training stop | Unusable | Capped Action | Builder"
        " calls (capped) | Wall s | Learning tokens | Over ceilings | Held-out done |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for sequence in score.sequences:
        heldout_done = [episode.goal_completed for episode in sequence.heldout]
        done = (
            "none run"
            if not heldout_done
            else "unknown"
            if any(value is None for value in heldout_done)
            else f"{sum(1 for value in heldout_done if value)} of {len(heldout_done)}"
        )
        accepted = _format(sequence.builder.accepted)
        if sequence.acceptance_source == "inferred":
            accepted += " (inferred)"
        lines.append(
            f"| {sequence.sequence_id} | {accepted} |"
            f" {_format(None if sequence.training is None else sequence.training.stop_reason)} |"
            f" {sequence.action.unusable_replies}/{sequence.action.decisions} |"
            f" {sequence.action.capped_calls}/{sequence.action.calls} |"
            f" {sequence.builder.builds + sequence.builder.repairs}"
            f" ({sequence.builder.capped_calls}) |"
            f" {_format(sequence.wall_time_seconds)} | {sequence.learning_tokens} |"
            f" {', '.join(sequence.ceilings_exceeded) or 'none'} | {done} |"
        )
    lines.append("")
    return "\n".join(lines)
