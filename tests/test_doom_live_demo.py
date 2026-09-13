"""The visible Doom mode is explicitly separate from benchmark runs."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


def _load_run_script():
    path = Path("scripts/run_doom_learning_sequence.py")
    spec = importlib.util.spec_from_file_location("run_doom_learning_sequence_live_demo", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_normal_mode_keeps_the_benchmark_defaults() -> None:
    module = _load_run_script()
    options = module._parse_args([])

    assert options.live_demo is False
    assert module._database_for(options) == module.DEFAULT_DATABASE
    assert module._condition_for(options) == "self-improving"
    connector = module._connector_factory(options)()
    assert connector._settings.window_visible is False
    assert connector._settings.realtime is False


def test_live_demo_is_visible_realtime_separate_and_capped_at_ten_minutes() -> None:
    module = _load_run_script()
    options = module._parse_args(["--live-demo"])

    assert options.live_demo is True
    assert options.deadline_seconds == 600.0
    assert module._database_for(options) == Path(".noob-agent/doom-live-demo.sqlite3")
    assert module._condition_for(options) == "non-benchmark-live-demo"
    assert module._sequence_prefix(options) == "doom-live-demo"
    connector = module._connector_factory(options)()
    assert connector._settings.window_visible is True
    assert connector._settings.realtime is True


@pytest.mark.parametrize("deadline", ["0", "-1", "601"])
def test_live_demo_refuses_an_invalid_or_overlong_deadline(
    deadline: str, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_run_script()

    with pytest.raises(SystemExit) as raised:
        module._parse_args(["--live-demo", "--deadline-seconds", deadline])

    assert raised.value.code == 2
    assert "deadline" in capsys.readouterr().err.lower()


def test_deadline_option_is_live_demo_only(capsys: pytest.CaptureFixture[str]) -> None:
    module = _load_run_script()

    with pytest.raises(SystemExit) as raised:
        module._parse_args(["--deadline-seconds", "30"])

    assert raised.value.code == 2
    assert "live-demo" in capsys.readouterr().err.lower()


def test_live_demo_refuses_to_start_without_a_graphical_display(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_run_script()
    database = tmp_path / "never-created.sqlite3"
    environ = {
        "NOOB_AGENT_MODEL_PROVIDER": "wandb-inference",
        "NOOB_AGENT_INFERENCE_MODEL": "some-model",
        "NOOB_AGENT_SANDBOX_MODE": "local",
    }

    code = module.main(["--live-demo", "--database", str(database)], environ=environ)

    assert code == 2
    assert "display" in capsys.readouterr().err.lower()
    assert not database.exists()


class _TraceSink:
    def __init__(self) -> None:
        self.flushes = 0

    def record(self, event: object) -> None:
        del event

    def flush(self) -> None:
        self.flushes += 1


def _live_environment() -> dict[str, str]:
    return {
        "DISPLAY": ":0",
        "NOOB_AGENT_MODEL_PROVIDER": "wandb-inference",
        "NOOB_AGENT_INFERENCE_MODEL": "some-model",
        "NOOB_AGENT_SANDBOX_MODE": "local",
    }


def _isolate_main(monkeypatch: pytest.MonkeyPatch, module: Any, *, block: bool) -> _TraceSink:
    sink = _TraceSink()

    class FakeSequence:
        def __init__(self, **kwargs: object) -> None:
            assert kwargs["condition"] == "non-benchmark-live-demo"
            assert kwargs["keep_training_connector_open_during_builder"] is True

        async def run(self, **kwargs: object) -> object:
            del kwargs
            if block:
                await asyncio.Event().wait()
            return object()

    monkeypatch.setattr(module, "LearningSequence", FakeSequence)
    monkeypatch.setattr(module, "build_trace_sink", lambda *args, **kwargs: sink)
    monkeypatch.setattr(module, "build_model_client", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        module,
        "build_skill_executor",
        lambda *args, **kwargs: SimpleNamespace(isolation_note="test"),
    )
    monkeypatch.setattr(
        module,
        "_cells",
        lambda path: (SimpleNamespace(scenario_id="training", seed=1), (object(),)),
    )
    monkeypatch.setattr(module, "_summary", lambda *args, **kwargs: {"sequence_id": "demo"})
    return sink


def test_live_demo_success_is_labeled_and_flushes_tracing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_run_script()
    sink = _isolate_main(monkeypatch, module, block=False)
    database = tmp_path / "demo.sqlite3"

    code = module.main(
        ["--live-demo", "--deadline-seconds", "1", "--database", str(database)],
        environ=_live_environment(),
    )

    assert code == 0
    assert sink.flushes == 1
    output = capsys.readouterr().out
    assert "NON-BENCHMARK LIVE DEMO" in output
    payload = json.loads(output[output.index("{") :])
    assert payload["run_kind"] == "non-benchmark-live-demo"
    assert payload["status"] == "completed"


def test_live_demo_deadline_cancels_the_sequence_and_flushes_tracing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_run_script()
    sink = _isolate_main(monkeypatch, module, block=True)
    database = tmp_path / "demo.sqlite3"

    code = module.main(
        ["--live-demo", "--deadline-seconds", "0.001", "--database", str(database)],
        environ=_live_environment(),
    )

    assert code == 124
    assert sink.flushes == 1
    output = capsys.readouterr().out
    payload = json.loads(output[output.index("{") :])
    assert payload["run_kind"] == "non-benchmark-live-demo"
    assert payload["status"] == "deadline_exceeded"
    assert payload["deadline_seconds"] == 0.001
