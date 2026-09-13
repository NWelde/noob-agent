"""Trace delivery is drained without becoming part of run correctness."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pydantic import JsonValue

from noob_agent.observability.tracing import TraceEvent, WeaveTraceSink
from noob_agent.runtime.sequence import HeldOutCell
from noob_agent.settings import IntegrationSettings


@dataclass
class RecordingWeaveClient:
    """Record lifecycle operations in the order the sink requests them."""

    operations: list[str] = field(default_factory=list)

    def create_call(
        self,
        op: str,
        inputs: dict[str, JsonValue],
        parent: object | None = None,
        /,
    ) -> object:
        del op, inputs, parent
        self.operations.append("create")
        return object()

    def finish_call(self, call: object, output: dict[str, JsonValue] | None = None, /) -> None:
        del call, output
        self.operations.append("finish")

    def flush(self) -> None:
        self.operations.append("flush")


def test_close_closes_an_open_episode_before_flushing_the_client() -> None:
    client = RecordingWeaveClient()
    sink = WeaveTraceSink(client)
    sink.record(TraceEvent(name="episode.started", attributes={"episode_id": "ep_0001"}))

    sink.close()

    assert client.operations == ["create", "finish", "flush"]


def test_flush_delivers_without_closing_episodes_that_are_still_running() -> None:
    """Section 22.E: one episode's flush must not close a concurrent episode's call."""
    client = RecordingWeaveClient()
    sink = WeaveTraceSink(client)
    sink.record(TraceEvent(name="episode.started", attributes={"episode_id": "ep_0001"}))

    sink.flush()

    assert client.operations == ["create", "flush"]


def test_a_client_flush_failure_does_not_escape() -> None:
    class FlushFailsClient(RecordingWeaveClient):
        def flush(self) -> None:
            self.operations.append("flush")
            raise RuntimeError("delivery failed")

    client = FlushFailsClient()
    sink = WeaveTraceSink(client)

    sink.flush()

    assert client.operations == ["flush"]


def test_a_client_without_flush_is_still_supported() -> None:
    class NoFlushClient:
        def create_call(
            self,
            op: str,
            inputs: dict[str, JsonValue],
            parent: object | None = None,
            /,
        ) -> object:
            del op, inputs, parent
            return object()

        def finish_call(self, call: object, output: dict[str, JsonValue] | None = None, /) -> None:
            del call, output

    WeaveTraceSink(NoFlushClient()).flush()


def _load_run_script():
    path = Path("scripts/run_doom_learning_sequence.py")
    spec = importlib.util.spec_from_file_location("run_doom_learning_sequence_flush", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass
class RecordingTraceSink:
    operations: list[str] = field(default_factory=list)
    fail_flush: bool = False

    def record(self, event: TraceEvent) -> None:
        del event
        self.operations.append("record")

    def flush(self) -> None:
        self.operations.append("flush")
        if self.fail_flush:
            raise RuntimeError("trace flush failed")


def _patch_run_script(
    monkeypatch: pytest.MonkeyPatch,
    sink: RecordingTraceSink,
    *,
    sequence_error: Exception | None = None,
) -> tuple[Any, dict[str, object]]:
    module = _load_run_script()
    captured: dict[str, object] = {}

    class FakeSequence:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        async def run(self, **kwargs: object) -> object:
            del kwargs
            sink.record(TraceEvent(name="model.call", attributes={}))
            if sequence_error is not None:
                raise sequence_error
            return object()

    monkeypatch.setattr(module, "build_trace_sink", lambda *args, **kwargs: sink)
    monkeypatch.setattr(module, "build_skill_executor", lambda settings: object())
    monkeypatch.setattr(module, "build_model_client", lambda model, wandb: object())
    monkeypatch.setattr(module, "LearningSequence", FakeSequence)
    monkeypatch.setattr(
        module,
        "_cells",
        lambda path: (HeldOutCell("training", 1), (HeldOutCell("heldout", 2),)),
    )
    monkeypatch.setattr(module, "_summary", lambda result, **caps: {"caps": caps})
    return module, captured


def _run_environment() -> dict[str, str]:
    return {
        "NOOB_AGENT_MODEL_PROVIDER": "wandb-inference",
        "NOOB_AGENT_INFERENCE_MODEL": "fake-model",
        "NOOB_AGENT_SANDBOX_MODE": "local",
        "NOOB_AGENT_ACTION_MAX_OUTPUT_TOKENS": "1500",
        "NOOB_AGENT_BUILDER_MAX_OUTPUT_TOKENS": "7000",
    }


def test_run_script_flushes_last_after_the_sequence_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sink = RecordingTraceSink()
    module, captured = _patch_run_script(monkeypatch, sink)

    code = module.main(["--database", str(tmp_path / "run.sqlite3")], environ=_run_environment())

    assert code == 0
    assert sink.operations == ["record", "flush"]
    assert captured["action_max_output_tokens"] == 1_500
    assert captured["builder_max_output_tokens"] == 7_000


def test_run_script_flushes_last_and_preserves_a_sequence_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = RuntimeError("sequence failed")
    sink = RecordingTraceSink(fail_flush=True)
    module, _ = _patch_run_script(monkeypatch, sink, sequence_error=original)

    with pytest.raises(RuntimeError, match="sequence failed") as raised:
        module.main(["--database", str(tmp_path / "run.sqlite3")], environ=_run_environment())

    assert raised.value is original
    assert sink.operations == ["record", "flush"]


def test_example_environment_has_a_real_wandb_base_url() -> None:
    values = dict(
        line.split("=", 1)
        for line in Path(".env.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    )

    assert values["WANDB_BASE_URL"].startswith("https://")


def test_an_empty_wandb_base_url_is_treated_as_unset() -> None:
    settings = IntegrationSettings.from_environ({"WANDB_BASE_URL": ""})

    assert settings.wandb.base_url is None
