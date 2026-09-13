"""Display-only Doom connector settings: a visible window and real-time pacing.

This is step 19a of `hackathon_plan.md` section 19. Neither setting may change
what the game computes: with real-time pacing, every step result must match the
default settings except for the per-reset episode ID and host wall time.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noob_agent.connectors import doom
from noob_agent.connectors.doom import DoomConnector, DoomSettings
from noob_agent.domain.model import StepResult, ToolRequest

TRAINING = "doom-basic-training"
HELDOUT_A = "doom-basic-heldout-a"
SEED = 20260912
CENTERED_SEED = 101


def _request(action_id: str, tool: str, **arguments: int) -> ToolRequest:
    return ToolRequest(action_id=action_id, tool_name=tool, arguments=dict(arguments))


EVERY_TOOL = (
    _request("a01", "observe"),
    _request("a02", "jump"),
    _request("a03", "attack", ticks=0),
    _request("a04", "move_forward", ticks=6),
    _request("a05", "move_backward", ticks=3),
    _request("a06", "strafe_left", ticks=5),
    _request("a07", "strafe_right", ticks=4),
    _request("a08", "turn_left", degrees=20),
    _request("a09", "turn_right", degrees=35),
    _request("a10", "use", ticks=2),
    _request("a11", "wait", ticks=12),
    # Long attacks last: on some seeds they end the episode.
    _request("a12", "attack", ticks=35),
    _request("a13", "attack", ticks=35),
)


def _comparable(result: StepResult) -> dict[str, Any]:
    return result.model_dump(exclude={"wall_time_ms": True, "observation": {"episode_id"}})


async def _play(
    settings: DoomSettings, scenario: str, seed: int, requests: tuple[ToolRequest, ...]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    connector = DoomConnector(settings)
    try:
        reset = await connector.reset(scenario, seed)
        results = []
        for request in requests:
            result = await connector.step(request)
            results.append(_comparable(result))
            if result.observation.terminal:
                break
    finally:
        await connector.close()
    return reset.model_dump(exclude={"episode_id"}), results


@pytest.fixture
def no_pause(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replace the real-time pause so paced tests run at full speed."""
    pauses: list[float] = []
    monkeypatch.setattr(doom, "_pace", lambda started: pauses.append(started))
    return pauses


# --- Defaults ------------------------------------------------------------------


def test_display_settings_default_to_off() -> None:
    settings = DoomSettings()

    assert settings.window_visible is False
    assert settings.realtime is False
    assert settings.fullscreen is False
    assert doom.CONNECTOR_VERSION == "doom-vizdoom-v2"
    assert doom.MANIFEST.connector_version == doom.CONNECTOR_VERSION


# --- Window settings, against a stand-in ViZDoom module --------------------------


class _FakeGame:
    def __init__(self, calls: list[tuple[str, tuple[Any, ...]]]) -> None:
        self._calls = calls

    def __getattr__(self, name: str) -> Any:
        returns: dict[str, Any] = {
            "get_game_variable": 0.0,
            "is_player_dead": False,
            "is_episode_timeout_reached": False,
            "is_episode_finished": False,
            "get_state": None,
            "get_episode_time": 0,
            "get_screen_width": 320,
        }

        def call(*args: Any) -> Any:
            self._calls.append((name, args))
            return returns.get(name)

        return call


def _fake_vizdoom(tmp_path: Path, calls: list[tuple[str, tuple[Any, ...]]]) -> SimpleNamespace:
    (tmp_path / "basic.cfg").write_text("# stand-in\n")
    names = (
        "MOVE_FORWARD",
        "MOVE_BACKWARD",
        "MOVE_LEFT",
        "MOVE_RIGHT",
        "TURN_LEFT_RIGHT_DELTA",
        "ATTACK",
        "USE",
    )
    return SimpleNamespace(
        scenarios_path=str(tmp_path),
        DoomGame=lambda: _FakeGame(calls),
        Mode=SimpleNamespace(PLAYER="PLAYER"),
        Button=SimpleNamespace(**{name: SimpleNamespace(name=name) for name in names}),
        GameVariable=SimpleNamespace(
            HEALTH="HEALTH", AMMO2="AMMO2", ANGLE="ANGLE", KILLCOUNT="KILLCOUNT"
        ),
    )


async def _setup_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: DoomSettings
) -> list[tuple[str, tuple[Any, ...]]]:
    calls: list[tuple[str, tuple[Any, ...]]] = []
    monkeypatch.setitem(sys.modules, "vizdoom", _fake_vizdoom(tmp_path, calls))
    connector = DoomConnector(settings)
    await connector.reset(TRAINING, SEED)
    await connector.close()
    return calls


async def test_default_settings_keep_the_window_hidden_and_render_as_today(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = await _setup_calls(tmp_path, monkeypatch, DoomSettings())
    names = [name for name, _ in calls]

    assert ("set_window_visible", (False,)) in calls
    assert ("set_window_visible", (True,)) not in calls
    assert "set_render_all_frames" not in names
    assert names.index("set_window_visible") < names.index("init")


async def test_a_visible_window_renders_every_frame_before_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = await _setup_calls(tmp_path, monkeypatch, DoomSettings(window_visible=True))
    names = [name for name, _ in calls]

    assert ("set_window_visible", (True,)) in calls
    assert ("set_window_visible", (False,)) not in calls
    assert ("set_render_all_frames", (True,)) in calls
    assert names.index("set_window_visible") < names.index("init")
    assert names.index("set_render_all_frames") < names.index("init")


async def test_full_screen_is_requested_only_for_a_visible_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hidden = await _setup_calls(tmp_path, monkeypatch, DoomSettings(fullscreen=True))
    visible = await _setup_calls(
        tmp_path, monkeypatch, DoomSettings(window_visible=True, fullscreen=True)
    )

    assert ("add_game_args", ("+fullscreen 1",)) not in hidden
    assert ("add_game_args", ("+fullscreen 1",)) in visible


# --- Real-time pacing, against real ViZDoom (headless) ---------------------------


@pytest.mark.parametrize("scenario", [TRAINING, HELDOUT_A])
async def test_realtime_pacing_produces_identical_step_results(
    scenario: str, no_pause: list[float]
) -> None:
    reference_reset, reference = await _play(DoomSettings(), scenario, SEED, EVERY_TOOL)
    paced_reset, paced = await _play(DoomSettings(realtime=True), scenario, SEED, EVERY_TOOL)

    # Every tool and both rejections run before the long attacks.
    assert len(reference) >= len(EVERY_TOOL) - 1
    assert {r["code"] for r in reference} >= {"OK", "INVALID_TOOL", "INVALID_ARGUMENT"}
    assert paced_reset == reference_reset
    assert paced == reference
    assert no_pause, "Real-time pacing never paused."


async def test_realtime_pacing_matches_when_the_episode_ends_mid_action(
    no_pause: list[float],
) -> None:
    # A centred target and long attacks: the episode ends inside a multi-tick action.
    connector = DoomConnector()
    requests: list[ToolRequest] = []
    try:
        await connector.reset(TRAINING, CENTERED_SEED)
        for index in range(12):
            latest = await connector.step(_request(f"o{index:02d}", "observe"))
            requests.append(_request(f"o{index:02d}", "observe"))
            if latest.observation.terminal:
                break
            offsets = [
                item.properties["screen_offset"]
                for item in latest.observation.visible_objects
                if item.label == "Cacodemon"
            ]
            offset = offsets[0] if offsets else 0
            assert isinstance(offset, int)
            if abs(offset) <= 3:
                request = _request(f"f{index:02d}", "attack", ticks=35)
            else:
                tool = "turn_right" if offset > 0 else "turn_left"
                request = _request(
                    f"f{index:02d}", tool, degrees=max(1, min(90, round(abs(offset) * 0.45)))
                )
            requests.append(request)
            if (await connector.step(request)).observation.terminal:
                break
    finally:
        await connector.close()

    reference_reset, reference = await _play(
        DoomSettings(), TRAINING, CENTERED_SEED, tuple(requests)
    )
    paced_reset, paced = await _play(
        DoomSettings(realtime=True), TRAINING, CENTERED_SEED, tuple(requests)
    )

    assert reference[-1]["observation"]["terminal"] is True
    assert reference[-1]["logical_duration"] == 35
    assert paced_reset == reference_reset
    assert paced == reference
    # Pacing stops with the episode instead of waiting out the final action.
    requested_ticks = sum(r["logical_duration"] for r in reference)
    assert len(no_pause) < requested_ticks


async def test_realtime_pacing_pauses_once_per_tick_and_never_for_a_rejection(
    no_pause: list[float],
) -> None:
    connector = DoomConnector(DoomSettings(realtime=True))
    counts: dict[str, int] = {}
    try:
        await connector.reset(TRAINING, SEED)
        assert no_pause == [], "Reset must not be paced."
        for request in (
            _request("p1", "attack", ticks=35),
            _request("p2", "turn_left", degrees=10),
            _request("p3", "observe"),
            _request("p4", "jump"),
            _request("p5", "wait", ticks=0),
        ):
            before = len(no_pause)
            await connector.step(request)
            counts[request.action_id] = len(no_pause) - before
    finally:
        await connector.close()

    assert counts == {"p1": 35, "p2": 1, "p3": 1, "p4": 0, "p5": 0}


async def test_default_settings_never_pause(no_pause: list[float]) -> None:
    connector = DoomConnector()
    try:
        await connector.reset(TRAINING, SEED)
        await connector.step(_request("d1", "attack", ticks=35))
        await connector.step(_request("d2", "turn_right", degrees=10))
    finally:
        await connector.close()

    assert no_pause == []


def test_pace_sleeps_only_the_rest_of_one_native_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr(doom.time, "monotonic", lambda: 10.010)
    monkeypatch.setattr(doom.time, "sleep", lambda seconds: slept.append(seconds))

    doom._pace(10.000)
    doom._pace(9.900)

    assert slept == [pytest.approx(1 / 35 - 0.010)]
