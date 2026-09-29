"""Keep demonstration controls separate from the physical state being measured."""

import pytest

from noob_agent.redstone.memory_demo import DATA, DISPLAY, HOLD, MemoryDemo


def test_demo_controller_cannot_drive_display_or_memory_cells():
    demo = MemoryDemo.__new__(MemoryDemo)
    commands = []
    demo.command = lambda command, kind: commands.append(command)
    for target in [*DISPLAY, [20, 65, 12], [19, 65, 12]]:
        with pytest.raises(ValueError, match="only declared input levers"):
            demo.control(target, True)
    assert commands == []
    for target in [*DATA, HOLD]:
        demo.control(target, True)
    assert len(commands) == 5
    assert all("minecraft:lever" in command for command in commands)


@pytest.mark.parametrize("value", [-1, 16, True, 1.5])
def test_invalid_input_word_rejected_before_any_controller_command(value):
    demo = MemoryDemo.__new__(MemoryDemo)
    demo.control = lambda *_: pytest.fail("Invalid word reached live controls")
    with pytest.raises(ValueError, match="0..15"):
        demo.data(value)
