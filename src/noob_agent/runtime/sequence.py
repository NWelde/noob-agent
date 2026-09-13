"""One learning sequence for one model: cold training, the Builder, held-out reuse.

This is build-order step 8 from `hackathon_plan.md` section 17. The sequence is
game-neutral: a connector factory supplies a fresh connector for every episode,
and a grading callback scores each episode only after it has finished. It does
not compare models or conditions, aggregate results, or write a report.

Separation holds by construction. The Builder sees only public evidence from
the cold training episode. Held-out episodes run through `HeldOutRunner`, which
has no path to the Builder or to validation. Grades are returned to the caller
and never passed to a model client.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Generic, TypeVar

from noob_agent.agents.action import ActionAgent
from noob_agent.agents.builder import BuilderAgent, BuilderOutcome
from noob_agent.agents.evidence import select_evidence
from noob_agent.connectors.protocol import GameConnector
from noob_agent.domain.records import ExperimentRecord, StopReason, StoredEpisode
from noob_agent.domain.skills import SkillVersion
from noob_agent.models.client import ModelClient
from noob_agent.observability.tracing import TraceSink
from noob_agent.runtime.heldout import HeldOutRunner, heldout_experiment
from noob_agent.runtime.runner import Clock, EpisodeRunner, SystemClock
from noob_agent.skills.executor import SkillExecutor
from noob_agent.skills.registry import SkillRegistry
from noob_agent.storage import EpisodeStore

# The training attempt budgets from connector_contract.md.
TRAINING_DECISION_BUDGET = 20
TRAINING_PRIMITIVE_BUDGET = 40
TRAINING_WALL_TIME_MS = 180_000

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
    ) -> None:
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
        manifest = await connector.manifest()
        created_at = self._clock.now()
        training_record = training_experiment(
            experiment_id=f"{sequence_id}-training",
            model_id=self._model_id,
            connector_version=manifest.connector_version,
            created_at=created_at,
        )
        heldout_record = heldout_experiment(
            experiment_id=f"{sequence_id}-heldout",
            model_id=self._model_id,
            connector_version=manifest.connector_version,
            created_at=created_at,
        )
        self._store.create_experiment(training_record)
        self._store.create_experiment(heldout_record)

        # Cold: primitives only, no skill runtime.
        cold = EpisodeRunner(
            connector=connector,
            store=self._store,
            policy=ActionAgent(self._client, manifest=manifest),
            clock=self._clock,
            trace=self._trace,
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

        builder = BuilderAgent(self._client, self._registry, max_repairs=self._max_repairs)
        outcome = await builder.build(
            select_evidence(training_stored),
            primitive_names=tuple(tool.name for tool in manifest.tools),
            authoring_model_id=self._model_id,
            created_at=self._clock.now(),
        )

        reports: list[HeldOutEpisodeReport[GradeT]] = []
        skipped_reason: str | None = None
        if outcome.accepted:
            for cell in cells:
                heldout_connector = self._connector_factory()
                runner = HeldOutRunner(
                    connector=heldout_connector,
                    store=self._store,
                    registry=self._registry,
                    client=self._client,
                    executor=self._executor,
                    clock=self._clock,
                    trace=self._trace,
                )
                result = await runner.run(
                    experiment=heldout_record, scenario_id=cell.scenario_id, seed=cell.seed
                )
                stored = self._store.read_episode(result.episode.episode_id)
                reports.append(
                    HeldOutEpisodeReport(
                        episode_id=result.episode.episode_id,
                        scenario_id=cell.scenario_id,
                        seed=cell.seed,
                        stop_reason=result.episode.stop_reason,
                        decisions_used=result.episode.decisions_used,
                        primitives_used=result.episode.primitives_used,
                        offered_skills=tuple(skill.name for skill in result.offered_skills),
                        skill_uses=len(result.skill_uses),
                        grade=self._grade(stored, heldout_connector),
                    )
                )
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
            heldout=tuple(reports),
            heldout_skipped_reason=skipped_reason,
            skill_isolation_note=self._executor.isolation_note,
        )
