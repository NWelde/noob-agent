from types import SimpleNamespace

import pytest

from noob_agent.redstone.module_gate import admit, affected_modules, current_module


def intention(module, position):
    return SimpleNamespace(
        circuit_plan=SimpleNamespace(module=module),
        module_inspection=None,
        actions=[SimpleNamespace(action="place", position=position)],
    )


def test_later_module_build_is_rejected_before_dispatch():
    with pytest.raises(ValueError, match="circuit_plan module register"):
        admit(intention("arithmetic", [45, 64, 10]), {})
    with pytest.raises(ValueError, match="restricted"):
        admit(intention("register", [45, 64, 10]), {})


def test_behavioral_acceptance_opens_only_next_region():
    passed = {"register": {"grader_event": 1}}
    assert current_module(passed) == "arithmetic"
    admit(intention("arithmetic", [45, 64, 10]), passed)
    with pytest.raises(ValueError):
        admit(intention("storage", [10, 64, 40]), passed)


def test_accepted_hardware_or_adjacent_mutation_invalidates_it():
    assert affected_modules([10, 64, 10], ["register"]) == {"register"}
    assert affected_modules([36, 64, 10], ["register"]) == {"register"}
    assert affected_modules([45, 64, 10], ["register"]) == set()


def test_wrong_grade_cannot_advance_gate():
    value = intention("register", [10, 64, 10])
    value.actions = []
    value.module_inspection = SimpleNamespace(module="output")
    with pytest.raises(ValueError, match="grade register"):
        admit(value, {})
