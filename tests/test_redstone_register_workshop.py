from __future__ import annotations

import pytest
from test_redstone_modules import declaration

from noob_agent.redstone.contract import MachineContract
from noob_agent.redstone.modules import ParameterBit
from noob_agent.redstone.register_workshop import (
    grade_register_bit,
    register_workshop_setup_metadata,
    scaffold_commands,
)


class FakeActions:
    contract = MachineContract()


def declared():
    value = declaration("register")
    value["controls"][-1].update(id="input0", role="test_input")
    value["controls"].extend(
        {"id": f"input{index}", "role": "test_input", "position": [index + 3, 64, 1]}
        for index in range(1, 4)
    )
    value["recipe_templates"] = {
        "load": [
            {
                "control": f"input{index}",
                "level": ParameterBit(parameter="value", bit=index).model_dump(),
            }
            for index in range(4)
        ]
    }
    return value


def install_fake_timeline(monkeypatch, signals):
    seen = {}

    class FakeGrader:
        def __init__(self, actions, declaration):
            seen["declaration"] = declaration

        def probe(self, role, index):
            seen["probe"] = (role, index)
            return {"properties": {"power": 0}}

        def timeline(self, recipe, **kwargs):
            seen["recipe"] = recipe
            seen["kwargs"] = kwargs
            return {
                "snapshots": [
                    {"phase": "settled", "signals": {"a": [0, 0, signals[0], 0]}},
                    {"phase": "settled", "signals": {"a": [0, 0, signals[1], 0]}},
                    {"phase": "settled", "signals": {"a": [0, 0, signals[2], 0]}},
                    {"phase": "settled", "signals": {"a": [0, 0, signals[3], 0]}},
                ],
                "control_effects": [
                    {"control": "step", "verified": True, "level": True},
                    {"control": "step", "verified": True, "level": False},
                    {"control": "step", "verified": True, "level": True},
                    {"control": "step", "verified": True, "level": False},
                    {"control": "reset", "verified": True, "level": True},
                    {"control": "reset", "verified": True, "level": False},
                    {"control": "reset", "verified": True, "level": True},
                    {"control": "reset", "verified": True, "level": False},
                ],
            }

    monkeypatch.setattr("noob_agent.redstone.register_workshop.GraderControl", FakeGrader)
    return seen


def test_one_bit_diagnostic_uses_observed_msb_probe_and_never_grants_full_pass(monkeypatch):
    seen = install_fake_timeline(monkeypatch, [0, 1, 1, 0])
    result = grade_register_bit(FakeActions(), declared(), bit=1)
    assert result["passed"] is True
    assert result["probe_index_msb_first"] == 2
    assert result["checkpoint_eligible"] is False
    assert result["fullcomputer_eligible"] is False
    assert result["evidence"]["hold_after_input_change"]["step_low"] is True
    assert seen["kwargs"]["sample_cycles"] == [2, 3]
    assert seen["probe"] == ("a", 2)
    assert {"control": "input1", "level": False} in seen["kwargs"]["cycle_recipes"][2]


def test_always_powered_register_probe_fails_and_wrong_module_rejected(monkeypatch):
    install_fake_timeline(monkeypatch, [15, 15, 15, 15])
    result = grade_register_bit(FakeActions(), declared(), bit=0)
    assert result["passed"] is False
    value = declared()
    value["module"] = "arithmetic"
    with pytest.raises(ValueError, match="requires a register"):
        grade_register_bit(FakeActions(), value, bit=0)


def test_setup_profile_is_marked_and_scaffold_is_deterministic():
    metadata = register_workshop_setup_metadata()
    assert metadata["baseline_comparable"] is False
    assert metadata["provides_circuit"] is False
    assert scaffold_commands((10, 64, 20)) == [
        "fill 10 64 20 18 64 28 minecraft:stone",
        "fill 10 65 20 18 68 28 minecraft:air",
        "setblock 10 65 20 minecraft:glass",
    ]
    with pytest.raises(ValueError):
        scaffold_commands([10, 63, 20])
