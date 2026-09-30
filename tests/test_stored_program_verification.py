"""Verifier tests are software fixtures, never Minecraft success evidence."""

import pytest

from noob_agent.redstone.contract import reference_execute
from noob_agent.redstone.machine import verify_programs


class ObservedMachine:
    def __init__(self, defect=None):
        self.words = (48,) * 8
        self.state = dict(accumulator=0, output=0, pc=0, halted=False, output_strobe=False)
        self.defect = defect
        self.index = 0
        self.writes = []
        self.steps = 0

    def hardware_snapshot(self):
        return {
            "full_region_sha256": "changed" if self.defect == "hardware" and self.steps else "same"
        }

    def program(self, words):
        self.words = tuple(words)
        self.writes.append(self.words)

    def read_words(self):
        return (48,) * 8 if self.defect == "storage" else self.words

    def reset(self):
        self.index = 0
        self.state = dict(accumulator=0, output=0, pc=0, halted=False, output_strobe=False)

    def sample(self):
        return dict(self.state)

    def step_and_sample(self):
        self.steps += 1
        trace = reference_execute(self.words)
        if self.index < len(trace.states):
            self.state = trace.states[self.index].model_dump()
            self.index += 1
        elif self.defect == "halt":
            self.state["pc"] += 1
        if (
            self.defect == "hardcoded"
            and self.state["output_strobe"] is False
            and self.state["pc"] >= 4
        ):
            self.state["output"] = 8
        return self.sample()

    def hold_and_sample(self, ticks):
        assert ticks == 400
        if self.defect == "hold":
            self.state["accumulator"] ^= 1
        return self.sample()


def test_requires_two_changed_programs_and_complete_observed_retirements():
    machine = ObservedMachine()
    result = verify_programs(machine)
    assert result["passed"] is True
    assert len(machine.writes) >= 4
    assert machine.writes[0] != machine.writes[1]
    assert result["programs"][0]["outputs"] == [3, 8]
    assert result["programs"][1]["outputs"] == [14, 3]
    assert all(len(p["observed_states"]) == 5 for p in result["programs"])


@pytest.mark.parametrize("defect", ["storage", "halt", "hardcoded", "hold", "hardware"])
def test_rejects_missing_storage_hardcoded_results_or_unstable_hardware(defect):
    result = verify_programs(ObservedMachine(defect))
    assert result["passed"] is False
    assert result["failures"]


def test_unknown_world_read_is_fatal_and_never_a_pass():
    machine = ObservedMachine()
    machine.step_and_sample = lambda: (_ for _ in ()).throw(TimeoutError("unknown read"))
    with pytest.raises(TimeoutError):
        verify_programs(machine)


def test_boolean_cannot_masquerade_as_an_integer_register_read():
    machine = ObservedMachine()
    original = machine.sample
    machine.sample = lambda: (
        dict(original(), accumulator=False) if original()["accumulator"] == 0 else original()
    )
    assert verify_programs(machine)["passed"] is False
