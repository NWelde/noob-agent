"""Behavioral requirements, independent of any Minecraft circuit layout."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from noob_agent.redstone import (
    MachineContract,
    RuntimeAdapters,
    decode_instruction,
    encode_instruction,
    load_contract,
    reference_execute,
    validate_build_action,
)

CONTRACT = Path("scenarios/minecraft/redstone-computer-v1/contract.json")


def test_public_programs_have_distinct_observable_traces():
    contract = load_contract(CONTRACT)
    traces = [reference_execute(p.words) for p in contract.programs]
    assert [t.outputs for t in traces] == [(3, 8), (14, 3)]
    assert [t.accumulator for t in traces] == [8, 3]
    assert all(t.halted and t.steps == 5 for t in traces)
    assert all(t.evidence_kind == "software_reference_only" for t in traces)


def test_arithmetic_wraps_and_output_is_latched():
    trace = reference_execute((15, 17, 32, 23, 48))
    assert trace.outputs == (0,)
    assert trace.accumulator == 7
    assert trace.output == 0
    assert reference_execute((48,)).output == 0


@pytest.mark.parametrize(
    "words", [(True, 48), (64, 48), (33, 48), (49,), (), (0,) * 9, (0,) * 8, (48, 0)]
)
def test_invalid_or_unterminated_programs_fail(words):
    with pytest.raises(ValueError):
        reference_execute(words)


def test_encoding_round_trip_and_reserved_operands():
    for opcode in ("LOAD", "ADD"):
        for value in range(16):
            assert decode_instruction(encode_instruction(opcode, value)) == (opcode, value)
    for opcode in ("OUT", "HALT"):
        assert decode_instruction(encode_instruction(opcode)) == (opcode, 0)
        with pytest.raises(ValueError):
            encode_instruction(opcode, 1)


def test_contract_is_deeply_frozen():
    contract = load_contract(CONTRACT)
    with pytest.raises(ValidationError):
        contract.data_bits = 8
    with pytest.raises(ValidationError):
        contract.programs[0].name = "changed"
    assert isinstance(contract.programs[0].words, tuple)
    assert contract == MachineContract()


@pytest.mark.parametrize(
    "change", ["version", "unknown", "expected", "budget", "coercion", "float"]
)
def test_loader_rejects_contract_drift(tmp_path, change):
    data = json.loads(CONTRACT.read_text())
    if change == "version":
        data["version"] = "2"
    elif change == "unknown":
        data["layout"] = []
    elif change == "expected":
        data["programs"][0]["expected_outputs"] = [9]
    elif change == "budget":
        data["budgets"]["primitive_actions"] += 1
    elif change == "float":
        data["data_bits"] = 4.0
    else:
        data["data_bits"] = "4"
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_contract(path)


def test_actions_enforce_inclusive_bounds_and_ordinary_blocks():
    contract = load_contract(CONTRACT)
    validate_build_action(contract, "place", (0, 64, 0), "minecraft:redstone_wire")
    validate_build_action(contract, "break", (95, 95, 95))
    for action, pos, block in [
        ("place", (-1, 64, 0), "minecraft:stone"),
        ("place", (0, 96, 0), "minecraft:stone"),
        ("place", (0, 64, 96), "minecraft:stone"),
        ("place", (0, 64, 0), "minecraft:command_block"),
        ("fill", (0, 64, 0), "minecraft:stone"),
        ("place", (True, 64, 0), "minecraft:stone"),
        ("place", (0, 64, 0), None),
    ]:
        with pytest.raises(ValueError):
            validate_build_action(contract, action, pos, block)


def test_runtime_choices_remain_configurable_and_pending():
    adapters = RuntimeAdapters(
        planner_provider="chosen-later",
        planner_model="chosen-later",
        jev_adapter="local-config-name",
    )
    assert adapters.status == "pending_api_inspection"
    assert RuntimeAdapters().planner_model is None


def test_every_addition_pair_and_retirement_state():
    for a in range(16):
        for n in range(16):
            trace = reference_execute((a, 16 + n, 32, 48))
            assert trace.outputs == ((a + n) % 16,)
            assert [s.pc for s in trace.states] == [1, 2, 3, 3]
            assert [s.halted for s in trace.states] == [False, False, False, True]
            assert [s.output_strobe for s in trace.states] == [False, False, True, True]
            assert trace.states[1].output == 0


def test_full_storage_capacity_and_halt_padding():
    trace = reference_execute((1, 17, 17, 17, 17, 17, 32, 48))
    assert trace.steps == 8
    assert trace.outputs == (6,)
    assert reference_execute((48,) * 8).steps == 1


def test_loader_requires_complete_artifact(tmp_path):
    path = tmp_path / "partial.json"
    path.write_text("{}")
    with pytest.raises(ValueError, match="frozen"):
        load_contract(path)


@pytest.mark.parametrize(
    "field_path",
    [
        ("data_bits",),
        ("instruction_bits",),
        ("storage_words",),
        ("budgets", "program_steps"),
        ("build_min", 0),
        ("programs", 0, "words", 0),
        ("programs", 0, "expected_outputs", 0),
    ],
)
@pytest.mark.parametrize("numeric_type", [float, bool])
def test_loader_rejects_numeric_type_drift(tmp_path, field_path, numeric_type):
    data = json.loads(CONTRACT.read_text())
    target = data
    for key in field_path[:-1]:
        target = target[key]
    key = field_path[-1]
    target[key] = numeric_type(target[key])
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_contract(path)


def test_loader_accepts_reformatted_artifact(tmp_path):
    data = json.loads(CONTRACT.read_text())
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")))
    assert load_contract(path) == MachineContract()
