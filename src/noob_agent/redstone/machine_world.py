"""Real server observations and declared lever inputs for machine verification."""

from typing import Any

from noob_agent.redstone.actions import Actions
from noob_agent.redstone.grading import GraderControl
from noob_agent.redstone.modules import MachineDeclaration, validate_declaration


class ServerMachineWorld:
    def __init__(self, actions: Actions, interface: object):
        declaration = validate_declaration(interface, actions.contract)
        if not isinstance(declaration, MachineDeclaration):
            raise ValueError("Full machine interface required")
        self.actions = actions
        self.declaration = declaration
        prior = actions.manifest.data.get("grading_budget", {})
        self.grader = GraderControl(
            actions,
            declaration,
            max_ticks=prior.get("tick_limit", 96000),
            max_commands=prior.get("command_limit", 1000000),
            normal_speed=True,
        )
        self.reset_id = next(c.id for c in declaration.controls if c.role == "reset")
        self.program_number = 0

    def hardware_snapshot(self) -> dict[str, Any]:
        result = self.actions.read({"op": "hardware"})
        if result.get("blocks") != 294912 or result.get("illegal") != []:
            raise ValueError("Incomplete or forbidden machine hardware")
        return result

    def program(self, words: tuple[int, ...]) -> None:
        if len(words) != 8 or any(type(word) is not int or not 0 <= word < 64 for word in words):
            raise ValueError("Eight six-bit words required")
        self.program_number += 1
        self.grader.set_control(self.reset_id, True)
        for word, row in zip(words, self.declaration.word_controls, strict=True):
            for bit, key in zip(range(5, -1, -1), row, strict=True):
                self.grader.set_control(key, bool(word & (1 << bit)))
        self.grader.wait_ticks(200)

    def reset(self) -> None:
        self.grader.set_control(self.reset_id, True)
        self.grader.wait_ticks(200)
        self.grader.set_control(self.reset_id, False)
        self.grader.wait_ticks(200)

    @staticmethod
    def _integer(values: list[int]) -> int:
        result = 0
        for value in values:
            if type(value) is not int or not 0 <= value <= 15:
                raise ValueError("Invalid observed wire/lamp level")
            result = (result << 1) | int(value > 0)
        return result

    def read_words(self) -> tuple[int, ...]:
        levels = []
        for index in range(48):
            block = self.grader.probe("words", index)
            value = block["properties"].get("power", block["properties"].get("lit"))
            levels.append(int(value) if type(value) is bool else value)
        return tuple(self._integer(levels[i : i + 6]) for i in range(0, 48, 6))

    def _state(self, result: dict[str, Any]) -> dict[str, Any]:
        signals = result["snapshots"][-1]["signals"]
        return {
            "accumulator": self._integer(signals["a"]),
            "output": self._integer(signals["o"]),
            "pc": self._integer(signals["pc"]),
            "halted": bool(self._integer(signals["halted"])),
            "output_strobe": bool(self._integer(signals["strobe"])),
        }

    def sample(self) -> dict[str, Any]:
        return self._state(self.grader.timeline([], sample_only=True))

    def step_and_sample(self) -> dict[str, Any]:
        return self._state(
            self.grader.timeline([], cycles=1, program_id=f"machine{self.program_number}")
        )

    def hold_and_sample(self, ticks: int) -> dict[str, Any]:
        if type(ticks) is not int or not 1 <= ticks <= 400:
            raise ValueError("Hold must be 1..400 ticks")
        recipe = [{"wait": min(200, ticks)}]
        if ticks > 200:
            recipe.append({"wait": ticks - 200})
        return self._state(self.grader.timeline(recipe, sample_only=True))
