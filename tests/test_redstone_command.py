"""Canonical trial selection fails closed before any world/provider access."""

import importlib.util
import json
from pathlib import Path

import pytest

from noob_agent.redstone import trial


def command():
    spec = importlib.util.spec_from_file_location(
        "redstone_command", "scripts/run_redstone_trial.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--trial"],
        ["--trial", "provider"],
        ["--trial", "fixture", "--planner-model", "test/model"],
        ["--trial", "fixture", "--smoke"],
    ],
)
def test_invalid_selection_never_dispatches(args, monkeypatch):
    module = command()
    monkeypatch.setattr(module, "run_trial", lambda *a, **k: pytest.fail("dispatched"))
    with pytest.raises(SystemExit) as error:
        module.main(args)
    assert error.value.code == 2


def test_explicit_fixture_dispatch(monkeypatch, capsys):
    module = command()
    seen = []

    def run(config):
        seen.append(config)
        return type("Saved", (), {"path": Path("fixture/manifest.json")})()

    monkeypatch.setattr(module, "run_trial", run)
    assert module.main(["--trial", "fixture"]) == 2
    assert seen[0].mode == "fixture"
    assert "fixture/manifest.json" in capsys.readouterr().out


def test_explicit_lamp_repair_provider_dispatch(monkeypatch, capsys):
    module = command()
    seen = []

    def run(config):
        seen.append(config)
        return type("Saved", (), {"path": Path("demo/manifest.json")})()

    monkeypatch.setattr(module, "run_trial", run)
    assert (
        module.main(
            [
                "--trial",
                "provider",
                "--task",
                "lamp-repair",
                "--planner-model",
                "open/model",
            ]
        )
        == 2
    )
    assert seen[0].task == "lamp_repair"
    assert "demo/manifest.json" in capsys.readouterr().out


def test_provider_configuration_is_frozen_public_and_separate():
    config = trial.TrialConfiguration(mode="provider", planner_model="open/model")
    assert config.public()["planner"]["model"] == "open/model"
    assert config.public()["jev"]["model"] == "typesafe-ai/jev"
    with pytest.raises(ValueError):
        config.planner_model = "changed"
    public = config.public()
    public["planner"]["model"] = "changed"
    assert config.public()["planner"]["model"] == "open/model"
    with pytest.raises(ValueError):
        trial.TrialConfiguration(mode="provider", planner_model="https://user:secret@host")
    with pytest.raises(ValueError):
        trial.TrialConfiguration(mode="fixture", task="lamp_repair")
    assert (
        trial.TrialConfiguration(
            mode="provider", task="lamp_repair", planner_model="open/model"
        ).public()["task"]
        == "lamp_repair"
    )


def test_session_action_limit_is_a_positive_lower_cap():
    config = trial.TrialConfiguration(
        mode="provider", planner_model="open/model", session_action_limit=25
    )
    assert config.public()["session_action_limit"] == 25
    with pytest.raises(ValueError):
        trial.TrialConfiguration(
            mode="provider", planner_model="open/model", session_action_limit=20_001
        )


def test_missing_provider_credentials_retained_without_fixture_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("WANDB_API_KEY", raising=False)
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    monkeypatch.setattr(trial.RconClient, "dedicated", lambda: pytest.fail("world touched"))
    config = trial.TrialConfiguration(mode="provider", planner_model="open/model")
    first = trial.run_trial(config, tmp_path)
    second = trial.run_trial(config, tmp_path)
    assert first.path != second.path
    saved = json.loads(first.path.read_text())
    assert saved["configuration"] == config.public()
    assert saved["errors"] == [{"stage": "configuration", "type": "ValueError"}]
    assert not saved["events"]
    assert saved["final_grade"]["model_success"] is False
    assert saved["completion"]["status"] == "incomplete"


@pytest.mark.parametrize("missing", ["WANDB_API_KEY", "AI_GATEWAY_API_KEY"])
def test_each_credential_required_before_world_access(tmp_path, monkeypatch, missing):
    monkeypatch.setenv("WANDB_API_KEY", "secret-planner")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "secret-jev")
    monkeypatch.delenv(missing)
    manifest = trial.run_trial(
        trial.TrialConfiguration(mode="provider", planner_model="open/model"), tmp_path
    )
    assert manifest.data["errors"] == [{"stage": "configuration", "type": "ValueError"}]
    assert "secret-" not in manifest.path.read_text()


def test_provider_adapters_are_selected_without_fixture_or_network(tmp_path, monkeypatch):
    from noob_agent.redstone import jev, loop, planner, reset, sidecar

    monkeypatch.setenv("WANDB_API_KEY", "secret-planner")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "secret-jev")
    instances = []
    resets = []

    class Resource:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class Reset:
        def __init__(self, manifest, *args):
            self.manifest = manifest

        def restore(self):
            resets.append(self.manifest.data["run_id"])

    class Loop:
        def __init__(
            self,
            manifest,
            actions,
            model,
            evaluator,
            *,
            check,
            require_module_grading=False,
            task="computer",
            register_workshop=False,
            stop_after_module=None,
            resume_state=None,
            jev_min_interval_seconds=0,
        ):
            assert type(model) is planner.TrialWandbClient
            assert type(evaluator) is jev.JevSubprocess
            assert evaluator.command[-1] == "scripts/jev_handler.mjs"
            assert model._model.inference_model == "open/model"
            assert model._wandb.api_key == "secret-planner"
            assert check is None
            assert require_module_grading is True
            assert register_workshop is False
            assert stop_after_module is None
            assert task == "computer"
            assert jev_min_interval_seconds == 4
            instances.append((manifest, model, evaluator))

        async def run(self, observation):
            raise RuntimeError("secret-runtime-error")

    monkeypatch.setattr(trial.RconClient, "dedicated", Resource)
    monkeypatch.setattr(sidecar, "Sidecar", Resource)
    monkeypatch.setattr(reset, "TrustedReset", Reset)
    monkeypatch.setattr(loop, "TrialLoop", Loop)
    config = trial.TrialConfiguration(mode="provider", planner_model="open/model")
    first, second = trial.run_trial(config, tmp_path), trial.run_trial(config, tmp_path)
    assert instances[0][1] is not instances[1][1]
    assert instances[0][2] is not instances[1][2]
    assert resets == [first.data["run_id"]] * 2 + [second.data["run_id"]] * 2
    assert first.data["errors"] == [{"stage": "trial_loop", "type": "RuntimeError"}]
    assert "secret-" not in first.path.read_text()


@pytest.mark.parametrize("final_verified", [True, False])
def test_public_module_checkpoint_requires_verified_final_reset(
    tmp_path, monkeypatch, final_verified
):
    from noob_agent.redstone import loop, reset, sidecar

    monkeypatch.setenv("WANDB_API_KEY", "secret-planner")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "secret-jev")

    class Resource:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class Reset:
        def __init__(self, manifest, *args):
            self.manifest = manifest

        def restore(self):
            records = self.manifest.data.setdefault("resets", [])
            records.append(
                {
                    "verified": True if not records else final_verified,
                    "baseline_sha256": "fixture-baseline",
                }
            )

    class Loop:
        def __init__(self, manifest, *args, **kwargs):
            self.manifest = manifest

        async def run(self, observation):
            self.manifest.data["loop"] = {"status": "public_modules_passed"}
            self.manifest.data["milestone_4"] = {
                "status": "checks_passed",
                "construction_epoch": 1,
                "modules": {
                    name: {"grader_event": index, "construction_epoch": 1}
                    for index, name in enumerate(("register", "arithmetic", "storage", "output"))
                },
            }
            self.manifest.save()

    monkeypatch.setattr(trial.RconClient, "dedicated", Resource)
    monkeypatch.setattr(sidecar, "Sidecar", Resource)
    monkeypatch.setattr(reset, "TrustedReset", Reset)
    monkeypatch.setattr(loop, "TrialLoop", Loop)
    manifest = trial.run_trial(
        trial.TrialConfiguration(mode="provider", planner_model="open/model"), tmp_path
    )
    milestone = manifest.data["milestone_4"]
    assert milestone["status"] == ("passed" if final_verified else "checks_passed")
    assert manifest.data["final_grade"]["model_success"] is False
    assert manifest.data["completion"]["status"] == "incomplete"
    assert "secret-" not in manifest.path.read_text()


def test_failed_final_reset_recovers_with_fresh_sidecar_and_full_verification(
    tmp_path, monkeypatch
):
    from noob_agent.redstone import loop, reset, sidecar

    class Resource:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class Reset:
        def __init__(self, manifest, *args):
            self.manifest = manifest

        def restore(self):
            records = self.manifest.data.setdefault("resets", [])
            records.append({"verified": len(records) != 1, "baseline_sha256": "fixture-baseline"})
            if len(records) == 2:
                raise BrokenPipeError("sidecar died")

    class Loop:
        def __init__(self, manifest, *args, **kwargs):
            self.manifest = manifest

        async def run(self, observation):
            self.manifest.data["loop"] = {"status": "stopped"}

    monkeypatch.setattr(trial.RconClient, "dedicated", Resource)
    monkeypatch.setattr(sidecar, "Sidecar", Resource)
    monkeypatch.setattr(reset, "TrustedReset", Reset)
    monkeypatch.setattr(loop, "TrialLoop", Loop)
    manifest = trial.run_trial(trial.TrialConfiguration(mode="fixture"), tmp_path)
    assert [record["verified"] for record in manifest.data["resets"]] == [True, False, True]
    assert manifest.data["final_reset_recovery"] == {"verified": True}
    assert {"stage": "final_reset", "type": "BrokenPipeError"} in manifest.data["errors"]


def test_canonical_module_inspection_dispatch(monkeypatch):
    module = command()
    seen = []

    def run(path):
        seen.append(path)
        return type("Saved", (), {"path": Path("fixture/manifest.json")})()

    monkeypatch.setattr(module, "run_module_inspection", run, raising=False)
    assert module.main(["--inspect-module", "declaration.json"]) == 2
    assert seen == [Path("declaration.json")]


def test_canonical_behavior_negative_dispatch(monkeypatch, tmp_path):
    from types import SimpleNamespace

    module = command()
    monkeypatch.setattr(
        module,
        "run_behavior_negative_proof",
        lambda: SimpleNamespace(path=tmp_path / "manifest.json"),
    )
    assert module.main(["--behavior-negative-proof"]) == 2
