"""Provider-neutral trace seam with an offline default.

SQLite is the source of truth. Tracing is a best-effort mirror that runs only
after local episode state is durable, so a slow, misconfigured, or unreachable
trace backend can never change a recorded attempt. Nothing here authenticates
or imports an optional package unless `TraceSettings.enabled` is true, which it
is not by default.

The mirror is shaped as a nested call tree: one episode call with one child call
per recorded step, which is what makes an attempt readable as a single trace
rather than a flat event log.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from contextlib import suppress
from typing import Protocol, cast

from pydantic import BaseModel, JsonValue

from noob_agent.domain.records import EpisodeOutcome, EpisodeRecord, StepRecord
from noob_agent.settings import TraceSettings, WandbSettings

EPISODE_STARTED = "episode.started"
EPISODE_STEP = "episode.step"
EPISODE_FINISHED = "episode.finished"

# The names the mirrored calls carry in the remote trace tree.
EPISODE_OP_NAME = "noob_agent.episode"
STEP_OP_NAME = "noob_agent.step"


class TraceEvent(BaseModel):
    """A local event eligible for later best-effort remote mirroring."""

    name: str
    attributes: dict[str, JsonValue]


class TraceSink(Protocol):
    """Accepts local events after the authoritative store records them."""

    def record(self, event: TraceEvent) -> None:
        """Queue or send a trace event without changing local episode state."""

    def flush(self) -> None:
        """Finish best-effort trace delivery without affecting correctness."""


class NullTraceSink:
    """Offline trace sink used until a Weave adapter is explicitly enabled."""

    def record(self, event: TraceEvent) -> None:
        del event

    def flush(self) -> None:
        return None


def episode_started_event(record: EpisodeRecord) -> TraceEvent:
    """Describe an episode that is already durable in the local store."""
    return TraceEvent(
        name=EPISODE_STARTED,
        attributes={
            "episode_id": record.episode_id,
            "experiment_id": record.experiment_id,
            "game_id": record.game_id,
            "scenario_id": record.scenario_id,
            "seed": record.seed,
            "split": record.split,
            "connector_version": record.manifest.connector_version,
            "observation_mode": record.manifest.observation_mode,
            "timing_model": record.manifest.timing_model,
            "tools": [tool.name for tool in record.manifest.tools],
            "public_goal": record.reset_observation.public_goal,
            "started_at": record.started_at.isoformat(),
        },
    )


def step_event(record: StepRecord) -> TraceEvent:
    """Describe one durable request/result pair exactly as it was recorded."""
    result = record.result
    return TraceEvent(
        name=EPISODE_STEP,
        attributes={
            "episode_id": record.episode_id,
            "sequence": record.sequence,
            "action_id": record.request.action_id,
            "tool_name": record.request.tool_name,
            "arguments": cast(JsonValue, record.request.arguments),
            "status": result.status,
            "code": result.code,
            "message": result.message,
            "state_changed": result.state_changed,
            "primitive_actions_charged": result.primitive_actions_charged,
            "logical_duration": result.logical_duration,
            "wall_time_ms": result.wall_time_ms,
            "logical_time": result.observation.logical_time,
            "terminal": result.observation.terminal,
            "terminal_reason": result.observation.terminal_reason,
        },
    )


def episode_finished_event(outcome: EpisodeOutcome) -> TraceEvent:
    """Describe the written-once outcome of an episode."""
    return TraceEvent(
        name=EPISODE_FINISHED,
        attributes={
            "episode_id": outcome.episode_id,
            "stop_reason": outcome.stop_reason,
            "terminal": outcome.terminal,
            "total_decisions": outcome.total_decisions,
            "total_primitives": outcome.total_primitives,
            "finished_at": outcome.finished_at.isoformat(),
        },
    )


class WeaveClient(Protocol):
    """The small part of a Weave client this mirror uses.

    Arguments are positional-only so a real client's parameter names are free to
    differ from the ones named here.
    """

    def create_call(
        self, op: str, inputs: dict[str, JsonValue], parent: object | None = None, /
    ) -> object:
        """Open a call, optionally nested inside `parent`, and return its handle."""
        ...

    def finish_call(self, call: object, output: dict[str, JsonValue] | None = None, /) -> None:
        """Close a call previously opened by `create_call`."""
        ...


ClientFactory = Callable[[str], WeaveClient]


class WeaveTraceSink:
    """Mirrors episode events into one nested Weave call tree.

    Every interaction with the client is best effort. A client that is
    unreachable, or whose API does not match `WeaveClient`, degrades to no
    tracing; it never raises into the episode loop, because the local record is
    already durable by the time an event arrives here.
    """

    def __init__(self, client: WeaveClient) -> None:
        self._client = client
        self._episode_call: object | None = None

    def record(self, event: TraceEvent) -> None:
        with suppress(Exception):
            if event.name == EPISODE_STARTED:
                self._episode_call = self._client.create_call(
                    EPISODE_OP_NAME, event.attributes, None
                )
            elif event.name == EPISODE_STEP:
                # A recorded step is already complete, so its call opens and
                # closes together, nested under the episode.
                call = self._client.create_call(STEP_OP_NAME, event.attributes, self._episode_call)
                self._client.finish_call(call, event.attributes)
            elif event.name == EPISODE_FINISHED:
                self._finish_episode(event.attributes)

    def flush(self) -> None:
        """Close an episode call that never received its outcome event."""
        with suppress(Exception):
            self._finish_episode(None)

    def _finish_episode(self, output: dict[str, JsonValue] | None) -> None:
        call, self._episode_call = self._episode_call, None
        if call is not None:
            self._client.finish_call(call, output)


def _weave_client(project: str) -> WeaveClient:
    """Initialize Weave lazily, by name, so importing this module never needs it."""
    module = importlib.import_module("weave")
    return cast(WeaveClient, module.init(project))


def build_trace_sink(
    trace: TraceSettings,
    *,
    wandb: WandbSettings | None = None,
    client_factory: ClientFactory | None = None,
) -> TraceSink:
    """Return the configured mirror, or the offline sink when tracing is disabled.

    Weave is touched only when `trace.enabled` is true, so the default
    configuration neither imports the optional dependency nor authenticates.
    """
    if not trace.enabled:
        return NullTraceSink()
    project = (wandb or WandbSettings()).project
    factory = client_factory if client_factory is not None else _weave_client
    return WeaveTraceSink(factory(project))
