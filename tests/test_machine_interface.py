import copy

import pytest

from noob_agent.redstone.contract import MachineContract
from noob_agent.redstone.modules import MachineDeclaration, validate_declaration
from noob_agent.redstone.planner import PlannerContext, validate_intention


def interface():
    widths = {"a": 4, "o": 4, "pc": 3, "halted": 1, "strobe": 1, "words": 48}
    probes = {}
    offset = 0
    for role, width in widths.items():
        probes[role] = [
            {
                "position": [i, 64, 0],
                "block": "minecraft:redstone_lamp" if role == "o" else "minecraft:redstone_wire",
            }
            for i in range(offset, offset + width)
        ]
        offset += width
    controls = [
        {"id": "reset", "role": "reset", "position": [0, 64, 1]},
        {"id": "step", "role": "step", "position": [1, 64, 1]},
    ]
    controls += [{"id": f"p{i}", "role": "programming", "position": [i, 64, 2]} for i in range(48)]
    return dict(
        module="machine",
        probes=probes,
        controls=controls,
        word_controls=[[f"p{i}" for i in range(a * 6, a * 6 + 6)] for a in range(8)],
    )


def test_full_interface_validates_without_external_execution_controls():
    assert isinstance(validate_declaration(interface(), MachineContract()), MachineDeclaration)


def test_machine_interface_requires_a_separate_nonmutating_grading_turn():
    value = dict(summary="Grade built machine", actions=[], machine_inspection=interface())
    assert validate_intention(value, MachineContract()).machine_inspection is not None
    value["actions"] = [dict(id="change", criteria="change", action="break", position=[1, 64, 1])]
    with pytest.raises(ValueError, match="no actions"):
        validate_intention(value, MachineContract())


def test_machine_schema_is_exposed_only_for_the_integration_phase():
    context = PlannerContext(MachineContract(), require_module_grading=True)
    assert "machine_inspection" not in context.request({}).response_schema["properties"]
    context.machine_phase = True
    request = context.request({})
    assert "machine_inspection" in request.response_schema["properties"]
    assert "module_inspection" not in request.response_schema["properties"]
    assert context.saved_state()["machine_phase"] is True


@pytest.mark.parametrize("defect", ["alias", "short", "input", "recipe"])
def test_machine_rejects_aliased_memory_and_external_computation(defect):
    value = copy.deepcopy(interface())
    if defect == "alias":
        value["word_controls"][0][0] = "p1"
    elif defect == "short":
        value["word_controls"][0].pop()
    elif defect == "input":
        value["controls"][2]["role"] = "test_input"
    else:
        value["recipes"] = {"execute": []}
    with pytest.raises(ValueError):
        validate_declaration(value, MachineContract())
