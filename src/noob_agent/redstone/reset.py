"""Trusted deterministic reset, separate from planner actions.

The snapshot covers every cell of the build prism, foundation and player apron,
not the infinite surrounding world. Hashes include every block state property.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from noob_agent.redstone.contract import load_contract
from noob_agent.redstone.rcon import RconClient
from noob_agent.redstone.sidecar import Sidecar
from noob_agent.redstone.trial import CONTRACT_PATH, RUN_DIRECTORY, CommandTransport, TrialManifest


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def layer(y: int) -> dict[str, Any]:
    name = "bedrock" if y == 60 else "dirt" if y < 63 else "grass_block" if y == 63 else "air"
    state = {"name": "minecraft:" + name, "properties": {"snowy": False} if y == 63 else {}}
    digest = hashlib.sha256(((canonical(state) + "\n") * (96 * 112)).encode()).hexdigest()
    return {"y": y, "sha256": digest}


ITEMS = [
    "stone",
    "glass",
    "redstone",
    "redstone_torch",
    "repeater",
    "comparator",
    "lever",
    "stone_button",
    "redstone_lamp",
    "redstone_block",
    "oak_sign",
]
BASELINE: dict[str, Any] = {
    "schema_version": 1,
    "minecraft_version": "1.21.1",
    "min": [0, 60, 0],
    "max": [95, 95, 111],
    "layers": [layer(y) for y in range(60, 96)],
    "inventory": [
        {"slot": 36 + i if i < 9 else i, "name": name, "count": 16 if name == "oak_sign" else 64}
        for i, name in enumerate(ITEMS)
    ],
    "player": {
        "position": [48.5, 64, 98.5],
        "yaw_degrees": 180,
        "pitch_degrees": 0,
        "gameMode": "creative",
        "dimension": "overworld",
    },
    "settings": {
        "seed": 1600,
        "difficulty": "peaceful",
        "daytime": 6000,
        "doDaylightCycle": False,
        "doWeatherCycle": False,
        "randomTickSpeed": 0,
        "weather": "clear",
        "tick_rate": 20,
    },
}


class ResetError(RuntimeError):
    """A complete baseline comparison failed; readiness is not established."""


def baseline_hash(baseline: object = BASELINE) -> str:
    return hashlib.sha256(canonical(baseline).encode()).hexdigest()


def verify_scan(scan: dict[str, Any]) -> None:
    if scan != {"layers": BASELINE["layers"], "blocks": 96 * 112 * 36}:
        raise ResetError("Full world snapshot differs from baseline")


def verify_player(player: dict[str, Any]) -> None:
    expected = {
        "username": "noobagentbot",
        "gameMode": "creative",
        "dimension": "overworld",
        "orientationUnits": "radians",
    }
    if any(player.get(k) != v for k, v in expected.items()):
        raise ResetError("Player identity or mode differs")
    position = player.get("position", [])
    if len(position) != 3 or any(
        not math.isclose(a, b, abs_tol=1e-5)
        for a, b in zip(position, [48.5, 64, 98.5], strict=True)
    ):
        raise ResetError("Player position differs")
    # Mineflayer zero radians corresponds to Minecraft command yaw 180 degrees.
    yaw, pitch = player.get("yaw", math.nan), player.get("pitch", math.nan)
    if not math.isclose(math.remainder(yaw, 2 * math.pi), 0, abs_tol=1e-5) or not math.isclose(
        pitch, 0, abs_tol=1e-5
    ):
        raise ResetError("Player orientation differs")
    actual = sorted(player.get("inventory", []), key=lambda item: item["slot"])
    if actual != sorted(BASELINE["inventory"], key=lambda item: item["slot"]):
        raise ResetError("Inventory differs")


class TrustedReset:
    def __init__(self, manifest: TrialManifest, transport: CommandTransport, sidecar: Sidecar):
        self.manifest, self.transport, self.sidecar = manifest, transport, sidecar

    def command(self, command: str) -> str:
        sequence = self.manifest.attempt("trusted_reset", {"command": command})
        response = self.transport.command(command)
        self.manifest.observed(sequence, {"response": response})
        return response

    def restore(self) -> None:
        record: dict[str, Any] = {"baseline_sha256": baseline_hash(), "verified": False}
        self.manifest.data.setdefault("resets", []).append(record)
        self.manifest.data["template"] = {
            "sha256": baseline_hash(),
            "verified": False,
            "snapshot": BASELINE,
        }
        self.manifest.data["initial_conditions"] = {"verified": False}
        self.manifest.save()
        # Force the complete owned region loaded; no external server/world or lifecycle changes.
        for command in [
            "forceload add 0 0 95 111",
            "difficulty peaceful",
            "gamerule doDaylightCycle false",
            "gamerule doWeatherCycle false",
            "gamerule randomTickSpeed 0",
            "time set 6000",
            "weather clear",
            "tick rate 20",
            "tick unfreeze",
        ]:
            self.command(command)
        # Each layer is below the vanilla 32768-block command limit.
        for y in range(95, 59, -1):
            name = (
                "bedrock"
                if y == 60
                else "dirt"
                if y < 63
                else "grass_block[snowy=false]"
                if y == 63
                else "air"
            )
            self.command(f"fill 0 {y} 0 95 {y} 111 minecraft:{name}")
        self.command("gamemode creative noobagentbot")
        self.command("clear noobagentbot")
        for i, item in enumerate(BASELINE["inventory"]):
            slot = f"hotbar.{i}" if i < 9 else f"inventory.{i - 9}"
            self.command(
                f"item replace entity noobagentbot {slot} with "
                f"minecraft:{item['name']} {item['count']}"
            )
        self.command("tp noobagentbot 48.5 64 98.5 180 0")
        self.sidecar.request({"op": "settle"})
        scan = self.sidecar.request({"op": "baseline"})
        verify_scan(scan)
        player = self.sidecar.request({"op": "player"})
        verify_player(player)
        settings = {
            "seed": ("seed", "Seed: [1600]"),
            "difficulty": ("difficulty", "The difficulty is Peaceful"),
            "daytime": ("time query daytime", "The time is 6000"),
            "daylight": (
                "gamerule doDaylightCycle",
                "Gamerule doDaylightCycle is currently set to: false",
            ),
            "weather_cycle": (
                "gamerule doWeatherCycle",
                "Gamerule doWeatherCycle is currently set to: false",
            ),
            "random_ticks": (
                "gamerule randomTickSpeed",
                "Gamerule randomTickSpeed is currently set to: 0",
            ),
            "weather": (
                'execute if predicate {"condition":"minecraft:weather_check",'
                '"raining":false,"thundering":false} run time query daytime',
                "The time is 6000",
            ),
        }
        responses = {}
        for key, (command, expected) in settings.items():
            responses[key] = self.command(command)
            if responses[key] != expected:
                raise ResetError("Effective setting differs: " + key)
        tick = self.command("tick query")
        if not tick.startswith("The game is running normallyTarget tick rate: 20.0 per second."):
            raise ResetError("Tick setting differs")
        responses["tick"] = tick
        record.update(verified=True, world=scan, player=player, settings=responses)
        self.manifest.data["template"]["verified"] = True
        self.manifest.data["initial_conditions"] = {"verified": True, "player": player}
        self.manifest.save()


def run_resets(root: Path = RUN_DIRECTORY) -> TrialManifest:
    """Infrastructure only: two full restorations with an intervening dirty fixture."""
    manifest = TrialManifest(root)
    manifest.data["kind"] = "infrastructure_reset_check"
    contract = load_contract(CONTRACT_PATH)
    manifest.data["contract"] = {
        "version": contract.version,
        "path": str(CONTRACT_PATH),
        "sha256": hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest(),
    }
    manifest.data["limits"] = contract.budgets.model_dump(mode="json")
    try:
        with RconClient.dedicated() as transport, Sidecar(manifest) as sidecar:
            reset = TrustedReset(manifest, transport, sidecar)
            reset.restore()
            reset.command("setblock 48 64 95 minecraft:redstone_block")
            dirty = sidecar.request({"op": "settle"})
            dirty = sidecar.request({"op": "block", "position": [48, 64, 95]})
            if dirty["name"] != "minecraft:redstone_block":
                raise ResetError("Dirty fixture not observed")
            reset.restore()
    except BaseException as error:
        manifest.data["errors"].append({"stage": "reset_check", "type": type(error).__name__})
        if not isinstance(error, Exception):
            raise
    finally:
        manifest.finish_incomplete(
            [
                "Infrastructure reset check only; bounded actions and lever smoke pending",
                "Trial recording missing; no model success or milestone acceptance",
            ]
        )
    return manifest


def run_smoke(root: Path = RUN_DIRECTORY) -> TrialManifest:
    """Private infrastructure fixture, never a planner design or model success."""
    from noob_agent.redstone.actions import Actions

    manifest = TrialManifest(root)
    manifest.data["kind"] = "infrastructure_lever_smoke"
    contract = load_contract(CONTRACT_PATH)
    manifest.data["contract"] = {
        "version": contract.version,
        "path": str(CONTRACT_PATH),
        "sha256": hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest(),
    }
    manifest.data["limits"] = contract.budgets.model_dump(mode="json")
    try:
        with RconClient.dedicated() as transport, Sidecar(manifest) as sidecar:
            reset = TrustedReset(manifest, transport, sidecar)
            reset.restore()
            actions = Actions(manifest, transport, sidecar)
            lever, dust, lamp = [48, 64, 95], [49, 64, 95], [50, 64, 95]
            actions.apply(
                "place",
                lever,
                "minecraft:lever",
                {"face": "floor", "facing": "north", "powered": False},
            )
            actions.apply("place", dust, "minecraft:redstone_wire")
            actions.apply("place", lamp, "minecraft:redstone_lamp")
            for phase, powered in [("off", False), ("on", True), ("off_again", False)]:
                if phase != "off":
                    actions.apply("interact", lever)
                actual_dust, actual_lamp = actions.observe(dust), actions.observe(lamp)
                passed = (
                    actual_dust["name"] == "minecraft:redstone_wire"
                    and actual_lamp["name"] == "minecraft:redstone_lamp"
                    and actual_dust["properties"].get("power") == (15 if powered else 0)
                    and actual_lamp["properties"].get("lit") is powered
                )
                manifest.data["checks"].append(
                    {"name": phase, "passed": passed, "dust": actual_dust, "lamp": actual_lamp}
                )
                manifest.save()
                if not passed:
                    raise ResetError("Independent smoke observation failed")
            actions.apply("break", lamp)
            reset.restore()
    except BaseException as error:
        manifest.data["errors"].append({"stage": "smoke", "type": type(error).__name__})
        if not isinstance(error, Exception):
            raise
    finally:
        manifest.finish_incomplete(
            [
                "Infrastructure only; trial recording missing",
                "No model success or milestone acceptance",
            ]
        )
    return manifest
