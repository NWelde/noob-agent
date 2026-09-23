"""Frozen v1 requirements and software-only behavioral oracle; no game adapter."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Program(FrozenModel):
    name: str
    words: tuple[int, ...]
    expected_outputs: tuple[int, ...]


class TrialBudgets(FrozenModel):
    wall_seconds: int = 3600
    primitive_actions: int = 20000
    planner_calls: int = 120
    jev_calls: int = 1200
    repair_rounds: int = 24
    intention_actions: int = 256
    intention_seconds: int = 60
    program_steps: int = 8
    program_game_ticks: int = 1600
    program_wall_seconds: int = 120


class RuntimeAdapters(FrozenModel):
    """Local selection metadata, deliberately not an invented remote API."""

    status: Literal["pending_api_inspection"] = "pending_api_inspection"
    planner_provider: str | None = Field(default=None, min_length=1)
    planner_model: str | None = Field(default=None, min_length=1)
    jev_adapter: str | None = Field(default=None, min_length=1)


class MachineContract(FrozenModel):
    version: Literal["redstone-computer-v1"] = "redstone-computer-v1"
    minecraft_version: Literal["1.21.1"] = "1.21.1"
    data_bits: Literal[4] = 4
    instruction_bits: Literal[6] = 6
    storage_words: Literal[8] = 8
    encoding: str = "bits 5..4: 00 LOAD, 01 ADD, 10 OUT, 11 HALT; bits 3..0 unsigned operand"
    semantics: tuple[str, ...] = (
        "LOAD n replaces A; ADD n sets A=(A+n) modulo 16; no carry register.",
        "OUT copies A to latched O and emits one output event; OUT and HALT require operand 0.",
        "HALT latches halted; further clock edges change no state until reset.",
        "PC starts at 0, advances after non-HALT; HALT retains PC; no branches or wraparound.",
        "Eight writable six-bit words; load only while reset held; unused words must be HALT=48.",
        "Reset clears A, O, PC, halted and output-event strobe; preserves instruction storage.",
        "STEP: high for 2 ticks, low for 198; one rising edge retires one "
        "instruction in 200 ticks.",
        "STEP remains low during reset; hold reset 200 ticks, release and wait 200 before STEP.",
        "No state changes without STEP except reset and the bounded settling after an edge.",
        "Sample settled state at 200 ticks; no next edge before sampling; OUT "
        "strobe toggles per OUT.",
        "O is four labeled lamps b3,b2,b1,b0 with weights 8,4,2,1; lit=1; persists "
        "until OUT/reset.",
        "Expose labeled A, PC, halted, OUT strobe and all stored word bits for "
        "direct world probes.",
    )
    build_min: tuple[int, int, int] = (0, 64, 0)
    build_max: tuple[int, int, int] = (95, 95, 95)
    permitted_blocks: tuple[str, ...] = (
        "minecraft:stone",
        "minecraft:glass",
        "minecraft:redstone_wire",
        "minecraft:redstone_torch",
        "minecraft:redstone_wall_torch",
        "minecraft:repeater",
        "minecraft:comparator",
        "minecraft:lever",
        "minecraft:stone_button",
        "minecraft:redstone_lamp",
        "minecraft:redstone_block",
        "minecraft:oak_sign",
        "minecraft:oak_wall_sign",
    )
    construction_actions: tuple[str, ...] = ("place", "break", "interact")
    construction_rules: tuple[str, ...] = (
        "Creative single-block placement/removal and normal use only, one target per primitive.",
        "Direct placement is permitted if the inspected adapter supports it; all targets bounded.",
        "No fill/clone/structure, commands, plugins, external computation or tick "
        "acceleration in circuit.",
        "Movement/observation allowed but charged as primitives; harness reset is "
        "separate and recorded.",
        "Whitelist applies to final blocks and action results; validate properties "
        "and effects live.",
        "Freeze budgets before trial; stop at first exhausted limit, count failures and retries.",
        "Planner and Jev calls both count, including retries; no uncounted repair subtrials.",
    )
    world_rules: tuple[str, ...] = (
        "Dedicated Java 1.21.1 creative superflat, seed 1600, structures off, peaceful.",
        "Template: bedrock y=60, dirt y=61..62, grass y=63; build prism initially air.",
        "Day time 6000, daylight/weather cycles off, clear weather, "
        "randomTickSpeed=0, 20 TPS target.",
        "Before each trial restore identical template snapshot, player "
        "(48.5,64,98.5), yaw=180,pitch=0.",
        "Empty inventory then one full stack of each permitted item (wall variants "
        "use base items).",
        "Fresh conversation; no prior designs/notes; record snapshot hash and effective settings.",
    )
    budgets: TrialBudgets = TrialBudgets()
    programs: tuple[Program, ...] = (
        Program(name="ordinary_addition", words=(3, 32, 21, 32, 48), expected_outputs=(3, 8)),
        Program(name="modular_addition", words=(14, 32, 21, 32, 48), expected_outputs=(14, 3)),
    )
    public_module_checks: tuple[str, ...] = (
        "Register: reset to 0; load each 0..15; hold for 400 ticks without STEP; reset again.",
        "Arithmetic: (A,n)=(0,0),(0,15),(15,0),(15,1),(14,5),(7,8); check A=(A+n)%16 and O holds.",
        "Storage: program all eight words, read every bit; reset preserves words; "
        "select addresses 0..7.",
        "Output: OUT for each 0..15 lights matching lamps; LOAD/ADD preserve O; "
        "reset clears lamps.",
    )
    independent_final_checks: tuple[str, ...] = (
        "Trusted world reader, separate from planner/Jev claims, reads actual "
        "blocks and signal states.",
        "Audit bounds/whitelist and declared probe map; missing or ambiguous probes fail closed.",
        "Load program 1 plus HALT padding via declared programming controls; read "
        "back all 48 bits.",
        "Reset and step; sample A, PC, O, halted and OUT strobe every retirement, "
        "compare full trace.",
        "After HALT issue two more steps and require unchanged state; reset and "
        "check cleared state.",
        "Load program 2 using only same programming controls, no hardware edits; "
        "repeat full checks.",
        "Record before/after hardware block snapshots excluding declared program "
        "controls; require equality.",
        "Add independent operand variations covering 0,15 and overflow; do not "
        "reveal order before final.",
        "Require both public traces and all independent checks within budgets; "
        "missing evidence is failure.",
        "Software oracle, model claims, HUD and video cannot establish Minecraft success.",
    )


def encode_instruction(opcode: str, operand: int = 0) -> int:
    """Encode one canonical word; reject coercions and reserved operands."""
    opcodes = ("LOAD", "ADD", "OUT", "HALT")
    if opcode not in opcodes or type(operand) is not int or not 0 <= operand <= 15:
        raise ValueError("Invalid opcode or four-bit operand")
    if opcode in ("OUT", "HALT") and operand != 0:
        raise ValueError("OUT/HALT operand must be zero")
    return opcodes.index(opcode) * 16 + operand


def decode_instruction(word: int) -> tuple[str, int]:
    if type(word) is not int or not 0 <= word <= 63:
        raise ValueError("Instruction must be an unsigned six-bit integer")
    opcode = ("LOAD", "ADD", "OUT", "HALT")[word >> 4]
    operand = word & 15
    encode_instruction(opcode, operand)
    return opcode, operand


class ReferenceState(FrozenModel):
    accumulator: int = 0
    output: int = 0
    pc: int = 0
    halted: bool = False
    output_strobe: bool = False


class ReferenceTrace(ReferenceState):
    evidence_kind: Literal["software_reference_only"] = "software_reference_only"
    steps: int
    outputs: tuple[int, ...]
    states: tuple[ReferenceState, ...]


def reference_execute(words: tuple[int, ...]) -> ReferenceTrace:
    """Derive expected retirements from reset. Never grades or interacts with Minecraft."""
    if not 1 <= len(words) <= 8:
        raise ValueError("Program must contain 1..8 words")
    decoded = tuple(decode_instruction(word) for word in words)
    halt = next((i for i, (op, _) in enumerate(decoded) if op == "HALT"), None)
    if halt is None or any(op != "HALT" for op, _ in decoded[halt:]):
        raise ValueError("Program requires HALT and only HALT padding")
    a = output = 0
    strobe = False
    outputs: list[int] = []
    states: list[ReferenceState] = []
    for pc, (op, operand) in enumerate(decoded[: halt + 1]):
        if op == "LOAD":
            a = operand
        elif op == "ADD":
            a = (a + operand) % 16
        elif op == "OUT":
            output = a
            outputs.append(output)
            strobe = not strobe
        states.append(
            ReferenceState(
                accumulator=a,
                output=output,
                pc=pc if op == "HALT" else pc + 1,
                halted=op == "HALT",
                output_strobe=strobe,
            )
        )
    return ReferenceTrace(
        **states[-1].model_dump(), steps=len(states), outputs=tuple(outputs), states=tuple(states)
    )


def load_contract(path: str | Path) -> MachineContract:
    """Load the exact v1 artifact, rejecting missing fields and semantic drift."""
    raw = Path(path).read_text(encoding="utf-8")
    contract = MachineContract.model_validate_json(raw)
    # Defaults aid programmatic discovery, but artifacts must explicitly freeze every field.
    # Python numeric equality treats 4.0 == 4 (and False == 0). Compare normalized
    # JSON instead so literal validation cannot silently erase numeric type drift.
    actual = json.dumps(json.loads(raw), sort_keys=True)
    expected = json.dumps(MachineContract().model_dump(mode="json"), sort_keys=True)
    if actual != expected:
        raise ValueError("Artifact differs from frozen redstone-computer-v1 contract")
    for program in contract.programs:
        if reference_execute(program.words).outputs != program.expected_outputs:
            raise ValueError("Expected output does not match instruction semantics")
    return contract


def validate_build_action(
    contract: MachineContract,
    action: str,
    position: tuple[int, int, int],
    block: str | None = None,
) -> None:
    """Pure boundary check, not authorization or proof that an adapter acted safely."""
    if action not in contract.construction_actions:
        raise ValueError("Unsupported construction action")
    if len(position) != 3 or any(
        type(value) is not int or not low <= value <= high
        for value, low, high in zip(position, contract.build_min, contract.build_max, strict=True)
    ):
        raise ValueError("Target outside inclusive build bounds")
    if (action == "place" and block is None) or (
        block is not None and block not in contract.permitted_blocks
    ):
        raise ValueError("Block is not permitted")
