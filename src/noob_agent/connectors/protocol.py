"""The fixed public boundary between noob-agent and a game.

The interface is transcribed from `connector_contract.md`. A connector gives an
agent the senses and controls a new player receives and returns public results
only: never private grader state, scenario answers, clean/faulty labels,
held-out configuration, or the meaning of an unfamiliar mechanic.

Only the harness calls `reset` and `close`. The agent and any generated skill
may call only the tools a manifest declares.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from noob_agent.domain.model import ConnectorManifest, Observation, StepResult, ToolRequest


class ConnectorError(RuntimeError):
    """Base class for a failure of the connector itself, not of an action."""


class ConnectorLostError(ConnectorError):
    """The connector can no longer be reached, so the episode cannot continue.

    This is distinct from a `StepResult` whose status is `failed` or `unknown`:
    those are durable results of an action that was delivered. Losing the
    connector means no result will arrive at all.
    """


@runtime_checkable
class GameConnector(Protocol):
    """Every game adapter implements these asynchronous methods."""

    async def manifest(self) -> ConnectorManifest:
        """Return the frozen connector version, modes, and primitive tools.

        The manifest is hashed and stored with every episode and cannot change
        after `reset`.
        """
        ...

    async def reset(self, scenario_id: str, seed: int) -> Observation:
        """Restore the declared initial state and return its sequence-0 snapshot."""
        ...

    async def step(self, request: ToolRequest) -> StepResult:
        """Perform one primitive and return its one durable result."""
        ...

    async def is_terminal(self) -> bool:
        """Report whether the episode has reached a terminal game state."""
        ...

    async def close(self) -> None:
        """Release the connector's resources."""
        ...
