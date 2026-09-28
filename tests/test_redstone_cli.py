import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run_redstone_trial.py"
SPEC = importlib.util.spec_from_file_location("run_redstone_trial", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
run_redstone_trial = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_redstone_trial)


@pytest.mark.parametrize(
    ("extra", "expected"),
    [([], True), (["--keep-agent-connected"], True), (["--disconnect-after-trial"], False)],
)
def test_provider_trials_keep_agent_connected_by_default(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], extra, expected: bool
) -> None:
    captured = {}

    def run_trial(config):
        captured["config"] = config
        return SimpleNamespace(path=Path("manifest.json"))

    monkeypatch.setattr(run_redstone_trial, "run_trial", run_trial)
    assert (
        run_redstone_trial.main(
            ["--trial", "provider", "--planner-model", "model", *extra]
        )
        == 2
    )
    assert captured["config"].keep_agent_connected is expected
    assert "manifest.json" in capsys.readouterr().out


def test_lamp_trial_dispatches_saved_manifest_for_continuation(monkeypatch, capsys):
    captured = {}
    saved = Path("old-manifest.json")

    def run_trial(config, *, resume=None):
        captured.update(config=config, resume=resume)
        return SimpleNamespace(path=Path("manifest.json"))

    monkeypatch.setattr(run_redstone_trial, "run_trial", run_trial)
    assert run_redstone_trial.main(
        [
            "--trial",
            "provider",
            "--task",
            "lamp-repair",
            "--planner-model",
            "model",
            "--resume",
            str(saved),
        ]
    ) == 2
    assert captured["resume"] == saved
    assert captured["config"].task == "lamp_repair"
    assert "manifest.json" in capsys.readouterr().out
