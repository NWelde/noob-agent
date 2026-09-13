"""The multi-round improvement loop: keep a skill version only when public practice improves.

Section 22 step 22.D of `hackathon_plan.md`. Round 0 is the single-pass cold
training episode and build. The accepted skill then plays a fixed set of
training-split practice seeds under held-out budgets. Each later round asks the
Builder for one refinement from the incumbent's public practice evidence. A
refinement that passes validation plays the same practice seeds and replaces the
incumbent only if its public practice score is better; otherwise it is rejected
and the loop stops. Held-out cells run once, after the loop has ended.

The practice score uses public records only: the fraction of practice episodes
that ended in a terminal state, with total primitive actions as the tie-breaker.
Each practice episode's private grade is recorded next to it solely to measure
how often the public score agrees with it; no grade reaches a prompt or a
decision.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Generic, Literal

from noob_agent.agents.builder import BuilderOutcome
from noob_agent.agents.evidence import select_evidence
from noob_agent.domain.records import ExperimentRecord
from noob_agent.domain.skills import SkillVersion
from noob_agent.prompts.refine import PracticeAttempt, render_refine_prompt
from noob_agent.runtime.heldout import heldout_experiment
from noob_agent.runtime.sequence import (
    ConnectorT,
    GradeT,
    HeldOutCell,
    HeldOutEpisodeReport,
    LearningSequence,
    TrainingEpisodeReport,
)

DEFAULT_MAX_ROUNDS = 3
# The multi-round learning budget in eval_protocol.md: training, every Builder
# call, and every practice episode's model calls.
MULTI_ROUND_TOKEN_BUDGET = 300_000
MULTI_ROUND_CALL_BUDGET = 140
MULTI_ROUND_WALL_TIME_SECONDS = 900.0

RoundDecision = Literal["incumbent", "kept", "not_improved", "not_validated"]
ImprovementStop = Literal[
    "no_skill", "perfect_practice", "max_rounds", "no_improvement", "learning_budget"
]


@dataclass(frozen=True)
class PracticeScore:
    """The public practice score of one skill version."""

    terminal_rate: float
    primitives: int

    def better_than(self, other: PracticeScore) -> bool:
        if self.terminal_rate != other.terminal_rate:
            return self.terminal_rate > other.terminal_rate
        return self.primitives < other.primitives


@dataclass(frozen=True)
class RoundReport(Generic[GradeT]):
    round: int
    version: SkillVersion | None
    builder: BuilderOutcome | None
    practice: tuple[HeldOutEpisodeReport[GradeT], ...]
    score: PracticeScore | None
    decision: RoundDecision


@dataclass(frozen=True)
class CurvePoint(Generic[GradeT]):
    """One kept version's held-out results, evaluated after the loop ended."""

    version: int
    learning_tokens: int
    heldout: tuple[HeldOutEpisodeReport[GradeT], ...]


@dataclass(frozen=True)
class ImprovementResult(Generic[GradeT]):
    sequence_id: str
    training: TrainingEpisodeReport[GradeT]
    rounds: tuple[RoundReport[GradeT], ...]
    final_version: SkillVersion | None
    heldout: tuple[HeldOutEpisodeReport[GradeT], ...]
    stop_reason: ImprovementStop
    learning_tokens: int
    learning_calls: int
    practice_agreement: float | None
    curve: tuple[CurvePoint[GradeT], ...]
    skill_isolation_note: str


def practice_score(episodes: Sequence[HeldOutEpisodeReport[Any]]) -> PracticeScore:
    ended = sum(1 for episode in episodes if episode.stop_reason == "terminal_state")
    return PracticeScore(
        terminal_rate=ended / len(episodes) if episodes else 0.0,
        primitives=sum(episode.primitives_used for episode in episodes),
    )


class ImprovementLoop(LearningSequence[ConnectorT, GradeT]):
    """A learning sequence that refines its skill over practice rounds before held-out."""

    def __init__(
        self,
        *,
        max_rounds: int = DEFAULT_MAX_ROUNDS,
        learning_token_budget: int = MULTI_ROUND_TOKEN_BUDGET,
        learning_call_budget: int = MULTI_ROUND_CALL_BUDGET,
        learning_wall_seconds: float = MULTI_ROUND_WALL_TIME_SECONDS,
        grade_success: Callable[[GradeT], bool] | None = None,
        **options: Any,
    ) -> None:
        if max_rounds < 0:
            raise ValueError("max_rounds cannot be negative.")
        super().__init__(**options)
        self._max_rounds = max_rounds
        self._learning_token_budget = learning_token_budget
        self._learning_call_budget = learning_call_budget
        self._learning_wall_ms = int(learning_wall_seconds * 1000)
        self._grade_success = grade_success

    async def run(  # type: ignore[override]
        self,
        *,
        sequence_id: str,
        training_scenario_id: str,
        training_seed: int,
        practice: Sequence[HeldOutCell],
        heldout: Sequence[HeldOutCell],
        curve: bool = False,
    ) -> ImprovementResult[GradeT]:
        practice_cells, heldout_cells = tuple(practice), tuple(heldout)
        if not practice_cells or not heldout_cells:
            raise ValueError("The loop needs at least one practice cell and one held-out cell.")
        if any(cell.scenario_id == training_scenario_id for cell in heldout_cells):
            raise ValueError("A held-out cell must not reuse the training scenario.")
        heldout_scenarios = {cell.scenario_id for cell in heldout_cells}
        if any(cell.scenario_id in heldout_scenarios for cell in practice_cells):
            raise ValueError("Practice must use training-split scenarios, never a held-out one.")
        if any(cell.seed == training_seed for cell in practice_cells):
            raise ValueError("Practice seeds must differ from the seed the first skill came from.")

        connector = self._connector_factory()
        try:
            result = await self._improve(
                connector,
                sequence_id=sequence_id,
                training_scenario_id=training_scenario_id,
                training_seed=training_seed,
                practice_cells=practice_cells,
                heldout_cells=heldout_cells,
                curve=curve,
            )
        except BaseException:
            if self._persistent_connector:
                with suppress(Exception):
                    await connector.close()
            raise
        if self._persistent_connector:
            await connector.close()
        return result

    async def _improve(
        self,
        connector: ConnectorT,
        *,
        sequence_id: str,
        training_scenario_id: str,
        training_seed: int,
        practice_cells: tuple[HeldOutCell, ...],
        heldout_cells: tuple[HeldOutCell, ...],
        curve: bool,
    ) -> ImprovementResult[GradeT]:
        started_ms = self._clock.monotonic_ms()
        trained = await self._train_and_build(
            connector,
            sequence_id=sequence_id,
            training_scenario_id=training_scenario_id,
            training_seed=training_seed,
        )
        tokens, calls = trained.spent_tokens, trained.spent_calls
        practice_record = heldout_experiment(
            experiment_id=f"{sequence_id}-practice",
            model_id=self._model_id,
            connector_version=trained.connector_version,
            created_at=self._clock.now(),
            condition=self._condition,
        )
        self._store.create_experiment(practice_record)

        def result(
            rounds: Sequence[RoundReport[GradeT]],
            final: SkillVersion | None,
            stop: ImprovementStop,
            heldout: tuple[HeldOutEpisodeReport[GradeT], ...] = (),
            points: tuple[CurvePoint[GradeT], ...] = (),
        ) -> ImprovementResult[GradeT]:
            return ImprovementResult(
                sequence_id=sequence_id,
                training=trained.training,
                rounds=tuple(rounds),
                final_version=final,
                heldout=heldout,
                stop_reason=stop,
                learning_tokens=tokens,
                learning_calls=calls,
                practice_agreement=self._agreement(rounds),
                curve=points,
                skill_isolation_note=self._executor.isolation_note,
            )

        if not trained.outcome.accepted or trained.outcome.version is None:
            return result((), None, "no_skill")

        incumbent = trained.outcome.version
        practice = await self._practice(connector, practice_record, practice_cells, incumbent)
        tokens += _tokens(practice)
        calls += _calls(practice)
        score = practice_score(practice)
        rounds: list[RoundReport[GradeT]] = [
            RoundReport(0, incumbent, trained.outcome, practice, score, "incumbent")
        ]
        kept: list[tuple[SkillVersion, int]] = [(incumbent, tokens)]

        stop: ImprovementStop | None = None
        for number in range(1, self._max_rounds + 1):
            if score.terminal_rate == 1.0:
                stop = "perfect_practice"
                break
            if self._clock.monotonic_ms() - started_ms >= self._learning_wall_ms:
                stop = "learning_budget"
                break
            outcome = await self._builder(
                trained.training_record, trained.training.episode_id
            ).refine(
                incumbent,
                prompt=render_refine_prompt(
                    incumbent=incumbent,
                    attempts=self._attempts(practice),
                    primitive_names=trained.manifest_tools,
                    tools=trained.training_stored.episode.manifest.tools,
                ),
                evidence=select_evidence(trained.training_stored),
                training_trace=trained.training_stored,
                primitive_names=trained.manifest_tools,
                authoring_model_id=self._model_id,
                created_at=self._clock.now(),
                spent_tokens=tokens,
                spent_calls=calls,
            )
            tokens += sum(item.input_tokens + item.output_tokens for item in outcome.usage)
            calls += outcome.attempts
            if outcome.stop_reason == "learning_budget_exhausted":
                stop = "learning_budget"
                break
            if outcome.stop_reason != "validated" or outcome.version is None:
                rounds.append(RoundReport(number, None, outcome, (), None, "not_validated"))
                stop = "no_improvement"
                break

            challenger = outcome.version
            challenger_practice = await self._practice(
                connector, practice_record, practice_cells, challenger
            )
            tokens += _tokens(challenger_practice)
            calls += _calls(challenger_practice)
            challenger_score = practice_score(challenger_practice)
            if challenger_score.better_than(score):
                self._registry.retire(
                    incumbent.name,
                    incumbent.version,
                    reason=(
                        f"Replaced by version {challenger.version} after a better practice score."
                    ),
                )
                incumbent = self._registry.accept(
                    challenger.name,
                    challenger.version,
                    reason=(
                        f"Practice score {challenger_score.terminal_rate:.2f} with "
                        f"{challenger_score.primitives} primitives beat "
                        f"{score.terminal_rate:.2f} with {score.primitives}."
                    ),
                )
                practice, score = challenger_practice, challenger_score
                kept.append((incumbent, tokens))
                rounds.append(
                    RoundReport(number, incumbent, outcome, challenger_practice, score, "kept")
                )
            else:
                self._registry.reject(
                    challenger.name,
                    challenger.version,
                    reason=(
                        "Validated, but did not improve practice score: "
                        f"{challenger_score.terminal_rate:.2f} with "
                        f"{challenger_score.primitives} primitives against "
                        f"{score.terminal_rate:.2f} with {score.primitives}."
                    ),
                )
                rounds.append(
                    RoundReport(
                        number,
                        self._registry.get(challenger.name, challenger.version),
                        outcome,
                        challenger_practice,
                        challenger_score,
                        "not_improved",
                    )
                )
                stop = "no_improvement"
                break
        if stop is None:
            stop = "perfect_practice" if score.terminal_rate == 1.0 else "max_rounds"

        # Held-out cells run only now, after every learning decision is final.
        heldout = await self._run_cells(connector, trained.heldout_record, heldout_cells)
        points: tuple[CurvePoint[GradeT], ...] = ()
        if curve:
            points = await self._curve(
                connector, sequence_id, trained.connector_version, heldout_cells, kept, heldout
            )
        return result(rounds, incumbent, stop, heldout, points)

    async def _practice(
        self,
        connector: ConnectorT,
        record: ExperimentRecord,
        cells: tuple[HeldOutCell, ...],
        version: SkillVersion,
    ) -> tuple[HeldOutEpisodeReport[GradeT], ...]:
        # A validated refinement is offered for practice before the registry accepts it.
        offered = version.model_copy(update={"status": "accepted"})
        return await self._run_cells(connector, record, cells, split="training", offered=(offered,))

    def _attempts(
        self, practice: Sequence[HeldOutEpisodeReport[GradeT]]
    ) -> tuple[PracticeAttempt, ...]:
        return tuple(
            PracticeAttempt(
                stop_reason=episode.stop_reason,
                decisions=episode.decisions_used,
                primitives=episode.primitives_used,
                skill_results=episode.skill_results,
                evidence=select_evidence(self._store.read_episode(episode.episode_id)),
            )
            for episode in practice
        )

    async def _curve(
        self,
        connector: ConnectorT,
        sequence_id: str,
        connector_version: str,
        cells: tuple[HeldOutCell, ...],
        kept: Sequence[tuple[SkillVersion, int]],
        final_heldout: tuple[HeldOutEpisodeReport[GradeT], ...],
    ) -> tuple[CurvePoint[GradeT], ...]:
        points: list[CurvePoint[GradeT]] = []
        for index, (version, learning_tokens) in enumerate(kept):
            if index == len(kept) - 1:
                points.append(CurvePoint(version.version, learning_tokens, final_heldout))
                continue
            record = heldout_experiment(
                experiment_id=f"{sequence_id}-curve-v{version.version}",
                model_id=self._model_id,
                connector_version=connector_version,
                created_at=self._clock.now(),
                condition=self._condition,
            )
            self._store.create_experiment(record)
            offered = version.model_copy(update={"status": "accepted"})
            reports = await self._run_cells(connector, record, cells, offered=(offered,))
            points.append(CurvePoint(version.version, learning_tokens, reports))
        return tuple(points)

    def _agreement(self, rounds: Sequence[RoundReport[GradeT]]) -> float | None:
        if self._grade_success is None:
            return None
        pairs = [
            (episode.stop_reason == "terminal_state", self._grade_success(episode.grade))
            for report in rounds
            for episode in report.practice
        ]
        if not pairs:
            return None
        return sum(1 for public, private in pairs if public == private) / len(pairs)


def _tokens(episodes: Sequence[HeldOutEpisodeReport[Any]]) -> int:
    return sum(episode.input_tokens + episode.output_tokens for episode in episodes)


def _calls(episodes: Sequence[HeldOutEpisodeReport[Any]]) -> int:
    return sum(episode.decisions_by_model for episode in episodes)
