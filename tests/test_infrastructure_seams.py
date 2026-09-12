import pytest

from noob_agent.observability.tracing import NullTraceSink, TraceEvent
from noob_agent.skills.executor import DisabledSandboxExecutor, SandboxRequest


def test_null_trace_sink_accepts_local_events_without_remote_configuration() -> None:
    sink = NullTraceSink()

    sink.record(TraceEvent(name="episode.started", attributes={"episode_id": "ep-1"}))
    sink.flush()


@pytest.mark.asyncio
async def test_disabled_sandbox_reports_unavailable_without_executing_code() -> None:
    result = await DisabledSandboxExecutor().execute(
        SandboxRequest(source="raise RuntimeError('must not execute')")
    )

    assert result.status == "unavailable"
    assert result.code == "SANDBOX_DISABLED"
