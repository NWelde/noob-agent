"""Small seeded repair demo; its pass is not full computer success."""

from __future__ import annotations

from typing import Any, Protocol

from noob_agent.redstone.actions import Actions

LEVER_POSITION = [48, 64, 95]
WIRE_POSITION = [49, 64, 95]
LAMP_POSITION = [50, 64, 95]


class BlockReader(Protocol):
    def observe(self, position: list[int]) -> dict[str, Any]: ...


class CommandWriter(Protocol):
    def command(self, command: str) -> str: ...


class EventWriter(Protocol):
    def attempt(self, kind: str, request: dict[str, Any]) -> int: ...

    def observed(self, sequence: int, result: Any) -> None: ...


class ActionPing:
    """Show a short, trusted label above the demo agent after action selection."""

    TAG = "noob_agent_action_ping"

    def __init__(self, manifest: EventWriter, transport: CommandWriter) -> None:
        self.manifest = manifest
        self.transport = transport

    @staticmethod
    def label(action: str, block: str | None) -> str:
        if action == "place" and block:
            name = block.removeprefix("minecraft:").replace("_", " ")
            return f"Place {name}"
        return {
            "break": "Break block",
            "interact": "Use lever",
            "observe": "Inspect block",
        }.get(action, "Act")

    def show(self, action: str, block: str | None) -> None:
        label = self.label(action, block)
        event = self.manifest.attempt(
            "action_ping", {"action": action, "block": block, "label": label}
        )
        # The label is assembled only from the validated action schema and block ID.
        text = label.replace("\\", "").replace("'", "")
        self.transport.command(
            f"kill @e[type=minecraft:text_display,tag={self.TAG}]"
        )
        self.transport.command(
            "execute at noobagentbot run summon minecraft:text_display ~ ~2.45 ~ "
            "{Tags:[\"noob_agent_action_ping\"],billboard:\"center\","
            "alignment:\"center\",background:805306368,shadow:1b,see_through:1b,"
            "view_range:1.0f,transformation:{scale:[1.25f,1.25f,1.25f]},"
            f"text:'{{\"text\":\"{text}\",\"color\":\"#55ff55\",\"bold\":true}}'}}"
        )
        self.manifest.observed(event, {"shown": True, "duration_ticks": 40})


def prepare_lamp_repair(actions: Actions) -> dict[str, Any]:
    """Seed fixed endpoints and leave only the connecting dust for the agent."""
    states = {
        "lever": actions.observe(LEVER_POSITION),
        "wire": actions.observe(WIRE_POSITION),
        "lamp": actions.observe(LAMP_POSITION),
    }
    if any(state.get("name") != "minecraft:air" for state in states.values()):
        raise ValueError("Lamp repair demo cells are not clear after reset")
    lever = actions.apply(
        "place",
        LEVER_POSITION,
        "minecraft:lever",
        {"face": "floor", "facing": "north", "powered": False},
    )
    lamp = actions.apply("place", LAMP_POSITION, "minecraft:redstone_lamp")
    return {
        "kind": "seeded_lamp_repair",
        "fixture_created_by_harness": True,
        "model_built": False,
        "lever_position": LEVER_POSITION,
        "missing_connection_position": WIRE_POSITION,
        "lamp_position": LAMP_POSITION,
        "fixture_effects_verified": bool(lever.get("effect_verified"))
        and bool(lamp.get("effect_verified")),
    }


class LampRepairCheck:
    """Require a real wire connection and observed off/on/off lamp behavior."""

    def __init__(self) -> None:
        self.powered_output_seen = False

    def save_state(self) -> dict[str, bool]:
        return {"powered_output_seen": self.powered_output_seen}

    def load_state(self, state: dict[str, Any]) -> None:
        self.powered_output_seen = state.get("powered_output_seen") is True

    def __call__(self, actions: BlockReader) -> dict[str, Any]:
        lever, wire, lamp = (
            actions.observe(position) for position in (LEVER_POSITION, WIRE_POSITION, LAMP_POSITION)
        )
        powered = lever.get("properties", {}).get("powered")
        wire_power = wire.get("properties", {}).get("power")
        lit = lamp.get("properties", {}).get("lit")
        fixture_valid = (
            lever.get("name") == "minecraft:lever" and lamp.get("name") == "minecraft:redstone_lamp"
        )
        connected = wire.get("name") == "minecraft:redstone_wire"
        passed = False
        complete = False
        if fixture_valid and not connected:
            name = "connection_missing"
        elif fixture_valid and connected and powered is False and lit is False and wire_power == 0:
            name = "repair_verified_off_on_off" if self.powered_output_seen else "wire_ready_off"
            passed = True
            complete = self.powered_output_seen
        elif fixture_valid and connected and powered is True and lit is True and wire_power == 15:
            name = "powered_output_verified"
            passed = True
            self.powered_output_seen = True
        else:
            name = "signal_mismatch"
        return {
            "name": name,
            "passed": passed,
            "complete": complete,
            "lever": lever,
            "wire": wire,
            "lamp": lamp,
            "scope": "seeded_lamp_repair_only",
        }
