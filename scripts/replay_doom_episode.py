"""Replay a recorded Doom episode in a visible window and check every step.

Step 19b from `hackathon_plan.md` section 19. The script resets the recorded
scenario with the recorded seed, sends the recorded requests to a real
`DoomConnector`, and compares each result with the record, ignoring only the
per-reset episode ID and host wall time. It stops at the first step that
differs, so a diverged replay is never shown as the agent's play.

It reads only a temporary copy of the database, and it never calls a model, the
Builder, a grader, or a skill executor. List the Doom episodes in a database:

    uv run python scripts/replay_doom_episode.py --database .noob-agent/doom-learning.sqlite3

Watch one:

    uv run python scripts/replay_doom_episode.py \\
      --database .noob-agent/doom-learning.sqlite3 --episode-id <episode-id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sqlite3
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from noob_agent.connectors.doom import CONNECTOR_VERSION, GAME_ID, DoomConnector, DoomSettings
from noob_agent.domain.model import Observation, StepResult
from noob_agent.domain.records import StoredEpisode
from noob_agent.storage import EpisodeStore, UnknownRecordError

DEFAULT_DATABASE = Path(".noob-agent/doom-learning.sqlite3")
DEFAULT_STEP_PAUSE_SECONDS = 0.5

Difference = tuple[str, object, object]


def _pause(seconds: float) -> None:
    time.sleep(seconds)


def _comparable_observation(observation: Observation) -> dict[str, Any]:
    return observation.model_dump(mode="json", exclude={"episode_id"})


def _comparable_result(result: StepResult) -> dict[str, Any]:
    return result.model_dump(
        mode="json", exclude={"wall_time_ms": True, "observation": {"episode_id"}}
    )


def _differences(recorded: object, replayed: object, path: str = "") -> list[Difference]:
    """Every leaf where the replay differs from the record, with its dotted path."""
    if isinstance(recorded, dict) and isinstance(replayed, dict):
        found: list[Difference] = []
        for key in sorted(set(recorded) | set(replayed)):
            found.extend(
                _differences(recorded.get(key), replayed.get(key), f"{path}.{key}" if path else key)
            )
        return found
    if isinstance(recorded, list) and isinstance(replayed, list) and len(recorded) == len(replayed):
        found = []
        for index, (left, right) in enumerate(zip(recorded, replayed, strict=True)):
            found.extend(_differences(left, right, f"{path}[{index}]"))
        return found
    return [] if recorded == replayed else [(path, recorded, replayed)]


def _report_mismatch(sequence: int, differences: list[Difference]) -> None:
    print(f"MISMATCH at sequence {sequence}. The replay stopped here:", file=sys.stderr)
    for path, recorded, replayed in differences:
        print(f"  {path}: recorded {recorded!r}, replayed {replayed!r}", file=sys.stderr)
    print(
        "This is not a faithful replay of the recorded episode from this point on.",
        file=sys.stderr,
    )


def _describe(observation: Observation) -> str:
    visible = ", ".join(
        f"{item.label}@{item.properties.get('screen_offset')}"
        for item in observation.visible_objects
    )
    health = observation.status.get("health")
    ammo = observation.status.get("ammo")
    return f"visible: {visible or '-'} | health {health} ammo {ammo}"


def _list_episodes(database: Path) -> None:
    connection = sqlite3.connect(database)
    try:
        rows = connection.execute(
            """
            SELECT episode.episode_id, episode.scenario_id, episode.seed, episode.split,
                   (SELECT COUNT(*) FROM step WHERE step.episode_id = episode.episode_id),
                   episode_outcome.stop_reason
            FROM episode
            LEFT JOIN episode_outcome USING (episode_id)
            WHERE episode.game_id = ?
            ORDER BY episode.rowid ASC
            """,
            (GAME_ID,),
        ).fetchall()
    finally:
        connection.close()
    if not rows:
        print("No Doom episodes are recorded in this database.")
        return
    print("episode_id | scenario | seed | split | steps | stop_reason")
    for episode_id, scenario_id, seed, split, steps, stop_reason in rows:
        print(
            f"{episode_id} | {scenario_id} | {seed} | {split} | {steps} | "
            f"{stop_reason or 'unfinished'}"
        )


def _refusal(stored: StoredEpisode) -> str | None:
    episode = stored.episode
    if episode.game_id != GAME_ID:
        return f"episode {episode.episode_id!r} is from game {episode.game_id!r}, not {GAME_ID!r}."
    recorded_version = episode.manifest.connector_version
    if recorded_version != CONNECTOR_VERSION:
        return (
            f"episode {episode.episode_id!r} was recorded with connector "
            f"{recorded_version!r}, but this connector is {CONNECTOR_VERSION!r}."
        )
    return None


async def _replay(
    stored: StoredEpisode, *, headless: bool, windowed: bool, step_pause_seconds: float
) -> int:
    episode = stored.episode
    outcome = stored.outcome.stop_reason if stored.outcome is not None else "unfinished"
    print(f"REPLAY of recorded episode {episode.episode_id} - not a live model run.")
    print(
        f"scenario {episode.scenario_id} | seed {episode.seed} | split {episode.split} | "
        f"steps {len(stored.steps)} | recorded stop reason {outcome}"
    )

    connector = DoomConnector(
        DoomSettings(
            window_visible=not headless,
            realtime=not headless,
            fullscreen=not headless and not windowed,
        )
    )
    try:
        reset = await connector.reset(episode.scenario_id, episode.seed)
        differences = _differences(
            _comparable_observation(episode.reset_observation), _comparable_observation(reset)
        )
        if differences:
            print(f"[reset] {_describe(reset)} | MISMATCH")
            _report_mismatch(0, differences)
            return 1
        print(f"[reset] {_describe(reset)} | match")

        for step in stored.steps:
            request = step.request
            result = await connector.step(request)
            differences = _differences(_comparable_result(step.result), _comparable_result(result))
            verdict = "MISMATCH" if differences else "match"
            print(
                f"[step {step.sequence}] {request.tool_name} "
                f"{json.dumps(request.arguments, sort_keys=True)} "
                f"{result.status}/{result.code} | {_describe(result.observation)} | {verdict}"
            )
            if differences:
                _report_mismatch(step.sequence, differences)
                return 1
            if not headless:
                _pause(step_pause_seconds)
    finally:
        await connector.close()

    print(
        f"REPLAY COMPLETE: all {len(stored.steps)} steps matched the record "
        f"(recorded stop reason {outcome})."
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--episode-id", default=None)
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Hide the window and skip pacing and pauses; only check the replay.",
    )
    parser.add_argument("--windowed", action="store_true", help="Do not request full screen.")
    parser.add_argument("--step-pause-seconds", type=float, default=DEFAULT_STEP_PAUSE_SECONDS)
    args = parser.parse_args(argv)

    if not args.database.is_file():
        print(f"Refusing to replay: database {args.database} does not exist.", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="noob-agent-replay-") as scratch:
        # Only the copy is opened, so the recorded database is never written.
        copy = Path(scratch) / args.database.name
        shutil.copyfile(args.database, copy)

        if args.episode_id is None:
            _list_episodes(copy)
            return 0

        with EpisodeStore.open(copy) as store:
            try:
                stored = store.read_episode(args.episode_id)
            except UnknownRecordError:
                print(
                    f"Refusing to replay: episode {args.episode_id!r} is not recorded in "
                    f"{args.database}.",
                    file=sys.stderr,
                )
                return 2

    refusal = _refusal(stored)
    if refusal is not None:
        print(f"Refusing to replay: {refusal}", file=sys.stderr)
        return 2
    return asyncio.run(
        _replay(
            stored,
            headless=args.headless,
            windowed=args.windowed,
            step_pause_seconds=args.step_pause_seconds,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
