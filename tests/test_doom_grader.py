"""The independent Doom grader: success comes from the private outcome, never from a claim."""

from __future__ import annotations

import inspect
import json
import math
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fakes.connector import FakeClock

from noob_agent.connectors import DoomConnector
from noob_agent.connectors.doom import CONNECTOR_VERSION, DoomEpisodeOutcome
from noob_agent.domain.model import Observation, ToolRequest
from noob_agent.domain.records import ExperimentRecord, StoredEpisode
from noob_agent.grading.doom import (
    GRADER_VERSION,
    DoomEpisodeGrade,
    DoomPrivateOutcome,
    grade_doom_episode,
)
from noob_agent.runtime import EpisodeRunner
from noob_agent.runtime.heldout import (
    HELD_OUT_DECISION_BUDGET,
    HELD_OUT_PRIMITIVE_BUDGET,
    heldout_experiment,
)
from noob_agent.runtime.runner import EpisodeResult, Policy
from noob_agent.storage import EpisodeStore

STARTED_AT = datetime(2026, 9, 12, 22, 0, 0, tzinfo=UTC)
MANIFEST_PATH = Path("scenarios/doom/basic-v1/manifest.json")
TRAINING = "doom-basic-training"
CENTERED_TOLERANCE = 3


def _experiment(experiment_id: str = "exp_doom_0001") -> ExperimentRecord:
    return heldout_experiment(
        experiment_id=experiment_id,
        model_id="scripted-oracle",
        connector_version=CONNECTOR_VERSION,
        created_at=STARTED_AT,
    )


def _outcome(episode_id: str, **overrides: object) -> DoomPrivateOutcome:
    values: dict[str, object] = {
        "episode_id": episode_id,
        "kills": 1,
        "player_dead": False,
        "timed_out": False,
        "finished": True,
    }
    return DoomPrivateOutcome.model_validate(values | overrides)


class ObservingPolicy:
    """Spends every decision observing, so it never engages the target."""

    def __init__(self) -> None:
        self._count = 0

    async def choose(self, observation: Observation) -> ToolRequest:
        self._count += 1
        return ToolRequest(action_id=f"obs_{self._count:04d}", tool_name="observe")


class CenterAndFireOracle:
    """Scripted oracle: turn until the target is centered, then fire."""

    def __init__(self) -> None:
        self._count = 0

    async def choose(self, observation: Observation) -> ToolRequest:
        self._count += 1
        action_id = f"orc_{self._count:04d}"
        targets = [item for item in observation.visible_objects if item.label == "Cacodemon"]
        if not targets:
            return ToolRequest(
                action_id=action_id, tool_name="turn_left", arguments={"degrees": 30}
            )
        offset = targets[0].properties["screen_offset"]
        assert isinstance(offset, int)
        if abs(offset) <= CENTERED_TOLERANCE:
            return ToolRequest(action_id=action_id, tool_name="attack", arguments={"ticks": 4})
        degrees = max(1, min(90, round(math.degrees(math.atan(abs(offset) / 100)))))
        tool = "turn_right" if offset > 0 else "turn_left"
        return ToolRequest(action_id=action_id, tool_name=tool, arguments={"degrees": degrees})


async def _run_doom(
    store: EpisodeStore, policy: Policy, scenario_id: str, seed: int
) -> tuple[StoredEpisode, DoomEpisodeOutcome, EpisodeResult]:
    connector = DoomConnector()
    runner = EpisodeRunner(
        connector=connector, store=store, policy=policy, clock=FakeClock(wall=STARTED_AT)
    )
    result = await runner.run(
        experiment=_experiment(), scenario_id=scenario_id, seed=seed, split="held-out"
    )
    return store.read_episode(result.episode_id), connector.private_outcome(), result


@pytest.fixture
async def observed_episode(store: EpisodeStore) -> StoredEpisode:
    store.create_experiment(_experiment())
    stored, _, _ = await _run_doom(store, ObservingPolicy(), TRAINING, 20260912)
    return stored


async def test_a_kill_with_the_player_alive_is_success(observed_episode: StoredEpisode) -> None:
    episode_id = observed_episode.episode.episode_id

    grade = grade_doom_episode(observed_episode, _outcome(episode_id))

    assert grade == DoomEpisodeGrade(
        episode_id=episode_id, goal_completed=True, grader_version=GRADER_VERSION
    )


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"kills": 1, "player_dead": True}, id="kill-after-player-died"),
        pytest.param({"kills": 0, "timed_out": True}, id="timeout"),
        pytest.param({"kills": 0}, id="zero-kills"),
    ],
)
async def test_failures_are_never_success(
    observed_episode: StoredEpisode, overrides: dict[str, object]
) -> None:
    outcome = _outcome(observed_episode.episode.episode_id, **overrides)

    assert grade_doom_episode(observed_episode, outcome).goal_completed is False


async def test_an_outcome_from_a_different_episode_is_refused(
    observed_episode: StoredEpisode,
) -> None:
    with pytest.raises(ValueError, match="different episode"):
        grade_doom_episode(observed_episode, _outcome("doom-doom-basic-training-r999"))


async def test_an_unfinished_episode_is_not_graded(observed_episode: StoredEpisode) -> None:
    unfinished = observed_episode.model_copy(update={"outcome": None})

    with pytest.raises(ValueError, match="after the episode"):
        grade_doom_episode(unfinished, _outcome(observed_episode.episode.episode_id))


async def test_an_episode_from_another_game_is_refused(observed_episode: StoredEpisode) -> None:
    other_game = observed_episode.model_copy(
        update={"episode": observed_episode.episode.model_copy(update={"game_id": "minecraft"})}
    )

    with pytest.raises(ValueError, match="Doom"):
        grade_doom_episode(other_game, _outcome(observed_episode.episode.episode_id))


async def test_the_grade_depends_only_on_the_private_outcome(
    observed_episode: StoredEpisode,
) -> None:
    parameters = list(inspect.signature(grade_doom_episode).parameters)
    assert parameters == ["stored", "outcome", "grader_version"]

    episode_id = observed_episode.episode.episode_id
    no_steps = observed_episode.model_copy(update={"steps": ()})
    for outcome in (_outcome(episode_id), _outcome(episode_id, kills=0)):
        assert grade_doom_episode(observed_episode, outcome) == grade_doom_episode(
            no_steps, outcome
        )


def test_the_private_outcome_is_built_from_the_connector_outcome() -> None:
    captured = DoomEpisodeOutcome(kills=1, player_dead=False, timed_out=False, finished=True)

    outcome = DoomPrivateOutcome.from_connector("doom-doom-basic-heldout-a-r001", captured)

    assert outcome == _outcome("doom-doom-basic-heldout-a-r001")


def _heldout_cells() -> list[tuple[str, int]]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return [
        (scenario_id, seed)
        for scenario_id, scenario in manifest["scenarios"].items()
        if scenario["split"] == "held-out"
        for seed in scenario["seeds"]
    ]


@pytest.mark.parametrize(("scenario_id", "seed"), _heldout_cells())
async def test_a_scripted_oracle_solves_every_heldout_cell_within_the_frozen_budget(
    store: EpisodeStore, scenario_id: str, seed: int
) -> None:
    store.create_experiment(_experiment())

    stored, captured, result = await _run_doom(store, CenterAndFireOracle(), scenario_id, seed)

    assert result.stop_reason == "terminal_state"
    assert result.decisions_used <= HELD_OUT_DECISION_BUDGET
    assert result.primitives_used <= HELD_OUT_PRIMITIVE_BUDGET
    outcome = DoomPrivateOutcome.from_connector(stored.episode.episode_id, captured)
    assert grade_doom_episode(stored, outcome).goal_completed is True
