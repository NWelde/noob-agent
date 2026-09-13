"""The Doom replay viewer: a recorded episode, replayed and checked step by step.

This is step 19b of `hackathon_plan.md` section 19. The replay drives a real
headless `DoomConnector` with the recorded requests, reads only a copy of the
store, stops at the first step that differs from the record, and never reaches
a model, the Builder, a grader, or a skill executor.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest
from fakes.connector import FakeClock, ScriptedConnector, ScriptedPolicy, ScriptedStep

from noob_agent.connectors.doom import CONNECTOR_VERSION, DoomConnector, DoomSettings
from noob_agent.domain.records import ExperimentRecord
from noob_agent.runtime import EpisodeRunner
from noob_agent.storage import EpisodeStore

SCRIPT = Path("scripts/replay_doom_episode.py")
STARTED_AT = datetime(2026, 9, 12, 23, 30, 0, tzinfo=UTC)
TRAINING = "doom-basic-training"
SEED = 20260912
CALLS: tuple[tuple[str, dict[str, object]], ...] = (
    ("observe", {}),
    ("unusable_reply", {}),
    ("turn_left", {"degrees": 91}),
    ("turn_right", {"degrees": 12}),
    ("move_forward", {"ticks": 4}),
    ("attack", {"ticks": 3}),
    ("wait", {"ticks": 5}),
)


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("replay_doom_episode", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _experiment(experiment_id: str, connector_version: str) -> ExperimentRecord:
    return ExperimentRecord(
        experiment_id=experiment_id,
        model_id="scripted-policy",
        condition="replay-test",
        connector_version=connector_version,
        decision_budget=len(CALLS),
        primitive_budget=40,
        wall_time_budget_ms=180_000,
        created_at=STARTED_AT,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def recorded(tmp_path: Path) -> tuple[Path, str]:
    """A real headless Doom episode recorded through the ordinary runner."""
    database = tmp_path / "recorded.sqlite3"
    with EpisodeStore.open(database) as store:
        experiment = _experiment("exp_replay", CONNECTOR_VERSION)
        store.create_experiment(experiment)
        runner = EpisodeRunner(
            connector=DoomConnector(),
            store=store,
            policy=ScriptedPolicy(*CALLS),
            clock=FakeClock(wall=STARTED_AT),
        )
        result = asyncio.run(runner.run(experiment=experiment, scenario_id=TRAINING, seed=SEED))
    return database, result.episode_id


class _NoGame:
    """Stands in for the connector where no game may start."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("A game was started.")


def _run(
    module: ModuleType, argv: list[str], capsys: pytest.CaptureFixture[str]
) -> tuple[int, str, str]:
    code = module.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# --- A faithful replay ----------------------------------------------------------


def test_a_recorded_episode_replays_with_every_step_matched(
    recorded: tuple[Path, str], capsys: pytest.CaptureFixture[str]
) -> None:
    database, episode_id = recorded
    before = _sha256(database)

    code, out, err = _run(
        _load_script(),
        ["--database", str(database), "--episode-id", episode_id, "--headless"],
        capsys,
    )

    assert code == 0, err
    assert "REPLAY" in out
    assert "not a live model run" in out
    assert episode_id in out and TRAINING in out and str(SEED) in out
    step_lines = [line for line in out.splitlines() if line.startswith("[step ")]
    assert len(step_lines) == len(CALLS)
    assert all("match" in line and "MISMATCH" not in line for line in step_lines)
    assert "INVALID_TOOL" in out and "INVALID_ARGUMENT" in out
    assert f"all {len(CALLS)} steps matched" in out
    assert _sha256(database) == before


def test_a_changed_record_stops_the_replay_at_that_step(
    recorded: tuple[Path, str], capsys: pytest.CaptureFixture[str]
) -> None:
    database, episode_id = recorded
    connection = sqlite3.connect(database)
    (result_json,) = connection.execute(
        "SELECT result_json FROM step WHERE episode_id = ? AND sequence = 5", (episode_id,)
    ).fetchone()
    result = json.loads(result_json)
    result["observation"]["status"]["ammo"] = 999.0
    connection.execute(
        "UPDATE step SET result_json = ? WHERE episode_id = ? AND sequence = 5",
        (json.dumps(result), episode_id),
    )
    connection.commit()
    connection.close()

    code, out, err = _run(
        _load_script(),
        ["--database", str(database), "--episode-id", episode_id, "--headless"],
        capsys,
    )

    assert code == 1
    report = out + err
    assert "MISMATCH" in report
    assert "sequence 5" in report
    assert "999.0" in report
    step_lines = [line for line in out.splitlines() if line.startswith(("[step 6]", "[step 7]"))]
    assert step_lines == [], "The replay continued past the first mismatch."


def test_without_headless_the_replay_opens_a_paced_window_and_pauses_each_step(
    recorded: tuple[Path, str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, episode_id = recorded
    module = _load_script()
    settings_seen: list[DoomSettings] = []
    pauses: list[float] = []

    def headless_connector(settings: DoomSettings) -> DoomConnector:
        settings_seen.append(settings)
        return DoomConnector(DoomSettings())

    monkeypatch.setattr(module, "DoomConnector", headless_connector)
    monkeypatch.setattr(module, "_pause", lambda seconds: pauses.append(seconds))

    code, _, err = _run(
        module,
        ["--database", str(database), "--episode-id", episode_id, "--step-pause-seconds", "0.25"],
        capsys,
    )

    assert code == 0, err
    assert [(s.window_visible, s.realtime) for s in settings_seen] == [(True, True)]
    assert pauses == [0.25] * len(CALLS)


# --- Refusals and listing ---------------------------------------------------------


def test_an_unknown_episode_or_missing_database_is_refused(
    recorded: tuple[Path, str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database, _ = recorded
    module = _load_script()
    module.DoomConnector = _NoGame

    unknown = _run(
        module, ["--database", str(database), "--episode-id", "nope", "--headless"], capsys
    )
    missing = _run(
        module,
        ["--database", str(tmp_path / "absent.sqlite3"), "--episode-id", "x", "--headless"],
        capsys,
    )

    assert unknown[0] == 2 and "nope" in unknown[2]
    assert missing[0] == 2 and "absent.sqlite3" in missing[2]
    assert not (tmp_path / "absent.sqlite3").exists()


def test_a_non_doom_episode_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    database = tmp_path / "other-game.sqlite3"
    with EpisodeStore.open(database) as store:
        experiment = _experiment("exp_other", "fake-minecraft-0.1.0")
        store.create_experiment(experiment)
        runner = EpisodeRunner(
            connector=ScriptedConnector(ScriptedStep(terminal=True), episode_id="ep_other"),
            store=store,
            policy=ScriptedPolicy(("observe", {})),
            clock=FakeClock(wall=STARTED_AT),
        )
        asyncio.run(runner.run(experiment=experiment, scenario_id="mc_signal_post_a", seed=1))
    module = _load_script()
    module.DoomConnector = _NoGame

    code, _, err = _run(
        module, ["--database", str(database), "--episode-id", "ep_other", "--headless"], capsys
    )

    assert code == 2
    assert "doom-vizdoom" in err


def test_a_different_connector_version_is_refused(
    recorded: tuple[Path, str], capsys: pytest.CaptureFixture[str]
) -> None:
    database, episode_id = recorded
    connection = sqlite3.connect(database)
    (manifest_json,) = connection.execute(
        "SELECT manifest_json FROM episode WHERE episode_id = ?", (episode_id,)
    ).fetchone()
    manifest = json.loads(manifest_json)
    manifest["connector_version"] = "doom-vizdoom-v1"
    connection.execute(
        "UPDATE episode SET manifest_json = ? WHERE episode_id = ?",
        (json.dumps(manifest), episode_id),
    )
    connection.commit()
    connection.close()
    module = _load_script()
    module.DoomConnector = _NoGame

    code, _, err = _run(
        module, ["--database", str(database), "--episode-id", episode_id, "--headless"], capsys
    )

    assert code == 2
    assert "doom-vizdoom-v1" in err and CONNECTOR_VERSION in err


def test_without_an_episode_id_the_doom_episodes_are_listed_and_no_game_starts(
    recorded: tuple[Path, str], capsys: pytest.CaptureFixture[str]
) -> None:
    database, episode_id = recorded
    before = _sha256(database)
    module = _load_script()
    module.DoomConnector = _NoGame

    code, out, err = _run(module, ["--database", str(database)], capsys)

    assert code == 0, err
    row = next(line for line in out.splitlines() if episode_id in line)
    for value in (TRAINING, str(SEED), "training", str(len(CALLS)), "decision_limit"):
        assert value in row
    assert _sha256(database) == before


# --- Separation -------------------------------------------------------------------


def test_the_script_imports_no_model_agent_grader_or_skill_executor() -> None:
    forbidden = (
        "noob_agent.models",
        "noob_agent.agents",
        "noob_agent.grading",
        "noob_agent.skills.executor",
    )
    probe = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('replay', {str(SCRIPT)!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        f"print([m for m in sys.modules if m.startswith({forbidden!r})])\n"
    )

    completed = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )

    assert completed.stdout.strip() == "[]", completed.stdout + completed.stderr
