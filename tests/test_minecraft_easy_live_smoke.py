"""Retained easy-button data-pack fixture checks; learning runner retired."""

import json
import re
from pathlib import Path

PACK = Path("scenarios/minecraft/easy-button-gate-v1")
FUNCTIONS = PACK / "data/noob_agent_easy/function"


def _function(name: str) -> str:
    return (FUNCTIONS / f"{name}.mcfunction").read_text(encoding="utf-8")


# --- Datapack ----------------------------------------------------------------


def test_easy_pack_is_isolated_from_the_resonator_pack() -> None:
    metadata = json.loads((PACK / "pack.mcmeta").read_text(encoding="utf-8"))
    tick_tag = json.loads(
        (PACK / "data/minecraft/tags/function/tick.json").read_text(encoding="utf-8")
    )

    assert metadata["pack"]["pack_format"] == 48
    assert "non-benchmark" in metadata["pack"]["description"]
    assert tick_tag == {"values": ["noob_agent_easy:tick"]}
    # A load tag would rerun reset on every `/reload` and teleport players.
    assert not (PACK / "data/minecraft/tags/function/load.json").exists()
    assert not (PACK / "data/noob_agent").exists()


def test_easy_reset_restores_the_room_and_grants_durable_night_vision() -> None:
    reset = _function("reset")
    lines = [line for line in reset.splitlines() if line and not line.startswith("#")]

    assert "forceload add 24 -4 34 4" in lines
    assert "clear @a" in lines
    assert "tp @a 27.5 100 0.5 -90 0" in lines
    assert "gamemode adventure @a" in lines
    clear_index = lines.index("effect clear @a")
    night_vision = "effect give @a minecraft:night_vision infinite 0 true"
    assert night_vision in lines
    assert lines.index(night_vision) > clear_index
    # The sealed gateway and its control are rebuilt on every reset.
    assert "fill 31 100 -1 31 102 1 minecraft:iron_bars" in lines
    assert "setblock 30 101 -2 minecraft:stone_button[face=wall,facing=west,powered=false]" in lines


def test_easy_room_has_no_hidden_recipe_container_timer_or_private_state() -> None:
    text = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(FUNCTIONS.rglob("*.mcfunction"))
    )

    for forbidden in ("barrel", "hopper", "chest", "scoreboard", "schedule", "item replace"):
        assert forbidden not in text
    assert 'CustomName:\'{"text":"Gate Button"' in text
    assert 'CustomName:\'{"text":"Iron-Bar Gateway"' in text


def test_pressing_the_button_opens_the_bars_with_public_feedback() -> None:
    tick = _function("tick")
    condition = (
        "execute if block 30 101 -2 minecraft:stone_button[powered=true] "
        "if block 31 101 0 minecraft:iron_bars run "
    )

    assert f"{condition}fill 31 100 -1 31 102 1 minecraft:air" in tick
    assert condition + "title @a[x=24,y=99,z=-4,dx=10,dy=5,dz=8] actionbar" in tick
    assert "The iron-bar gateway opens." in tick
    # The sidecar records action-bar feedback, so a duplicate chat copy would
    # show the model the same message twice.
    assert "tellraw" not in tick
    assert tick.count("The iron-bar gateway opens.") == 1
    # The message is issued before the bars disappear, so it fires exactly once.
    assert tick.index("actionbar") < tick.index("minecraft:air")


def test_button_and_whole_gateway_are_reachable_and_observable_from_the_start() -> None:
    start = (27.5, 100.0, 0.5)
    floored = (27, 100, 0)
    button = (30, 101, -2)
    bars = [(31, y, z) for y in (100, 101, 102) for z in (-1, 0, 1)]

    # Sidecar reach is 5 blocks from the feet; default observe radius is 5.
    assert sum((b - s) ** 2 for b, s in zip(button, start, strict=True)) ** 0.5 <= 5
    for block in (button, *bars):
        assert sum((b - f) ** 2 for b, f in zip(block, floored, strict=True)) <= 25


def test_easy_room_is_outside_the_resonator_room_and_its_observation_radius() -> None:
    reset = _function("reset")
    xs = [int(value) for value in re.findall(r"(?:fill|setblock) (-?\d+) ", reset)]
    resonator_max_player_x = 6
    max_observe_radius = 8

    assert min(xs) > resonator_max_player_x + max_observe_radius
    # Nothing in the easy pack calls into, or clears timers of, the Resonator pack.
    assert "noob_agent:" not in reset
    assert "noob_agent:" not in _function("tick")
