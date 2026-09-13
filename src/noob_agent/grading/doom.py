"""The independent grader for the Doom `basic.cfg` scenario family.

Success is decided from the connector's harness-only private outcome after the
episode has ended. A policy's decisions, messages, or claims of success are
never inputs: `hackathon_plan.md` section 17 defines success as at least one
kill with the player alive when the episode ends.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from noob_agent.connectors.doom import GAME_ID, DoomEpisodeOutcome
from noob_agent.domain.records import StoredEpisode

GRADER_VERSION = "doom-basic-grader-0.1.0"


class DoomGraderModel(BaseModel):
    """Immutable base for Doom grader values."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class DoomPrivateOutcome(DoomGraderModel):
    """The private end-of-episode facts, pinned to the episode they came from."""

    episode_id: str = Field(min_length=1)
    kills: int = Field(ge=0)
    player_dead: bool
    timed_out: bool
    finished: bool

    @classmethod
    def from_connector(cls, episode_id: str, outcome: DoomEpisodeOutcome) -> DoomPrivateOutcome:
        return cls(
            episode_id=episode_id,
            kills=outcome.kills,
            player_dead=outcome.player_dead,
            timed_out=outcome.timed_out,
            finished=outcome.finished,
        )


class DoomEpisodeGrade(DoomGraderModel):
    """What the grader concluded about one Doom attempt."""

    episode_id: str = Field(min_length=1)
    goal_completed: bool
    grader_version: str = Field(min_length=1)


def grade_doom_episode(
    stored: StoredEpisode,
    outcome: DoomPrivateOutcome,
    grader_version: str = GRADER_VERSION,
) -> DoomEpisodeGrade:
    """Score one finished Doom episode from its private outcome alone."""
    episode = stored.episode
    if episode.game_id != GAME_ID:
        raise ValueError(f"The Doom grader cannot grade a {episode.game_id!r} episode.")
    if stored.outcome is None:
        raise ValueError("An episode is graded only after the episode has ended.")
    if outcome.episode_id != episode.episode_id:
        raise ValueError(
            "The private outcome belongs to a different episode than the one supplied."
        )
    return DoomEpisodeGrade(
        episode_id=episode.episode_id,
        goal_completed=outcome.kills >= 1 and not outcome.player_dead,
        grader_version=grader_version,
    )
