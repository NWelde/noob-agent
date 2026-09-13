"""One learning sequence for one model: cold training, the Builder, held-out reuse.

This is build-order step 8 from `hackathon_plan.md` section 17. The sequence is
game-neutral: a connector factory supplies a fresh connector for every episode,
and a grading callback scores each episode only after it has finished. It does
not compare models or conditions, aggregate results, or write a report.

Separation holds by construction. The Builder sees only public evidence from
the cold training episode. Held-out episodes run through `HeldOutRunner`, which
has no path to the Builder or to validation. Grades are returned to the caller
and never passed to a model client.

Every model call is recorded. Each role gets its own `RecordingModelClient`,
which writes one Model call record per call to the store and mirrors it to the
trace sink; the agents never see those records.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from typing import Generic, TypeVar

from noob_agent.agents.action import ActionAgent
from noob_agent.agents.builder import BuilderAgent, BuilderOutcome
from noob_agent.agents.evidence import select_evidence
from noob_agent.connectors.protocol import GameConnector
from noob_agent.domain.records import (
    EpisodeSplit,
    ExperimentRecord,
    ModelCallPurpose,
    StopReason,
    StoredEpisode,
)
from noob_agent.domain.skills import SkillVersion
from noob_agent.models.client import ModelClient
from noob_agent.models.recording import ModelRole, RecordingModelClient
from noob_agent.observability.tracing import TraceSink
from noob_agent.prompts.builder import DEFAULT_REPAIR_MAX_OUTPUT_TOKENS
from noob_agent.runtime.heldout import HeldOutRunner, heldout_experiment
from noob_agent.runtime.runner import Clock, EpisodeRunner, SystemClock
from noob_agent.settings import (
    DEFAULT_ACTION_MAX_OUTPUT_TOKENS,
    DEFAULT_BUILDER_MAX_OUTPUT_TOKENS,
)
from noob_agent.skills.executor import SkillExecutor
from noob_agent.skills.registry import SkillRegistry
from noob_agent.storage import EpisodeStore

# The training attempt budgets from connector_contract.md.
TRAINING_DECISION_BUDGET = 20
TRAINING_PRIMITIVE_BUDGET = 40
TRAINING_WALL_TIME_MS = 180_000

# The per-sequence learning budget from eval_protocol.md: training Action calls
# plus every Build and Repair call.
LEARNING_TOKEN_BUDGET = 60_000
LEARNING_CALL_BUDGET = 22
# Validation executions per candidate that may run at once.
VALIDATION_CONCURRENCY = 8

ConnectorT = TypeVar("ConnectorT", bound=GameConnector)
GradeT = TypeVar("GradeT")


def training_experiment(
    *,
    experiment_id: str,
    model_id: str,
    connector_version: str,
    created_at: datetime,
    condition: str = "self-improving",
) -> ExperimentRecord:
    """An experiment record carrying exactly the frozen training budgets."""
    return ExperimentRecord(
        experiment_id=experiment_id,
        model_id=model_id,
        condition=condition,
        connector_version=connector_version,
        decision_budget=TRAINING_DECISION_BUDGET,
        primitive_budget=TRAINING_PRIMITIVE_BUDGET,
        wall_time_budget_ms=TRAINING_WALL_TIME_MS,
        created_at=created_at,
    )


@dataclass(frozen=True)
class HeldOutCell:
    """One held-out scenario and precommitted seed."""

    scenario_id: str
    seed: int


@dataclass(frozen=True)
class TrainingEpisodeReport(Generic[GradeT]):
    episode_id: str
    scenario_id: str
    seed: int
    stop_reason: StopReason
    decisions_used: int
    primitives_used: int
    grade: GradeT


@dataclass(frozen=True)
class HeldOutEpisodeReport(Generic[GradeT]):
    episode_id: str
    scenario_id: str
    seed: int
    stop_reason: StopReason
    decisions_used: int
    primitives_used: int
    offered_skills: tuple[str, ...]
    skill_uses: int
    grade: GradeT
    input_tokens: int = 0
    output_tokens: int = 0
    decisions_by_model: int = 0
    # Each skill use as "status: summary", public results only.
    skill_results: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Trained(Generic[GradeT]):
    """Cold training and the first Builder run, with the learning spend so far."""

    manifest_tools: tuple[str, ...]
    connector_version: str
    training_record: ExperimentRecord
    heldout_record: ExperimentRecord
    training: TrainingEpisodeReport[GradeT]
    training_stored: StoredEpisode
    outcome: BuilderOutcome
    spent_tokens: int
    spent_calls: int


@dataclass(frozen=True)
class LearningSequenceResult(Generic[GradeT]):
    sequence_id: str
    model_id: str
    training_experiment: ExperimentRecord
    heldout_experiment: ExperimentRecord
    training: TrainingEpisodeReport[GradeT]
    builder: BuilderOutcome
    accepted_version: SkillVersion | None
    heldout: tuple[HeldOutEpisodeReport[GradeT], ...]
    heldout_skipped_reason: str | None
    skill_isolation_note: str


class LearningSequence(Generic[ConnectorT, GradeT]):
    """Runs one cold training episode, one Builder run, and the held-out episodes."""

    def __init__(
        self,
        *,
        connector_factory: Callable[[], ConnectorT],
        client: ModelClient,
        model_id: str,
        store: EpisodeStore,
        registry: SkillRegistry,
        executor: SkillExecutor,
        grade: Callable[[StoredEpisode, ConnectorT], GradeT],
        clock: Clock | None = None,
        trace: TraceSink | None = None,
        max_repairs: int = 1,
        action_max_output_tokens: int = DEFAULT_ACTION_MAX_OUTPUT_TOKENS,
        builder_max_output_tokens: int = DEFAULT_BUILDER_MAX_OUTPUT_TOKENS,
        condition: str = "self-improving",
        persistent_connector: bool = False,
        action_thinking: bool | None = None,
        builder_thinking: bool | None = None,
        heldout_concurrency: int = 1,
        validation_concurrency: int = VALIDATION_CONCURRENCY,
    ) -> None:
        if heldout_concurrency < 1:
            raise ValueError("heldout_concurrency must be at least 1.")
        if persistent_connector and heldout_concurrency > 1:
            raise ValueError(
                "A persistent connector plays one episode at a time; heldout_concurrency must be 1."
            )
        self._connector_factory = connector_factory
        self._client = client
        self._model_id = model_id
        self._store = store
        self._registry = registry
        self._executor = executor
        self._grade = grade
        self._clock = clock if clock is not None else SystemClock()
        self._trace = trace
        self._max_repairs = max_repairs
        self._action_max_output_tokens = action_max_output_tokens
        self._builder_max_output_tokens = builder_max_output_tokens
        self._condition = condition
        self._action_thinking = action_thinking
        self._builder_thinking = builder_thinking
        self._heldout_concurrency = heldout_concurrency
        self._validation_concurrency = validation_concurrency
        # One game for the whole sequence: reset per episode, closed once at the end.
        self._persistent_connector = persistent_connector
        self._learning_token_budget = LEARNING_TOKEN_BUDGET
        self._learning_call_budget = LEARNING_CALL_BUDGET

    def _recording(
        self, experiment: ExperimentRecord, *, role: ModelRole, episode_id: str | None = None
    ) -> RecordingModelClient:
        return RecordingModelClient(
            self._client,
            store=self._store,
            experiment_id=experiment.experiment_id,
            role=role,
            model_id=self._model_id,
            episode_id=episode_id,
            clock=self._clock,
            trace=self._trace,
        )

    async def _run_cell(
        self,
        connector: ConnectorT,
        record: ExperimentRecord,
        cell: HeldOutCell,
        *,
        split: EpisodeSplit = "held-out",
        offered: Sequence[SkillVersion] | None = None,
    ) -> HeldOutEpisodeReport[GradeT]:
        heldout_connector = connector if self._persistent_connector else self._connector_factory()
        # Several held-out episodes of one experiment may be open at once, so each
        # cell's model calls are attributed to the episode its own runner opened.
        opened: list[str] = []
        runner = HeldOutRunner(
            connector=heldout_connector,
            store=self._store,
            registry=self._registry,
            client=RecordingModelClient(
                self._client,
                store=self._store,
                experiment_id=record.experiment_id,
                role="action",
                model_id=self._model_id,
                clock=self._clock,
                trace=self._trace,
                episode_source=lambda: opened[-1] if opened else None,
            ),
            executor=self._executor,
            clock=self._clock,
            trace=self._trace,
            action_max_output_tokens=self._action_max_output_tokens,
            action_thinking=self._action_thinking,
            close_connector=not self._persistent_connector,
            on_episode_started=opened.append,
            flush_trace=self._heldout_concurrency == 1,
            offered=offered,
        )
        result = await runner.run(
            experiment=record, scenario_id=cell.scenario_id, seed=cell.seed, split=split
        )
        stored = self._store.read_episode(result.episode.episode_id)
        return HeldOutEpisodeReport(
            episode_id=result.episode.episode_id,
            scenario_id=cell.scenario_id,
            seed=cell.seed,
            stop_reason=result.episode.stop_reason,
            decisions_used=result.episode.decisions_used,
            primitives_used=result.episode.primitives_used,
            offered_skills=tuple(skill.name for skill in result.offered_skills),
            skill_uses=len(result.skill_uses),
            grade=self._grade(stored, heldout_connector),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            decisions_by_model=len(result.decisions),
            skill_results=tuple(
                f"{use.status}: {use.result.summary if use.result is not None else use.message}"
                for use in result.skill_uses
            ),
        )

    async def _run_cells(
        self,
        connector: ConnectorT,
        record: ExperimentRecord,
        cells: Sequence[HeldOutCell],
        *,
        split: EpisodeSplit = "held-out",
        offered: Sequence[SkillVersion] | None = None,
    ) -> tuple[HeldOutEpisodeReport[GradeT], ...]:
        gate = asyncio.Semaphore(self._heldout_concurrency)

        async def run_cell(cell: HeldOutCell) -> HeldOutEpisodeReport[GradeT]:
            async with gate:
                return await self._run_cell(connector, record, cell, split=split, offered=offered)

        try:
            return tuple(await asyncio.gather(*(run_cell(cell) for cell in cells)))
        finally:
            if self._heldout_concurrency > 1 and self._trace is not None:
                with suppress(Exception):
                    self._trace.flush()

    def _builder(
        self,
        training_record: ExperimentRecord,
        episode_id: str,
        *,
        first_purpose: ModelCallPurpose = "build",
    ) -> BuilderAgent:
        return BuilderAgent(
            RecordingModelClient(
                self._client,
                store=self._store,
                experiment_id=training_record.experiment_id,
                role="builder",
                model_id=self._model_id,
                episode_id=episode_id,
                clock=self._clock,
                trace=self._trace,
                first_purpose=first_purpose,
            ),
            self._registry,
            executor=self._executor,
            max_repairs=self._max_repairs,
            max_output_tokens=self._builder_max_output_tokens,
            thinking=self._builder_thinking,
            repair_max_output_tokens=min(
                self._builder_max_output_tokens, DEFAULT_REPAIR_MAX_OUTPUT_TOKENS
            ),
            learning_token_budget=self._learning_token_budget,
            learning_call_budget=self._learning_call_budget,
            validation_concurrency=self._validation_concurrency,
        )

    async def _train_and_build(
        self,
        connector: ConnectorT,
        *,
        sequence_id: str,
        training_scenario_id: str,
        training_seed: int,
    ) -> _Trained[GradeT]:
        manifest = await connector.manifest()
        created_at = self._clock.now()
        training_record = training_experiment(
            experiment_id=f"{sequence_id}-training",
            model_id=self._model_id,
            connector_version=manifest.connector_version,
            created_at=created_at,
            condition=self._condition,
        )
        heldout_record = heldout_experiment(
            experiment_id=f"{sequence_id}-heldout",
            model_id=self._model_id,
            connector_version=manifest.connector_version,
            created_at=created_at,
            condition=self._condition,
        )
        self._store.create_experiment(training_record)
        self._store.create_experiment(heldout_record)

        # Cold: primitives only, no skill runtime.
        cold_agent = ActionAgent(
            self._recording(training_record, role="action"),
            manifest=manifest,
            max_output_tokens=self._action_max_output_tokens,
            thinking=self._action_thinking,
        )
        cold = EpisodeRunner(
            connector=connector,
            store=self._store,
            policy=cold_agent,
            clock=self._clock,
            trace=self._trace,
            close_connector=not self._persistent_connector,
        )
        cold_result = await cold.run(
            experiment=training_record,
            scenario_id=training_scenario_id,
            seed=training_seed,
            split="training",
        )
        training_stored = self._store.read_episode(cold_result.episode_id)
        training = TrainingEpisodeReport(
            episode_id=cold_result.episode_id,
            scenario_id=training_scenario_id,
            seed=training_seed,
            stop_reason=cold_result.stop_reason,
            decisions_used=cold_result.decisions_used,
            primitives_used=cold_result.primitives_used,
            grade=self._grade(training_stored, connector),
        )
        spent_tokens = cold_agent.input_tokens + cold_agent.output_tokens
        spent_calls = len(cold_agent.decisions)
        outcome = await self._builder(training_record, cold_result.episode_id).build(
            select_evidence(training_stored),
            training_trace=training_stored,
            primitive_names=tuple(tool.name for tool in manifest.tools),
            authoring_model_id=self._model_id,
            created_at=self._clock.now(),
            spent_tokens=spent_tokens,
            spent_calls=spent_calls,
        )
        return _Trained(
            manifest_tools=tuple(tool.name for tool in manifest.tools),
            connector_version=manifest.connector_version,
            training_record=training_record,
            heldout_record=heldout_record,
            training=training,
            training_stored=training_stored,
            outcome=outcome,
            spent_tokens=spent_tokens
            + sum(item.input_tokens + item.output_tokens for item in outcome.usage),
            spent_calls=spent_calls + outcome.attempts,
        )

    async def run(
        self,
        *,
        sequence_id: str,
        training_scenario_id: str,
        training_seed: int,
        heldout: Sequence[HeldOutCell],
    ) -> LearningSequenceResult[GradeT]:
        cells = tuple(heldout)
        if not cells:
            raise ValueError("A learning sequence needs at least one held-out cell.")
        if any(cell.scenario_id == training_scenario_id for cell in cells):
            raise ValueError("A held-out cell must not reuse the training scenario.")

        connector = self._connector_factory()
        try:
            result = await self._run(
                connector,
                sequence_id=sequence_id,
                training_scenario_id=training_scenario_id,
                training_seed=training_seed,
                cells=cells,
            )
        except BaseException:
            # Closing must not replace the error that is already propagating.
            if self._persistent_connector:
                with suppress(Exception):
                    await connector.close()
            raise
        if self._persistent_connector:
            await connector.close()
        return result

    async def _run(
        self,
        connector: ConnectorT,
        *,
        sequence_id: str,
        training_scenario_id: str,
        training_seed: int,
        cells: tuple[HeldOutCell, ...],
    ) -> LearningSequenceResult[GradeT]:
        trained = await self._train_and_build(
            connector,
            sequence_id=sequence_id,
            training_scenario_id=training_scenario_id,
            training_seed=training_seed,
        )
        training_record, heldout_record = trained.training_record, trained.heldout_record
        training, outcome = trained.training, trained.outcome

        reports: tuple[HeldOutEpisodeReport[GradeT], ...] = ()
        skipped_reason: str | None = None
        if outcome.accepted:
            reports = await self._run_cells(connector, heldout_record, cells)
        else:
            skipped_reason = outcome.stop_reason

        return LearningSequenceResult(
            sequence_id=sequence_id,
            model_id=self._model_id,
            training_experiment=training_record,
            heldout_experiment=heldout_record,
            training=training,
            builder=outcome,
            accepted_version=outcome.version,
            heldout=reports,
            heldout_skipped_reason=skipped_reason,
            skill_isolation_note=self._executor.isolation_note,
        )
