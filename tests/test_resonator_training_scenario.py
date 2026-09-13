"""Acceptance checks for the vanilla Resonator training-world data pack."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

PACK = Path("scenarios/minecraft/resonator-training-v1")
FUNCTIONS = PACK / "data/noob_agent/function"


def function(name: str) -> str:
    """Read one namespaced function from the source-controlled data pack."""
    return (FUNCTIONS / f"{name}.mcfunction").read_text(encoding="utf-8")


def test_pack_targets_vanilla_1_21_1_and_registers_lifecycle_functions() -> None:
    metadata = json.loads((PACK / "pack.mcmeta").read_text(encoding="utf-8"))
    load_tag = json.loads(
        (PACK / "data/minecraft/tags/function/load.json").read_text(encoding="utf-8")
    )
    tick_tag = json.loads(
        (PACK / "data/minecraft/tags/function/tick.json").read_text(encoding="utf-8")
    )

    assert metadata["pack"]["pack_format"] == 48
    assert load_tag == {"values": ["noob_agent:load"]}
    assert tick_tag == {"values": ["noob_agent:tick"]}


def test_reset_restores_every_public_and_scenario_owned_state() -> None:
    reset = function("reset")

    required_commands = (
        "schedule clear noob_agent:device/complete",
        "schedule clear noob_agent:oracle/",
        "kill @e[tag=noob_agent_scenario]",
        "clear @a",
        "tp @a",
        "title @a reset",
        "scoreboard players reset * noob_agent.state",
        "forceload add -1 -1 0 0",
        "function noob_agent:build_room",
    )
    for command in required_commands:
        assert command in reset


def test_clean_rule_requires_two_inputs_waits_40_ticks_and_creates_one_key() -> None:
    activate = function("device/activate")
    complete = function("device/complete")

    assert "if score #inputs noob_agent.state matches 2.." in activate
    assert "schedule function noob_agent:device/complete 40t replace" in activate
    assert "scoreboard players remove #inputs noob_agent.state 2" in complete
    assert complete.count("minecraft:echo_shard") == 1
    assert "scoreboard players add #inputs_consumed noob_agent.state 2" in complete
    assert "scoreboard players add #outputs_created noob_agent.state 1" in complete


def test_gateway_consumes_one_key_and_opens_only_for_a_key_holder() -> None:
    gateway = function("gateway/use")

    assert "if items entity @s weapon.mainhand minecraft:echo_shard" in gateway
    assert "item replace entity @s weapon.mainhand with minecraft:air" in gateway
    assert "fill 5 101 -1 5 103 1 minecraft:air" in gateway
    assert "scoreboard players set #gateway_open noob_agent.state 1" in gateway


def test_negative_inputs_preserve_items_and_emit_exact_feedback() -> None:
    reject = function("device/reject_input")
    activate = function("device/activate")

    assert "kill @s" not in reject
    assert "The device does not respond." in reject
    assert "The device hums, but nothing changes." in activate


def test_oracle_runs_the_clean_sequence_twice_and_checks_the_same_result() -> None:
    start = function("oracle/start")
    run_clean = function("oracle/run_clean")
    finish = function("oracle/finish_run")
    compare = function("oracle/compare_runs")

    assert "scoreboard players set #oracle_run noob_agent.test 1" in start
    assert "function noob_agent:oracle/run_clean" in start
    assert "count:2" in run_clean
    assert "function noob_agent:device/accept_input" in run_clean
    assert "noob_agent_oracle_actor" in run_clean
    assert "matches 1 run function noob_agent:oracle/reset_for_second_run" in finish
    assert "matches 2 run function noob_agent:oracle/compare_runs" in finish
    assert finish.index("matches 2 run function") < finish.index("matches 1 run function")
    assert "scoreboard players set #oracle_status noob_agent.test 1" in compare


@pytest.mark.parametrize("forbidden", ["recipe", "grading", "grader", "clean", "faulty"])
def test_public_text_does_not_leak_the_rule_or_evaluation_identity(forbidden: str) -> None:
    public_commands = "\n".join(
        line
        for path in FUNCTIONS.rglob("*.mcfunction")
        for line in path.read_text(encoding="utf-8").splitlines()
        if "title " in line or "tellraw " in line or "CustomName" in line or "text:'" in line
    ).lower()

    assert forbidden not in public_commands
