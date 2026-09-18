"""The redstone demo compares fresh attempts under identical limits."""

import asyncio
import importlib.util
from pathlib import Path

import pytest


def load_demo():
    spec = importlib.util.spec_from_file_location(
        "redstone_demo", "scripts/run_minecraft_redstone_demo.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_comparison_configuration_is_isolated_and_equal():
    demo = load_demo()
    harness = demo.harness()
    assert harness.SCENARIO_ID == "redstone-lamp-v1"
    assert harness.EPISODE_DECISION_BUDGET == 12
    assert harness.EPISODE_PRIMITIVE_BUDGET == 24
    assert harness.UNBOUNDED_WALL_TIME_MS == 120_000
    assert harness.SUCCESS_MESSAGE == "The redstone lamp lights up."
    assert "place" not in harness.EASY_PUBLIC_GOAL.lower()
    assert harness.RESET_COMMANDS == ("function noob_agent_redstone:reset",)


def test_pack_resets_power_and_success_comes_from_actual_lamp():
    root = Path("scenarios/minecraft/redstone-lamp-v1/data/noob_agent_redstone/function")
    reset = (root / "reset.mcfunction").read_text()
    tick = (root / "tick.mcfunction").read_text()
    assert "minecraft:redstone_block" in reset
    assert "minecraft:barrel" in reset
    assert "minecraft:redstone_lamp[lit=false]" in reset
    assert "minecraft:redstone_lamp[lit=true]" in tick
    assert "The redstone lamp lights up." in tick
    assert "gamemode survival @a[name=noobagentbot]" in reset
    assert "tag @e[tag=noob_redstone] remove solved" in reset


def test_connector_exposes_the_lamp_and_its_public_lit_state():
    source = Path("src/noob_agent/connectors/minecraft_sidecar/index.js").read_text()
    allowlist = source.split("const PUBLIC_BLOCKS = new Set([", 1)[1].split("]);", 1)[0]
    assert '"redstone_lamp"' in allowlist
    assert '"redstone_block"' in allowlist
    assert "lit: block.getProperties().lit" in source


def test_the_demo_refuses_a_missing_wandb_key_before_touching_weave_or_the_network(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A fresh clone with .env.demo.example copied but no key filled in must get the
    same clear, actionable refusal as scripts/run_doom_learning_sequence.py, not a
    Weave login prompt followed by a traceback."""
    demo = load_demo()
    environ = {
        "NOOB_AGENT_TRACE_MODE": "weave",
        "WEAVE_DISABLED": "false",
        "NOOB_AGENT_MODEL_PROVIDER": "wandb-inference",
        "NOOB_AGENT_INFERENCE_MODEL": "some-model",
    }

    code = asyncio.run(demo.run(environ=environ))

    assert code == 2
    assert "WANDB_API_KEY is not set" in capsys.readouterr().err
