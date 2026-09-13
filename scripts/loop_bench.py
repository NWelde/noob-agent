"""Run headless fixed-seed learning sequences and score the loop.

Section 22 step 22.F of `hackathon_plan.md`. `run` executes fresh Doom learning
sequences with the manifest's fixed seeds into a new Git-ignored database and
writes the scorecard as JSON and Markdown. `score` scores an existing database
through a read-only connection.

    uv run --env-file .env python scripts/loop_bench.py run --sequences 5
    uv run python scripts/loop_bench.py score --database .noob-agent/doom-live-demo.sqlite3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from noob_agent.connectors.protocol import GameConnector
from noob_agent.domain.records import StoredEpisode
from noob_agent.models.client import ModelClient, build_model_client
from noob_agent.observability.loop_scorecard import (
    CurveSummary,
    RoundSummary,
    RunScore,
    SequenceOutcome,
    load_sequences,
    render_markdown,
    run_latencies,
    score_run,
    score_sequence,
)
from noob_agent.observability.tracing import NullTraceSink, TraceSink, build_trace_sink, close_trace
from noob_agent.runtime.improvement import ImprovementLoop, ImprovementResult
from noob_agent.runtime.sequence import HeldOutCell, LearningSequence, LearningSequenceResult
from noob_agent.settings import IntegrationSettings
from noob_agent.skills.executor import SkillExecutor, build_skill_executor
from noob_agent.skills.registry import SkillRegistry
from noob_agent.storage import EpisodeStore

DEFAULT_MANIFEST = Path("scenarios/doom/basic-v1/manifest.json")
DEFAULT_OUTPUT_DIR = Path(".noob-agent/loop-bench")
BENCH_CONDITION = "loop-bench"
MULTI_ROUND_CONDITION = "loop-bench-multi-round"
DEFAULT_HELDOUT_CONCURRENCY = 6


@dataclass(frozen=True)
class BenchDependencies:
    """Everything a run needs from the outside world, injectable for tests."""

    connector_factory: Callable[[], GameConnector]
    client: ModelClient
    grade: Callable[[StoredEpisode, Any], Any]
    executor: SkillExecutor
    trace: TraceSink | None = None


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="Run fixed-seed sequences and score them.")
    run.add_argument("--sequences", type=int, default=5)
    run.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    run.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    run.add_argument("--run-id", default=None)
    run.add_argument(
        "--rounds",
        type=int,
        default=0,
        help="Refinement rounds over practice seeds (0 runs the single-pass sequence).",
    )
    run.add_argument(
        "--curve",
        action="store_true",
        help="With --rounds, evaluate every kept version on the held-out cells afterwards.",
    )
    run.add_argument(
        "--heldout-concurrency",
        type=int,
        default=DEFAULT_HELDOUT_CONCURRENCY,
        help="Held-out cells played at once (1 plays them one after another).",
    )

    score = commands.add_parser("score", help="Score an existing database, read-only.")
    score.add_argument("--database", type=Path, required=True)
    score.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    score.add_argument("--title", default=None)

    parsed = parser.parse_args(argv)
    if parsed.command == "run" and parsed.sequences < 1:
        parser.error("--sequences must be at least 1")
    if parsed.command == "run" and parsed.heldout_concurrency < 1:
        parser.error("--heldout-concurrency must be at least 1")
    if parsed.command == "run" and parsed.rounds < 0:
        parser.error("--rounds cannot be negative")
    if parsed.command == "run" and parsed.curve and parsed.rounds == 0:
        parser.error("--curve requires --rounds")
    return parsed


def _practice_cells(manifest_path: Path) -> tuple[HeldOutCell, ...]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return tuple(
        HeldOutCell(scenario_id, int(seed))
        for scenario_id, scenario in manifest["scenarios"].items()
        if scenario["split"] == "training"
        for seed in scenario.get("practice_seeds", ())
    )


def _cells(manifest_path: Path) -> tuple[HeldOutCell, tuple[HeldOutCell, ...]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    training: list[HeldOutCell] = []
    heldout: list[HeldOutCell] = []
    for scenario_id, scenario in manifest["scenarios"].items():
        cells = [HeldOutCell(scenario_id, int(seed)) for seed in scenario["seeds"]]
        (training if scenario["split"] == "training" else heldout).extend(cells)
    if len(training) != 1:
        raise ValueError("The manifest must declare exactly one training seed.")
    return training[0], tuple(heldout)


def _doom_dependencies(settings: IntegrationSettings) -> BenchDependencies:
    from noob_agent.connectors.doom import DoomConnector, DoomSettings
    from noob_agent.grading.doom import DoomPrivateOutcome, grade_doom_episode

    def grade(stored: StoredEpisode, connector: DoomConnector) -> object:
        outcome = DoomPrivateOutcome.from_connector(
            stored.episode.episode_id, connector.private_outcome()
        )
        return grade_doom_episode(stored, outcome)

    return BenchDependencies(
        connector_factory=lambda: DoomConnector(DoomSettings()),
        client=build_model_client(settings.model, settings.wandb),
        grade=grade,
        executor=build_skill_executor(settings.sandbox),
        trace=build_trace_sink(settings.trace, wandb=settings.wandb),
    )


def _outcome(result: LearningSequenceResult[Any]) -> SequenceOutcome:
    grades = {result.training.episode_id: bool(result.training.grade.goal_completed)}
    grades.update(
        {episode.episode_id: bool(episode.grade.goal_completed) for episode in result.heldout}
    )
    return SequenceOutcome(
        builder_accepted=result.builder.accepted,
        builder_stop_reason=result.builder.stop_reason,
        grades=grades,
        skill_uses={episode.episode_id: episode.skill_uses for episode in result.heldout},
    )


def _goal_completed(grade: Any) -> bool:
    return bool(getattr(grade, "goal_completed", False))


def _loop_outcome(result: ImprovementResult[Any]) -> SequenceOutcome:
    episodes = [result.training, *result.heldout]
    grades = {episode.episode_id: _goal_completed(episode.grade) for episode in episodes}
    for report in result.rounds:
        grades.update({e.episode_id: _goal_completed(e.grade) for e in report.practice})
    return SequenceOutcome(
        builder_accepted=result.final_version is not None,
        builder_stop_reason=result.rounds[0].builder.stop_reason
        if result.rounds and result.rounds[0].builder is not None
        else result.stop_reason,
        grades=grades,
        skill_uses={e.episode_id: e.skill_uses for e in result.heldout},
        rounds=tuple(
            RoundSummary(
                round=report.round,
                version=None if report.version is None else report.version.version,
                decision=report.decision,
                practice_rate=None if report.score is None else report.score.terminal_rate,
                practice_primitives=0 if report.score is None else report.score.primitives,
                practice_goals=sum(1 for e in report.practice if _goal_completed(e.grade)),
                practice_episodes=len(report.practice),
            )
            for report in result.rounds
        ),
        loop_stop_reason=result.stop_reason,
        practice_agreement=result.practice_agreement,
        curve=tuple(
            CurveSummary(
                version=point.version,
                learning_tokens=point.learning_tokens,
                heldout_goals=sum(1 for e in point.heldout if _goal_completed(e.grade)),
                heldout_episodes=len(point.heldout),
            )
            for point in result.curve
        ),
    )


def _write(score: RunScore, output_dir: Path, name: str, title: str) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown = render_markdown(score, title=title)
    (output_dir / f"{name}.json").write_text(score.model_dump_json(indent=2), encoding="utf-8")
    (output_dir / f"{name}.md").write_text(markdown, encoding="utf-8")
    return markdown


def _run(
    args: argparse.Namespace,
    environment: Mapping[str, str],
    dependencies: BenchDependencies | None,
) -> int:
    settings = IntegrationSettings.from_environ(environment)
    if settings.model.provider == "disabled" or settings.model.inference_model is None:
        print("Refusing to run: no model provider and model ID are configured.", file=sys.stderr)
        return 2
    if settings.sandbox.mode == "disabled":
        print("Refusing to run: NOOB_AGENT_SANDBOX_MODE is disabled.", file=sys.stderr)
        return 2

    run_id = args.run_id or datetime.now(UTC).strftime("loop-bench-%Y%m%dT%H%M%SZ")
    database = args.output_dir / f"{run_id}.sqlite3"
    if database.exists():
        print(f"Refusing to run: {database} already exists.", file=sys.stderr)
        return 2
    training, heldout = _cells(args.manifest)
    practice = _practice_cells(args.manifest)
    if args.rounds and not practice:
        print(
            f"Refusing to run: --rounds needs practice_seeds in {args.manifest}.",
            file=sys.stderr,
        )
        return 2

    if dependencies is None:
        try:
            dependencies = _doom_dependencies(settings)
        except NotImplementedError as unsupported:
            print(f"Refusing to run: {unsupported}", file=sys.stderr)
            return 2
    trace = dependencies.trace if dependencies.trace is not None else NullTraceSink()
    model_id = settings.model.inference_model

    args.output_dir.mkdir(parents=True, exist_ok=True)
    outcomes: dict[str, SequenceOutcome] = {}
    failed = False
    try:
        with EpisodeStore.open(database) as store:
            for index in range(1, args.sequences + 1):
                sequence_id = f"{run_id}-s{index:02d}"
                print(f"{sequence_id}: running", file=sys.stderr, flush=True)
                options: dict[str, Any] = {
                    "connector_factory": dependencies.connector_factory,
                    "client": dependencies.client,
                    "model_id": model_id,
                    "store": store,
                    "registry": SkillRegistry(),
                    "executor": dependencies.executor,
                    "grade": dependencies.grade,
                    "trace": trace,
                    "action_max_output_tokens": settings.model.action_max_output_tokens,
                    "builder_max_output_tokens": settings.model.builder_max_output_tokens,
                    "action_thinking": settings.model.action_thinking,
                    "builder_thinking": settings.model.builder_thinking,
                    "condition": BENCH_CONDITION if not args.rounds else MULTI_ROUND_CONDITION,
                    "heldout_concurrency": args.heldout_concurrency,
                }
                try:
                    if args.rounds:
                        loop = ImprovementLoop(
                            max_rounds=args.rounds,
                            grade_success=_goal_completed,
                            **options,
                        )
                        improved = asyncio.run(
                            loop.run(
                                sequence_id=sequence_id,
                                training_scenario_id=training.scenario_id,
                                training_seed=training.seed,
                                practice=practice,
                                heldout=heldout,
                                curve=args.curve,
                            )
                        )
                        outcomes[sequence_id] = _loop_outcome(improved)
                        continue
                    result = asyncio.run(
                        LearningSequence(**options).run(
                            sequence_id=sequence_id,
                            training_scenario_id=training.scenario_id,
                            training_seed=training.seed,
                            heldout=heldout,
                        )
                    )
                except Exception as error:  # a provider or connector failure ends the run
                    print(
                        f"{sequence_id}: stopped by {type(error).__name__}: {error}",
                        file=sys.stderr,
                    )
                    failed = True
                    break
                outcomes[sequence_id] = _outcome(result)
    finally:
        with suppress(Exception):
            close_trace(trace)

    records = load_sequences(database)
    score = score_run(
        [score_sequence(r, outcomes.get(r.sequence_id)) for r in records],
        run_latencies(records),
    )
    print(_write(score, args.output_dir, run_id, f"Loop bench {run_id} ({model_id})"))
    return 1 if failed else 0


def _score(args: argparse.Namespace) -> int:
    if not args.database.is_file():
        print(f"Refusing to score: {args.database} does not exist.", file=sys.stderr)
        return 2
    records = load_sequences(args.database)
    score = score_run([score_sequence(r) for r in records], run_latencies(records))
    name = args.database.stem
    print(_write(score, args.output_dir, name, args.title or f"Loop scorecard: {name}"))
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    dependencies: BenchDependencies | None = None,
) -> int:
    args = _parse_args(argv)
    if args.command == "score":
        return _score(args)
    return _run(args, os.environ if environ is None else environ, dependencies)


if __name__ == "__main__":
    raise SystemExit(main())
