"""Provider-neutral trace seam with an offline default."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, JsonValue


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
