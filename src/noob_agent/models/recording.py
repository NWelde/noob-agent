"""A model client wrapper that records every call it passes through.

`RecordingModelClient` sits between an agent and its provider client. After
each call returns or fails, it writes one `ModelCallRecord` to the local store
and then mirrors it to the trace sink on a best-effort basis. It never changes
the request, the response, or the error: the agent sees exactly what it would
have seen without the wrapper, so decisions and budgets are charged as before.

One wrapper serves one role. An Action wrapper links each call to the episode
the runner has open for its experiment, and to the `action_id` the agent issues
for that reply. The agent numbers its decisions `a_0001`, `a_0002`, and so on
per episode, counting only calls that returned, and the wrapper follows the
same count. A Builder wrapper is given the authoring episode; its first call is
the build and every later call is a repair.

Nothing here reads, stores, or traces a credential, and no record is ever
handed back to an agent.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import uuid4

from noob_agent.domain.records import ModelCallPurpose, ModelCallRecord
from noob_agent.models.client import ModelClient, ModelRequest, ModelResponse
from noob_agent.observability.tracing import TraceSink, model_call_event
from noob_agent.storage import EpisodeStore, StorageError

ModelRole = Literal["action", "builder"]


class Clock(Protocol):
    """Wall time for the record, monotonic time for latency."""

    def now(self) -> datetime: ...

    def monotonic_ms(self) -> int: ...


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic_ms(self) -> int:
        return time.monotonic_ns() // 1_000_000


class RecordingModelClient:
    """Completes through an inner client and records one row per call."""

    def __init__(
        self,
        inner: ModelClient,
        *,
        store: EpisodeStore,
        experiment_id: str,
        role: ModelRole,
        model_id: str,
        episode_id: str | None = None,
        clock: Clock | None = None,
        trace: TraceSink | None = None,
        episode_source: Callable[[], str | None] | None = None,
        first_purpose: ModelCallPurpose = "build",
    ) -> None:
        if role == "builder" and episode_id is None:
            raise ValueError("A Builder recorder needs the authoring episode.")
        self._inner = inner
        self._store = store
        self._experiment_id = experiment_id
        self._role = role
        self._model_id = model_id
        self._episode_id = episode_id
        self._clock = clock if clock is not None else _SystemClock()
        self._trace = trace
        # Names the acting episode when several episodes of the experiment are open.
        self._episode_source = episode_source
        # A Builder recorder's first call is a build, or a refinement of an accepted skill.
        self._first_purpose: ModelCallPurpose = first_purpose
        self._provider = str(getattr(inner, "provider", "unknown"))
        self._builder_calls = 0
        self._actions_per_episode: dict[str, int] = {}

    def __repr__(self) -> str:
        return (
            f"RecordingModelClient(role={self._role!r}, experiment_id={self._experiment_id!r}, "
            f"provider={self._provider!r})"
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Run the inner call, then record it whether it returned or failed."""
        purpose, episode_id = self._purpose_and_episode()
        started_at = self._clock.now()
        started_ms = self._clock.monotonic_ms()
        try:
            response = await self._inner.complete(request)
        except (Exception, asyncio.CancelledError) as error:
            latency_ms = max(0, self._clock.monotonic_ms() - started_ms)
            # The provider's error is the one that matters; a record that cannot
            # be written must not replace it.
            with suppress(StorageError):
                self._write(
                    self._record(
                        request,
                        purpose=purpose,
                        episode_id=episode_id,
                        started_at=started_at,
                        latency_ms=latency_ms,
                        error=f"{type(error).__name__}: {error}",
                    )
                )
            raise
        latency_ms = max(0, self._clock.monotonic_ms() - started_ms)
        self._write(
            self._record(
                request,
                purpose=purpose,
                episode_id=episode_id,
                started_at=started_at,
                latency_ms=latency_ms,
                response=response,
                action_id=self._next_action_id(episode_id) if purpose == "action" else None,
            )
        )
        return response

    def _purpose_and_episode(self) -> tuple[ModelCallPurpose, str | None]:
        if self._role == "builder":
            self._builder_calls += 1
            return (self._first_purpose if self._builder_calls == 1 else "repair"), self._episode_id
        if self._episode_source is not None:
            return "action", self._episode_source()
        return "action", self._store.open_episode_id(self._experiment_id)

    def _next_action_id(self, episode_id: str | None) -> str | None:
        if episode_id is None:
            return None
        issued = self._actions_per_episode.get(episode_id, 0) + 1
        self._actions_per_episode[episode_id] = issued
        return f"a_{issued:04d}"

    def _record(
        self,
        request: ModelRequest,
        *,
        purpose: ModelCallPurpose,
        episode_id: str | None,
        started_at: datetime,
        latency_ms: int,
        response: ModelResponse | None = None,
        action_id: str | None = None,
        error: str | None = None,
    ) -> ModelCallRecord:
        reported = response is not None and response.usage_reported
        return ModelCallRecord(
            call_id=f"mc_{uuid4().hex}",
            experiment_id=self._experiment_id,
            purpose=purpose,
            episode_id=episode_id,
            action_id=action_id,
            provider=self._provider,
            model_id=response.model_id if response is not None else self._model_id,
            system=request.system,
            prompt=request.prompt,
            max_output_tokens=request.max_output_tokens,
            temperature=request.temperature,
            response_text=response.text if response is not None else None,
            reasoning=response.reasoning if response is not None else None,
            finish_reason=response.finish_reason if response is not None else None,
            input_tokens=response.input_tokens if response is not None and reported else None,
            output_tokens=response.output_tokens if response is not None and reported else None,
            latency_ms=latency_ms,
            error=error,
            started_at=started_at,
            request_options=request.options(),
        )

    def _write(self, record: ModelCallRecord) -> None:
        self._store.record_model_call(record)
        if self._trace is not None:
            with suppress(Exception):
                self._trace.record(model_call_event(record))
