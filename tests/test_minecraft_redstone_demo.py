"""Retained redstone fixture and public connector checks."""

from pathlib import Path


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
