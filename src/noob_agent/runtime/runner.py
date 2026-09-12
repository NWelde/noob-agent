"""The bounded cold-episode runner.

One agent decision calls one primitive. Every observation, request, and result
is recorded through the SQLite store before another action is allowed, and the
episode always ends with a recorded stop reason.

The runner sees only the public connector surface. It performs no grading, calls
no model API, executes no generated skill, and sends nothing to a remote
service.
"""

from __future__ import annotations

import json
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from pydantic import ValidationError

from noob_agent.connectors import ConnectorLostError, GameConnector
from noob_agent.domain.model import Observation, StepResult, ToolRequest
from noob_agent.domain.records import (
    EpisodeOutcome,
    EpisodeRecord,
    EpisodeSplit,
    ExperimentRecord,
    StepRecord,
    StopReason,
)
from noob_agent.storage import EpisodeStore, StorageError

# Three identical failed calls with no intervening public state change end the
# episode, per connector_contract.md.
REPEATED_FAILURE_LIMIT = 3


class Policy(Protocol):
    """Chooses the next primitive from the latest public observation."""

    async def choose(self, observation: Observation) -> ToolRequest: ...


class Clock(Protocol):
    """Wall time for records, monotonic time for the budget."""

    def now(self) -> datetime: ...

    def monotonic_ms(self) -> int: ...


class SystemClock:
    """The default clock: UTC for records, a monotonic host clock for budgets."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic_ms(self) -> int:
        return time.monotonic_ns() // 1_000_000


@dataclass(frozen=True)
class EpisodeResult:
    """What one bounded attempt spent and why it stopped."""

    episode_id: str
    stop_reason: StopReason
    terminal: bool
    decisions_used: int
    primitives_used: int


@dataclass
class _FailureStreak:
    """Tracks identical failed calls that changed no public state."""

    signature: tuple[str, str] | None = None
    count: int = 0

    def record(self, request: ToolRequest, result: StepResult) -> int:
        if result.status != "failed" or result.state_changed is True:
            self.signature = None
            self.count = 0
            return 0
        # Deliberately excludes action_id: every request carries a fresh one, so
        # "identical" must mean the same tool with the same arguments.
        signature = (request.tool_name, json.dumps(request.arguments, sort_keys=True))
        if signature == self.signature:
            self.count += 1
        else:
            self.signature = signature
            self.count = 1
        return self.count


class EpisodeRunner:
    """Runs one bounded episode against a public connector."""

    def __init__(
        self,
        *,
        connector: GameConnector,
        store: EpisodeStore,
        policy: Policy,
        clock: Clock | None = None,
    ) -> None:
        self._connector = connector
        self._store = store
        self._policy = policy
        self._clock = clock if clock is not None else SystemClock()

    async def run(
        self,
        *,
        experiment: ExperimentRecord,
        scenario_id: str,
        seed: int,
        split: EpisodeSplit = "training",
    ) -> EpisodeResult:
        """Reset, act within budget, and record the episode and its outcome."""
        try:
            result = await self._run(
                experiment=experiment, scenario_id=scenario_id, seed=seed, split=split
            )
        except BaseException:
            # Closing must not replace the error that is already propagating.
            with suppress(Exception):
                await self._connector.close()
            raise
        await self._connector.close()
        return result

    async def _run(
        self,
        *,
        experiment: ExperimentRecord,
        scenario_id: str,
        seed: int,
        split: EpisodeSplit,
    ) -> EpisodeResult:
        manifest = await self._connector.manifest()
        started_at = self._clock.now()
        started_ms = self._clock.monotonic_ms()

        # Losing the connector during reset propagates: no episode row exists
        # yet, so there is nothing to record a stop reason against.
        reset_observation = await self._connector.reset(scenario_id, seed)

        # The connector owns episode identity; the harness adopts it.
        episode_id = reset_observation.episode_id
        self._store.create_episode(
            EpisodeRecord(
                episode_id=episode_id,
                experiment_id=experiment.experiment_id,
                game_id=reset_observation.game_id,
                scenario_id=reset_observation.scenario_id,
                seed=seed,
                split=split,
                manifest=manifest,
                reset_observation=reset_observation,
                started_at=started_at,
            )
        )

        decisions_used = 0
        primitives_used = 0
        terminal = reset_observation.terminal
        sequence = 0
        streak = _FailureStreak()
        observation = reset_observation
        stop_reason: StopReason | None = "terminal_state" if terminal else None

        while stop_reason is None:
            if decisions_used >= experiment.decision_budget:
                stop_reason = "decision_limit"
                break
            if self._clock.monotonic_ms() - started_ms >= experiment.wall_time_budget_ms:
                stop_reason = "wall_time_limit"
                break

            request = await self._policy.choose(observation)
            decisions_used += 1
            sequence += 1

            try:
                result = await self._connector.step(request)
            except ConnectorLostError:
                # No durable result arrives, so nothing is recorded for this action.
                stop_reason = "connector_lost"
                break

            # A result the harness cannot durably record is a result whose effect
            # on the game cannot be confirmed, so the episode ends the same way an
            # unknown action ends it: recorded, and never retried. Crashing here
            # would leave an episode row with no outcome in the source of truth.
            try:
                if result.sequence != sequence:
                    raise ValueError(
                        f"The connector returned sequence {result.sequence}, expected {sequence}."
                    )
                self._store.append_step(
                    StepRecord(
                        episode_id=episode_id,
                        sequence=result.sequence,
                        request=request,
                        result=result,
                    )
                )
            except (ValidationError, ValueError, StorageError):
                stop_reason = "unknown_result"
                break

            primitives_used += result.primitive_actions_charged
            observation = result.observation
            terminal = result.observation.terminal

            if result.code == "CONNECTOR_LOST":
                stop_reason = "connector_lost"
            elif result.status == "unknown":
                # An unknown action may have changed the game, so it is never
                # retried and the episode ends as an infrastructure ambiguity.
                stop_reason = "unknown_result"
            elif terminal:
                stop_reason = "terminal_state"
            elif streak.record(request, result) >= REPEATED_FAILURE_LIMIT:
                stop_reason = "repeated_failure"
            elif primitives_used >= experiment.primitive_budget:
                stop_reason = "primitive_limit"

        self._store.finalize_episode(
            EpisodeOutcome(
                episode_id=episode_id,
                stop_reason=stop_reason,
                terminal=terminal,
                total_decisions=decisions_used,
                total_primitives=primitives_used,
                finished_at=self._clock.now(),
            )
        )
        return EpisodeResult(
            episode_id=episode_id,
            stop_reason=stop_reason,
            terminal=terminal,
            decisions_used=decisions_used,
            primitives_used=primitives_used,
        )
