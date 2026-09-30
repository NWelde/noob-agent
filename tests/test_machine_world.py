from types import SimpleNamespace

from test_machine_interface import interface

from noob_agent.redstone import machine_world
from noob_agent.redstone.contract import MachineContract


def test_server_adapter_samples_observed_state_without_expected_arithmetic(monkeypatch):
    levels = {
        "a": [0, 15, 0, 15],
        "o": [1, 0, 0, 0],
        "pc": [0, 15, 0],
        "halted": [0],
        "strobe": [15],
        "words": [0] * 48,
    }
    calls = []

    class Grader:
        def __init__(self, actions, declaration, **kwargs):
            assert kwargs["normal_speed"] is True
            assert kwargs["max_ticks"] == 96000

        def timeline(self, recipe, **kwargs):
            calls.append((recipe, kwargs))
            return {"snapshots": [{"signals": levels}]}

        def set_control(self, key, level):
            calls.append((key, level))

        def wait_ticks(self, ticks):
            calls.append(("wait", ticks))

    monkeypatch.setattr(machine_world, "GraderControl", Grader)
    actions = SimpleNamespace(contract=MachineContract(), manifest=SimpleNamespace(data={}))
    world = machine_world.ServerMachineWorld(actions, interface())
    assert world.step_and_sample() == dict(
        accumulator=5, output=8, pc=2, halted=False, output_strobe=True
    )
    assert calls == [([], {"cycles": 1, "program_id": "machine0"})]
    calls.clear()
    world.program((3, 32, 21, 32, 48, 48, 48, 48))
    assert calls[0] == ("reset", True)
    assert len([c for c in calls if isinstance(c[0], str) and c[0].startswith("p")]) == 48
    assert not any(c[0] == "step" for c in calls)
    assert calls[-1] == ("wait", 200)
