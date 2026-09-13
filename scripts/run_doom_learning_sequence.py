"""Run one live Doom learning sequence and print its result as JSON.

Build-order step 8c from `hackathon_plan.md` section 17: one cold training
episode, one Builder run, and every precommitted held-out cell in
`scenarios/doom/basic-v1/manifest.json`, each graded by the independent Doom
grader after it ends.

Settings come only from the process environment. Load the local `.env` with:

    uv run --env-file .env python scripts/run_doom_learning_sequence.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from noob_agent.connectors.doom import DoomConnector, DoomSettings
from noob_agent.domain.records import StoredEpisode
from noob_agent.grading.doom import DoomEpisodeGrade, DoomPrivateOutcome, grade_doom_episode
from noob_agent.models.client import build_model_client
from noob_agent.observability.tracing import build_trace_sink
from noob_agent.runtime.sequence import HeldOutCell, LearningSequence, LearningSequenceResult
from noob_agent.settings import IntegrationSettings
from noob_agent.skills.executor import build_skill_executor
from noob_agent.skills.registry import SkillRegistry
from noob_agent.storage import EpisodeStore

DEFAULT_MANIFEST = Path("scenarios/doom/basic-v1/manifest.json")
DEFAULT_DATABASE = Path(".noob-agent/doom-learning.sqlite3")
LIVE_DEMO_DATABASE = Path(".noob-agent/doom-live-demo.sqlite3")
LIVE_DEMO_CONDITION = "non-benchmark-live-demo"
LIVE_DEMO_DEADLINE_SECONDS = 600.0


class RunOptions(NamedTuple):
    database: Path | None
    manifest: Path
    sequence_id: str | None
    live_demo: bool
    deadline_seconds: float | None


def _parse_args(argv: Sequence[str] | None = None) -> RunOptions:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", type=Path, default=None)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--sequence-id", default=None)
    parser.add_argument(
        "--live-demo",
        action="store_true",
        help="Show real-time Doom under an explicit non-benchmark 10-minute deadline.",
    )
    parser.add_argument("--deadline-seconds", type=float, default=None)
    parsed = parser.parse_args(argv)
    live_demo = bool(parsed.live_demo)
    deadline = parsed.deadline_seconds
    if deadline is not None and not live_demo:
        parser.error("--deadline-seconds requires --live-demo")
    if live_demo:
        deadline = LIVE_DEMO_DEADLINE_SECONDS if deadline is None else float(deadline)
        if deadline <= 0 or deadline > LIVE_DEMO_DEADLINE_SECONDS:
            parser.error(
                f"the live-demo deadline must be greater than 0 and no more than "
                f"{LIVE_DEMO_DEADLINE_SECONDS:g} seconds"
            )
    return RunOptions(
        database=parsed.database,
        manifest=parsed.manifest,
        sequence_id=parsed.sequence_id,
        live_demo=live_demo,
        deadline_seconds=deadline,
    )


def _database_for(options: RunOptions) -> Path:
    return options.database or (LIVE_DEMO_DATABASE if options.live_demo else DEFAULT_DATABASE)


def _condition_for(options: RunOptions) -> str:
    return LIVE_DEMO_CONDITION if options.live_demo else "self-improving"


def _sequence_prefix(options: RunOptions) -> str:
    return "doom-live-demo" if options.live_demo else "doom-seq"


def _connector_factory(options: RunOptions) -> Callable[[], DoomConnector]:
    settings = DoomSettings(window_visible=options.live_demo, realtime=options.live_demo)
    return lambda: DoomConnector(settings)


def _grade(stored: StoredEpisode, connector: DoomConnector) -> DoomEpisodeGrade:
    outcome = DoomPrivateOutcome.from_connector(
        stored.episode.episode_id, connector.private_outcome()
    )
    return grade_doom_episode(stored, outcome)


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


def _summary(
    result: LearningSequenceResult[DoomEpisodeGrade],
    *,
    action_max_output_tokens: int,
    builder_max_output_tokens: int,
) -> dict[str, object]:
    version = result.accepted_version
    return {
        "sequence_id": result.sequence_id,
        "model_id": result.model_id,
        "skill_isolation": result.skill_isolation_note,
        "max_output_tokens": {
            "action": action_max_output_tokens,
            "builder": builder_max_output_tokens,
        },
        "training": {
            "episode_id": result.training.episode_id,
            "scenario_id": result.training.scenario_id,
            "seed": result.training.seed,
            "stop_reason": result.training.stop_reason,
            "decisions_used": result.training.decisions_used,
            "primitives_used": result.training.primitives_used,
            "goal_completed": result.training.grade.goal_completed,
        },
        "builder": {
            "accepted": result.builder.accepted,
            "stop_reason": result.builder.stop_reason,
            "attempts": result.builder.attempts,
            "input_tokens": sum(usage.input_tokens for usage in result.builder.usage),
            "output_tokens": sum(usage.output_tokens for usage in result.builder.usage),
        },
        "accepted_skill": None
        if version is None
        else {
            "name": version.name,
            "version": version.version,
            "content_hash": version.content_hash,
            "authoring_model_id": version.authoring_model_id,
        },
        "heldout_skipped_reason": result.heldout_skipped_reason,
        "heldout": [
            {
                "episode_id": episode.episode_id,
                "scenario_id": episode.scenario_id,
                "seed": episode.seed,
                "stop_reason": episode.stop_reason,
                "decisions_used": episode.decisions_used,
                "primitives_used": episode.primitives_used,
                "offered_skills": list(episode.offered_skills),
                "skill_uses": episode.skill_uses,
                "goal_completed": episode.grade.goal_completed,
            }
            for episode in result.heldout
        ],
    }


def main(argv: Sequence[str] | None = None, *, environ: Mapping[str, str] | None = None) -> int:
    args = _parse_args(argv)
    environment = os.environ if environ is None else environ

    if args.live_demo and not (
        environment.get("DISPLAY") or environment.get("WAYLAND_DISPLAY")
    ):
        print("Refusing live demo: no graphical display is available.", file=sys.stderr)
        return 2

    settings = IntegrationSettings.from_environ(environment)
    if settings.model.provider == "disabled":
        print("Refusing to run: NOOB_AGENT_MODEL_PROVIDER is disabled.", file=sys.stderr)
        return 2
    if settings.model.inference_model is None:
        print("Refusing to run: NOOB_AGENT_INFERENCE_MODEL is not set.", file=sys.stderr)
        return 2
    if settings.sandbox.mode == "disabled":
        print("Refusing to run: NOOB_AGENT_SANDBOX_MODE is disabled.", file=sys.stderr)
        return 2
    try:
        executor = build_skill_executor(settings.sandbox)
    except NotImplementedError as unsupported:
        print(f"Refusing to run: {unsupported}", file=sys.stderr)
        return 2

    training, heldout = _cells(args.manifest)
    model_id = settings.model.inference_model
    sequence_id = args.sequence_id or datetime.now(UTC).strftime(
        f"{_sequence_prefix(args)}-%Y%m%dT%H%M%SZ"
    )
    database = _database_for(args)
    database.parent.mkdir(parents=True, exist_ok=True)

    if args.live_demo:
        print(
            "NON-BENCHMARK LIVE DEMO: visible real-time Doom with Weave tracing; "
            f"whole-run deadline {args.deadline_seconds:g} seconds.",
            flush=True,
        )

    trace = build_trace_sink(settings.trace, wandb=settings.wandb)
    timed_out = False
    try:
        with EpisodeStore.open(database) as store:
            sequence = LearningSequence(
                connector_factory=_connector_factory(args),
                client=build_model_client(settings.model, settings.wandb),
                model_id=model_id,
                store=store,
                registry=SkillRegistry(),
                executor=executor,
                grade=_grade,
                trace=trace,
                action_max_output_tokens=settings.model.action_max_output_tokens,
                builder_max_output_tokens=settings.model.builder_max_output_tokens,
                action_thinking=settings.model.action_thinking,
                condition=_condition_for(args),
                persistent_connector=args.live_demo,
            )
            run = sequence.run(
                sequence_id=sequence_id,
                training_scenario_id=training.scenario_id,
                training_seed=training.seed,
                heldout=heldout,
            )
            try:
                result = asyncio.run(
                    asyncio.wait_for(run, timeout=args.deadline_seconds)
                    if args.live_demo
                    else run
                )
            except TimeoutError:
                timed_out = True
    finally:
        with suppress(Exception):
            trace.flush()

    if timed_out:
        print(
            json.dumps(
                {
                    "run_kind": LIVE_DEMO_CONDITION,
                    "status": "deadline_exceeded",
                    "deadline_seconds": args.deadline_seconds,
                    "sequence_id": sequence_id,
                    "database": str(database),
                },
                indent=2,
            )
        )
        return 124

    summary = _summary(
        result,
        action_max_output_tokens=settings.model.action_max_output_tokens,
        builder_max_output_tokens=settings.model.builder_max_output_tokens,
    )
    if args.live_demo:
        summary = {
            "run_kind": LIVE_DEMO_CONDITION,
            "status": "completed",
            "deadline_seconds": args.deadline_seconds,
            "database": str(database),
            **summary,
        }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
