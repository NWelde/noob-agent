"""Compare actual world observations with stored-program semantics.

The reference computes expectations only. The world adapter must program real
controls, read all 48 stored bits, pulse STEP at normal speed, and independently
read the declared signals. Software fixtures do not establish Minecraft success.
"""

import json
from typing import Any, Protocol

from noob_agent.redstone.contract import MachineContract, reference_execute

RESET = dict(accumulator=0, output=0, pc=0, halted=False, output_strobe=False)


class MachineWorld(Protocol):
    def hardware_snapshot(self) -> dict[str, Any]: ...
    def program(self, words: tuple[int, ...]) -> None: ...
    def read_words(self) -> tuple[int, ...]: ...
    def reset(self) -> None: ...
    def sample(self) -> dict[str, Any]: ...
    def step_and_sample(self) -> dict[str, Any]: ...
    def hold_and_sample(self, ticks: int) -> dict[str, Any]: ...


def verify_programs(world: MachineWorld) -> dict[str, Any]:
    """Two public programs plus zero, maximum, and overflow operand variations.

    Unknown observations propagate to the caller; they cannot become a pass.
    Inputs may change only through the same programming controls. Each hardware
    snapshot must cover the full build region with dynamic signal fields removed.
    """
    contract = MachineContract()
    programs = [tuple(p.words) for p in contract.programs]
    programs += [(a, 32, 16 + n, 32, 48) for a, n in ((0, 0), (0, 15), (15, 0), (15, 1), (7, 8))]
    original = world.hardware_snapshot()
    if not original.get("full_region_sha256"):
        raise ValueError("Full-region hardware evidence required")
    result: dict[str, Any] = {
        "scope": "observed_stored_program_behavior",
        "passed": False,
        "programs": [],
        "failures": [],
    }

    def check(case: str, target: str, actual: Any, expected: Any) -> None:
        if json.dumps(actual, sort_keys=True, allow_nan=False) != json.dumps(
            expected, sort_keys=True, allow_nan=False
        ):
            result["failures"].append(
                dict(case=case, target=target, actual=actual, expected=expected)
            )

    for index, source in enumerate(programs):
        words = source + (48,) * (8 - len(source))
        case = f"program_{index + 1}"
        world.program(words)
        check(case, "stored_words", world.read_words(), words)
        world.reset()
        check(case, "reset_preserves_words", world.read_words(), words)
        check(case, "reset_state", world.sample(), RESET)
        expected = reference_execute(words)
        observed_states = []
        outputs = []
        previous_strobe = False
        for retirement, state in enumerate(expected.states):
            actual = world.step_and_sample()
            observed_states.append(actual)
            check(case, f"retirement_{retirement + 1}", actual, state.model_dump())
            if actual.get("output_strobe") != previous_strobe:
                outputs.append(actual.get("output"))
            previous_strobe = bool(actual.get("output_strobe"))
        halted = expected.states[-1].model_dump()
        for extra in range(2):
            check(case, f"halt_extra_step_{extra + 1}", world.step_and_sample(), halted)
        check(case, "hold_without_step", world.hold_and_sample(400), halted)
        check(case, "execution_preserves_words", world.read_words(), words)
        check(case, "outputs", outputs, list(expected.outputs))
        check(case, "hardware_unchanged", world.hardware_snapshot(), original)
        result["programs"].append(
            dict(words=list(words), observed_states=observed_states, outputs=outputs)
        )
        world.reset()
        check(case, "final_reset_state", world.sample(), RESET)
        check(case, "final_reset_preserves_words", world.read_words(), words)
    result["passed"] = not result["failures"]
    return result
