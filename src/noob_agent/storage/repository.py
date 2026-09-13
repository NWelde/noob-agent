"""The local episode store: SQLite as the source of truth.

Uses the standard library only. Every write runs inside an explicit
transaction, so a rejected row leaves the database exactly as it was. Duplicate
episode and action IDs are refused rather than silently overwritten, because a
recorded attempt must never change after the fact.

Findings, verdicts, and reproductions follow the same rules. A finding is a
public report; its verdict and reproductions are private grader records that
are written once and are never read back into a prompt.

Model call records are diagnostic. Each is written once, after its call, and
nothing in the harness reads one back into a prompt.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from noob_agent.domain.findings import (
    FindingRecord,
    FindingReport,
    FindingVerdict,
    ReplayOutcome,
    ReproductionRecord,
    StoredFinding,
)
from noob_agent.domain.model import ConnectorManifest, Observation, StepResult, ToolRequest
from noob_agent.domain.records import (
    EpisodeOutcome,
    EpisodeRecord,
    ExperimentRecord,
    ModelCallRecord,
    SequenceSummaryRecord,
    StepRecord,
    StoredEpisode,
)
from noob_agent.storage.schema import SCHEMA_STATEMENTS, SCHEMA_VERSION

_RecordT = TypeVar("_RecordT", bound=BaseModel)


class StorageError(RuntimeError):
    """Base class for every durable-record failure."""


class DuplicateRecordError(StorageError):
    """A record with this identity is already recorded and cannot be replaced."""


class UnknownRecordError(StorageError):
    """The record, or the parent it belongs to, is not in the store."""


class InconsistentRecordError(StorageError):
    """The record's own invariants do not hold, so it must not be persisted."""


class EpisodeFinalizedError(StorageError):
    """The episode has already been finalized and can no longer be appended to."""


def _classify(error: sqlite3.IntegrityError, context: str) -> StorageError:
    """Turn a SQLite constraint failure into the store's own error type."""
    message = str(error).upper()
    if "FOREIGN KEY" in message:
        return UnknownRecordError(f"{context} refers to a record that does not exist.")
    if "UNIQUE" in message or "PRIMARY KEY" in message:
        return DuplicateRecordError(f"{context} is already recorded.")
    return StorageError(f"{context} violates a database constraint: {error}")


def _revalidate(record: _RecordT, context: str) -> _RecordT:
    """Re-run a record's own validators before it becomes durable.

    `model_copy(update=...)` and `model_construct` both bypass validation, so a
    caller can hold a record whose invariants no longer hold. Catching that here
    keeps a bad row out of the source of truth, instead of letting it fail much
    later when the episode is read back.
    """
    try:
        return type(record).model_validate(record.model_dump())
    except ValidationError as error:
        raise InconsistentRecordError(f"{context} is not internally consistent: {error}") from error


class EpisodeStore:
    """Durable, append-only local records for experiments, episodes, and findings."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @classmethod
    def open(cls, path: Path | str) -> EpisodeStore:
        """Open (creating if needed) a database and apply the schema."""
        connection = sqlite3.connect(path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        store = cls(connection)
        store.initialize()
        return store

    def initialize(self) -> None:
        """Create the schema if it is not already present, upgrading additively.

        Every table is created with IF NOT EXISTS, so a database at an earlier
        version simply gains the tables it lacks and its version is advanced. A
        database from a newer schema is refused rather than misread.
        """
        with self._transaction():
            for statement in SCHEMA_STATEMENTS:
                self._connection.execute(statement)
            recorded = self._connection.execute("SELECT version FROM schema_version").fetchone()
            if recorded is None:
                self._connection.execute(
                    "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
                )
            elif recorded["version"] > SCHEMA_VERSION:
                raise StorageError(
                    f"The database is at schema version {recorded['version']}, newer than "
                    f"the supported version {SCHEMA_VERSION}."
                )
            elif recorded["version"] < SCHEMA_VERSION:
                self._connection.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> EpisodeStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        """One explicit transaction: every statement lands, or none of them do."""
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        self._connection.execute("COMMIT")

    def create_experiment(self, record: ExperimentRecord) -> None:
        """Record one comparison configuration. Its ID must be unused."""
        record = _revalidate(record, f"Experiment {record.experiment_id!r}")
        with self._transaction():
            try:
                self._connection.execute(
                    """
                    INSERT INTO experiment (
                        experiment_id, model_id, condition, connector_version,
                        decision_budget, primitive_budget, wall_time_budget_ms, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.experiment_id,
                        record.model_id,
                        record.condition,
                        record.connector_version,
                        record.decision_budget,
                        record.primitive_budget,
                        record.wall_time_budget_ms,
                        record.created_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise _classify(error, f"Experiment {record.experiment_id!r}") from error

    def create_episode(self, record: EpisodeRecord) -> None:
        """Open one episode, pinned to the manifest and reset state it began from."""
        record = _revalidate(record, f"Episode {record.episode_id!r}")
        manifest_json = record.manifest.model_dump_json()
        with self._transaction():
            try:
                self._connection.execute(
                    """
                    INSERT INTO episode (
                        episode_id, experiment_id, game_id, scenario_id, seed, split,
                        manifest_json, manifest_hash, reset_observation_json, started_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.episode_id,
                        record.experiment_id,
                        record.game_id,
                        record.scenario_id,
                        record.seed,
                        record.split,
                        manifest_json,
                        manifest_hash(record.manifest),
                        record.reset_observation.model_dump_json(),
                        record.started_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise _classify(error, f"Episode {record.episode_id!r}") from error

    def append_step(self, record: StepRecord) -> None:
        """Append one immutable public step to an open episode."""
        self.append_steps((record,))

    def append_steps(self, records: Iterable[StepRecord]) -> None:
        """Append several steps atomically: all of them land, or none do."""
        validated = [
            _revalidate(record, f"Step {record.sequence} of episode {record.episode_id!r}")
            for record in records
        ]
        with self._transaction():
            for record in validated:
                finalized = self._connection.execute(
                    "SELECT 1 FROM episode_outcome WHERE episode_id = ?", (record.episode_id,)
                ).fetchone()
                if finalized is not None:
                    raise EpisodeFinalizedError(
                        f"Episode {record.episode_id!r} is already finalized "
                        "and cannot accept new steps."
                    )
                try:
                    self._connection.execute(
                        """
                        INSERT INTO step (
                            episode_id, sequence, action_id, request_json, result_json
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            record.episode_id,
                            record.sequence,
                            record.request.action_id,
                            record.request.model_dump_json(),
                            record.result.model_dump_json(),
                        ),
                    )
                except sqlite3.IntegrityError as error:
                    raise _classify(
                        error,
                        f"Step {record.sequence} of episode {record.episode_id!r} "
                        f"(action {record.request.action_id!r})",
                    ) from error

    def finalize_episode(self, outcome: EpisodeOutcome) -> None:
        """Write an episode's final outcome exactly once."""
        outcome = _revalidate(outcome, f"Outcome for episode {outcome.episode_id!r}")
        with self._transaction():
            try:
                self._connection.execute(
                    """
                    INSERT INTO episode_outcome (
                        episode_id, stop_reason, terminal,
                        total_decisions, total_primitives, finished_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        outcome.episode_id,
                        outcome.stop_reason,
                        int(outcome.terminal),
                        outcome.total_decisions,
                        outcome.total_primitives,
                        outcome.finished_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise _classify(error, f"Outcome for episode {outcome.episode_id!r}") from error

    def read_episode(self, episode_id: str) -> StoredEpisode:
        """Read one episode back with its steps in recorded sequence order."""
        row = self._connection.execute(
            "SELECT * FROM episode WHERE episode_id = ?", (episode_id,)
        ).fetchone()
        if row is None:
            raise UnknownRecordError(f"Episode {episode_id!r} is not recorded.")

        try:
            episode = EpisodeRecord(
                episode_id=row["episode_id"],
                experiment_id=row["experiment_id"],
                game_id=row["game_id"],
                scenario_id=row["scenario_id"],
                seed=row["seed"],
                split=row["split"],
                manifest=ConnectorManifest.model_validate_json(row["manifest_json"]),
                reset_observation=Observation.model_validate_json(row["reset_observation_json"]),
                started_at=row["started_at"],
            )

            step_rows = self._connection.execute(
                "SELECT * FROM step WHERE episode_id = ? ORDER BY sequence ASC", (episode_id,)
            ).fetchall()
            steps = tuple(
                StepRecord(
                    episode_id=step_row["episode_id"],
                    sequence=step_row["sequence"],
                    request=ToolRequest.model_validate_json(step_row["request_json"]),
                    result=StepResult.model_validate_json(step_row["result_json"]),
                )
                for step_row in step_rows
            )

            outcome_row = self._connection.execute(
                "SELECT * FROM episode_outcome WHERE episode_id = ?", (episode_id,)
            ).fetchone()
            outcome = (
                None
                if outcome_row is None
                else EpisodeOutcome(
                    episode_id=outcome_row["episode_id"],
                    stop_reason=outcome_row["stop_reason"],
                    terminal=bool(outcome_row["terminal"]),
                    total_decisions=outcome_row["total_decisions"],
                    total_primitives=outcome_row["total_primitives"],
                    finished_at=outcome_row["finished_at"],
                )
            )
        except ValidationError as error:
            raise InconsistentRecordError(
                f"Episode {episode_id!r} has a recorded row that is not internally "
                f"consistent: {error}"
            ) from error

        return StoredEpisode(episode=episode, steps=steps, outcome=outcome)

    def open_episode_id(self, experiment_id: str) -> str | None:
        """The most recently started episode in an experiment with no outcome yet."""
        row = self._connection.execute(
            """
            SELECT episode.episode_id FROM episode
            LEFT JOIN episode_outcome USING (episode_id)
            WHERE episode.experiment_id = ? AND episode_outcome.episode_id IS NULL
            ORDER BY episode.rowid DESC
            LIMIT 1
            """,
            (experiment_id,),
        ).fetchone()
        return None if row is None else str(row["episode_id"])

    # --- Findings and the private grader's records ---------------------------

    def record_finding(self, record: FindingRecord) -> None:
        """Record one reported finding against an existing episode."""
        record = _revalidate(record, f"Finding {record.finding_id!r}")
        with self._transaction():
            try:
                self._connection.execute(
                    """
                    INSERT INTO finding (
                        finding_id, episode_id, action_id, reporting_model_id,
                        report_json, reported_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.finding_id,
                        record.episode_id,
                        record.action_id,
                        record.reporting_model_id,
                        record.report.model_dump_json(),
                        record.reported_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise _classify(error, f"Finding {record.finding_id!r}") from error

    def record_verdict(self, verdict: FindingVerdict) -> None:
        """Write the grader's verdict on a finding exactly once."""
        verdict = _revalidate(verdict, f"Verdict for finding {verdict.finding_id!r}")
        with self._transaction():
            try:
                self._connection.execute(
                    """
                    INSERT INTO finding_verdict (
                        finding_id, verification, reason_code, reason, grader_version, decided_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        verdict.finding_id,
                        verdict.verification,
                        verdict.reason_code,
                        verdict.reason,
                        verdict.grader_version,
                        verdict.decided_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise _classify(error, f"Verdict for finding {verdict.finding_id!r}") from error

    def record_reproduction(self, record: ReproductionRecord) -> None:
        """Record one fresh-reset reproduction attempt for a finding."""
        record = _revalidate(record, f"Reproduction {record.reproduction_id!r}")
        with self._transaction():
            try:
                self._connection.execute(
                    """
                    INSERT INTO reproduction (
                        reproduction_id, finding_id, scenario_id, seed, build, attempted_json,
                        result, predicate_result, first_mismatch, attempted_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.reproduction_id,
                        record.finding_id,
                        record.scenario_id,
                        record.seed,
                        record.build,
                        json.dumps([step.model_dump(mode="json") for step in record.attempted]),
                        record.result,
                        None if record.predicate_result is None else int(record.predicate_result),
                        record.first_mismatch,
                        record.attempted_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise _classify(error, f"Reproduction {record.reproduction_id!r}") from error

    def read_finding(self, finding_id: str) -> StoredFinding:
        """Read one finding with its verdict and reproductions, if any."""
        row = self._connection.execute(
            "SELECT * FROM finding WHERE finding_id = ?", (finding_id,)
        ).fetchone()
        if row is None:
            raise UnknownRecordError(f"Finding {finding_id!r} is not recorded.")
        try:
            finding = self._finding_from_row(row)
            verdict_row = self._connection.execute(
                "SELECT * FROM finding_verdict WHERE finding_id = ?", (finding_id,)
            ).fetchone()
            verdict = (
                None
                if verdict_row is None
                else FindingVerdict(
                    finding_id=verdict_row["finding_id"],
                    verification=verdict_row["verification"],
                    reason_code=verdict_row["reason_code"],
                    reason=verdict_row["reason"],
                    grader_version=verdict_row["grader_version"],
                    decided_at=verdict_row["decided_at"],
                )
            )
            reproduction_rows = self._connection.execute(
                "SELECT * FROM reproduction WHERE finding_id = ? ORDER BY attempted_at ASC, "
                "reproduction_id ASC",
                (finding_id,),
            ).fetchall()
            reproductions = tuple(
                ReproductionRecord(
                    reproduction_id=repro["reproduction_id"],
                    finding_id=repro["finding_id"],
                    scenario_id=repro["scenario_id"],
                    seed=repro["seed"],
                    build=repro["build"],
                    attempted=tuple(
                        ReplayOutcome.model_validate(item)
                        for item in json.loads(repro["attempted_json"])
                    ),
                    result=repro["result"],
                    predicate_result=(
                        None
                        if repro["predicate_result"] is None
                        else bool(repro["predicate_result"])
                    ),
                    first_mismatch=repro["first_mismatch"],
                    attempted_at=repro["attempted_at"],
                )
                for repro in reproduction_rows
            )
        except ValidationError as error:
            raise InconsistentRecordError(
                f"Finding {finding_id!r} has a recorded row that is not internally "
                f"consistent: {error}"
            ) from error
        return StoredFinding(finding=finding, verdict=verdict, reproductions=reproductions)

    def findings_for_episode(self, episode_id: str) -> tuple[FindingRecord, ...]:
        """Every finding reported in one episode, in the order it was reported."""
        rows = self._connection.execute(
            "SELECT * FROM finding WHERE episode_id = ? ORDER BY reported_at ASC, finding_id ASC",
            (episode_id,),
        ).fetchall()
        try:
            return tuple(self._finding_from_row(row) for row in rows)
        except ValidationError as error:
            raise InconsistentRecordError(
                f"Episode {episode_id!r} has a finding row that is not internally "
                f"consistent: {error}"
            ) from error

    # --- Model call records ---------------------------------------------------

    def record_model_call(self, record: ModelCallRecord) -> None:
        """Write one model call record exactly once."""
        record = _revalidate(record, f"Model call {record.call_id!r}")
        with self._transaction():
            try:
                self._connection.execute(
                    """
                    INSERT INTO model_call (
                        call_id, experiment_id, purpose, episode_id, action_id,
                        provider, model_id, system_text, prompt_text,
                        max_output_tokens, temperature, response_text, reasoning,
                        finish_reason, input_tokens, output_tokens, latency_ms,
                        error, started_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.call_id,
                        record.experiment_id,
                        record.purpose,
                        record.episode_id,
                        record.action_id,
                        record.provider,
                        record.model_id,
                        record.system,
                        record.prompt,
                        record.max_output_tokens,
                        record.temperature,
                        record.response_text,
                        record.reasoning,
                        record.finish_reason,
                        record.input_tokens,
                        record.output_tokens,
                        record.latency_ms,
                        record.error,
                        record.started_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise _classify(error, f"Model call {record.call_id!r}") from error

    def read_model_calls(
        self, *, experiment_id: str | None = None, episode_id: str | None = None
    ) -> tuple[ModelCallRecord, ...]:
        """Model call records in the order they were written, optionally filtered."""
        rows = self._connection.execute(
            """
            SELECT * FROM model_call
            WHERE (? IS NULL OR experiment_id = ?) AND (? IS NULL OR episode_id = ?)
            ORDER BY rowid ASC
            """,
            (experiment_id, experiment_id, episode_id, episode_id),
        ).fetchall()
        try:
            return tuple(
                ModelCallRecord(
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
                for row in rows
            )
        except ValidationError as error:
            raise InconsistentRecordError(
                f"A recorded model call is not internally consistent: {error}"
            ) from error

    # --- Learning sequence summaries ---------------------------------------

    def record_sequence_summary(self, record: SequenceSummaryRecord) -> None:
        """Write one immutable, harness-only sequence summary."""
        record = _revalidate(record, f"Sequence summary {record.sequence_id!r}")
        with self._transaction():
            try:
                self._connection.execute(
                    """
                    INSERT INTO sequence_summary VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.sequence_id, record.run_kind, record.training_episode_id,
                        int(record.training_goal_completed), record.builder_stop_reason,
                        int(record.builder_truncated), record.accepted_skill_name,
                        record.accepted_skill_version, record.heldout_total,
                        record.heldout_completed, record.heldout_skipped_reason,
                        record.input_tokens, record.output_tokens, record.finished_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise _classify(error, f"Sequence summary {record.sequence_id!r}") from error

    def read_sequence_summary(self, sequence_id: str) -> SequenceSummaryRecord:
        row = self._connection.execute(
            "SELECT * FROM sequence_summary WHERE sequence_id = ?", (sequence_id,)
        ).fetchone()
        if row is None:
            raise UnknownRecordError(f"Sequence summary {sequence_id!r} is not recorded.")
        try:
            return SequenceSummaryRecord(
                sequence_id=row["sequence_id"], run_kind=row["run_kind"],
                training_episode_id=row["training_episode_id"],
                training_goal_completed=bool(row["training_goal_completed"]),
                builder_stop_reason=row["builder_stop_reason"],
                builder_truncated=bool(row["builder_truncated"]),
                accepted_skill_name=row["accepted_skill_name"],
                accepted_skill_version=row["accepted_skill_version"],
                heldout_total=row["heldout_total"], heldout_completed=row["heldout_completed"],
                heldout_skipped_reason=row["heldout_skipped_reason"],
                input_tokens=row["input_tokens"], output_tokens=row["output_tokens"],
                finished_at=row["finished_at"],
            )
        except ValidationError as error:
            raise InconsistentRecordError(
                f"Sequence summary {sequence_id!r} is internally inconsistent: {error}"
            ) from error

    @staticmethod
    def _finding_from_row(row: sqlite3.Row) -> FindingRecord:
        return FindingRecord(
            finding_id=row["finding_id"],
            episode_id=row["episode_id"],
            action_id=row["action_id"],
            reporting_model_id=row["reporting_model_id"],
            report=FindingReport.model_validate_json(row["report_json"]),
            reported_at=row["reported_at"],
        )


def manifest_hash(manifest: ConnectorManifest) -> str:
    """Fingerprint a manifest so an episode records the tool surface it ran on."""
    return hashlib.sha256(manifest.model_dump_json().encode("utf-8")).hexdigest()
