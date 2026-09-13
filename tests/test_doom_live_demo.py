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
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_run_script()
    monkeypatch.setattr(module.sys, "platform", "linux")
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


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_live_demo_does_not_require_display_variables_off_linux(platform: str) -> None:
    module = _load_run_script()

    assert module._has_display({}, platform=platform) is True


def test_live_demo_on_linux_needs_x11_or_wayland() -> None:
    module = _load_run_script()

    assert module._has_display({}, platform="linux") is False
    assert module._has_display({"DISPLAY": ":0"}, platform="linux") is True
    assert module._has_display({"WAYLAND_DISPLAY": "wayland-0"}, platform="linux") is True


def test_run_refuses_early_without_a_wandb_api_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_run_script()
    database = tmp_path / "never-created.sqlite3"
    environ = {key: value for key, value in _live_environment().items() if key != "WANDB_API_KEY"}

    code = module.main(["--database", str(database)], environ=environ)

    assert code == 2
    assert "WANDB_API_KEY" in capsys.readouterr().err
    assert not database.exists()


def test_run_names_missing_integration_packages_and_the_install_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_run_script()
    monkeypatch.setattr(module, "_missing_packages", lambda settings: ["openai", "weave"])
    database = tmp_path / "never-created.sqlite3"

    code = module.main(["--database", str(database)], environ=_live_environment())

    assert code == 2
    error = capsys.readouterr().err
    assert "openai, weave" in error
    assert "uv sync --group dev --group integrations" in error
    assert not database.exists()


def test_missing_packages_checks_weave_only_when_tracing_is_enabled() -> None:
    module = _load_run_script()
    settings = module.IntegrationSettings.from_environ(
        {**_live_environment(), "NOOB_AGENT_TRACE_MODE": "weave", "WEAVE_DISABLED": "false"}
    )
    untraced = module.IntegrationSettings.from_environ(_live_environment())

    assert module._missing_packages(settings, find_spec=lambda name: None) == ["openai", "weave"]
    assert module._missing_packages(untraced, find_spec=lambda name: None) == ["openai"]
    assert module._missing_packages(settings, find_spec=lambda name: object()) == []


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
        "WANDB_API_KEY": "test-key",
        "NOOB_AGENT_MODEL_PROVIDER": "wandb-inference",
        "NOOB_AGENT_INFERENCE_MODEL": "some-model",
        "NOOB_AGENT_SANDBOX_MODE": "local",
    }


def _isolate_main(monkeypatch: pytest.MonkeyPatch, module: Any, *, block: bool) -> _TraceSink:
    sink = _TraceSink()

    class FakeSequence:
        def __init__(self, **kwargs: object) -> None:
            assert kwargs["condition"] == "non-benchmark-live-demo"
            assert kwargs["persistent_connector"] is True

        async def run(self, **kwargs: object) -> object:
            del kwargs
            if block:
                await asyncio.Event().wait()
            return object()

    monkeypatch.setattr(module, "LearningSequence", FakeSequence)
    monkeypatch.setattr(module, "_missing_packages", lambda settings: [])
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


def test_live_view_requires_live_demo(capsys: pytest.CaptureFixture[str]) -> None:
    module = _load_run_script()

    with pytest.raises(SystemExit) as raised:
        module._parse_args(["--live-view"])

    assert raised.value.code == 2
    assert "live-demo" in capsys.readouterr().err.lower()


def test_live_view_refuses_to_start_with_tracing_disabled(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_run_script()
    _isolate_main(monkeypatch, module, block=False)
    started: list[object] = []
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: started.append(a))

    code = module.main(
        ["--live-demo", "--live-view", "--database", str(tmp_path / "demo.sqlite3")],
        environ=_live_environment(),
    )

    assert code == 2
    assert "tracing" in capsys.readouterr().err.lower()
    assert started == []


class _ViewProcess:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def terminate(self) -> None:
        self._events.append("view terminated")

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return 0


@pytest.mark.parametrize("block", [False, True], ids=["completed", "deadline"])
def test_live_view_starts_for_the_run_and_stops_after_the_final_flush(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    block: bool,
) -> None:
    module = _load_run_script()
    sink = _isolate_main(monkeypatch, module, block=block)
    events: list[str] = []
    original_flush = sink.flush

    def flush() -> None:
        events.append("trace flushed")
        original_flush()

    monkeypatch.setattr(sink, "flush", flush)
    launched: list[list[str]] = []

    def popen(command: list[str], **kwargs: object) -> _ViewProcess:
        del kwargs
        launched.append(command)
        events.append("view started")
        return _ViewProcess(events)

    monkeypatch.setattr(module.subprocess, "Popen", popen)
    monkeypatch.setattr(module, "LIVE_VIEW_LINGER_SECONDS", 0.0)
    environment = {
        **_live_environment(),
        "NOOB_AGENT_TRACE_MODE": "weave",
        "WEAVE_DISABLED": "false",
    }

    module.main(
        [
            "--live-demo",
            "--live-view",
            "--deadline-seconds",
            "0.001" if block else "1",
            "--sequence-id",
            "doom-live-demo-view-test",
            "--database",
            str(tmp_path / "demo.sqlite3"),
        ],
        environ=environment,
    )

    assert events == ["view started", "trace flushed", "view terminated"]
    (command,) = launched
    assert command[1].endswith("live_reasoning_view.py")
    assert command[command.index("--run-id") + 1] == "doom-live-demo-view-test"
    assert "http://127.0.0.1:" in capsys.readouterr().out


def test_demo_environment_template_enables_a_runnable_traced_doom_sequence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    values = dict(
        line.split("=", 1)
        for line in Path(".env.demo.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    assert values["WANDB_API_KEY"] == ""
    module = _load_run_script()
    settings = module.IntegrationSettings.from_environ(values)
    assert settings.model.provider == "wandb-inference"
    assert settings.model.inference_model
    assert settings.trace.enabled
    assert settings.sandbox.mode == "local"

    _isolate_main(monkeypatch, module, block=False)
    code = module.main(
        ["--live-demo", "--deadline-seconds", "5", "--database", str(tmp_path / "demo.sqlite3")],
        environ={**values, "WANDB_API_KEY": "judge-key", "DISPLAY": ":0"},
    )

    assert code == 0
    assert capsys.readouterr().err == ""
