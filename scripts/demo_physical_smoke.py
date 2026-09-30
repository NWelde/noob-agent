"""Recorded, assisted geometry smoke; no reset or full computer success claim."""

import os
from pathlib import Path

from noob_agent.redstone.actions import Actions
from noob_agent.redstone.demo import ActionPing
from noob_agent.redstone.rcon import RconClient
from noob_agent.redstone.sidecar import Sidecar
from noob_agent.redstone.trial import TrialManifest

os.environ["NOOB_PHYSICAL_ACTIONS"] = "1"
manifest = TrialManifest(Path("demo/physical-smoke"))
manifest.data["assistance"] = "Sol-authored geometry smoke, not full computer construction"
placements = [
    ([26, 64, 46], "minecraft:stone", {}),
    ([26, 65, 46], "minecraft:repeater", {"facing": "north", "delay": 2}),
    ([26, 64, 45], "minecraft:redstone_wall_torch", {"facing": "north"}),
    ([21, 64, 50], "minecraft:lever", {"face": "floor", "facing": "north", "powered": False}),
    ([22, 64, 50], "minecraft:redstone_wire", {}),
    ([23, 64, 50], "minecraft:redstone_wire", {}),
    ([24, 64, 50], "minecraft:redstone_lamp", {}),
]
try:
    with (
        RconClient.dedicated() as transport,
        Sidecar(manifest, keep_connected=True, timeout=30) as reader,
    ):
        actions = Actions(manifest, transport, reader)
        ping = ActionPing(manifest, transport)
        for position, _, _ in placements:
            if actions.observe(position)["name"] != "minecraft:air":
                raise RuntimeError("Sacrificial cells occupied; preserve existing world")
        for position, block, properties in placements:
            ping.show("place", block)
            actions.apply("place", position, block, properties)
        assert actions.observe([24, 64, 50])["properties"]["lit"] is False
        for expected in [True, False]:
            ping.show("interact", None)
            actions.apply("interact", [21, 64, 50])
            actual = actions.observe([24, 64, 50])
            assert actual["properties"]["lit"] is expected, actual
        manifest.data["physical_smoke_result"] = {"status": "passed", "computer_success": False}
        # Remove only original-air smoke cells, using genuine player digging.
        for position, _, _ in reversed(placements):
            actions.apply("break", position)
except BaseException as exc:
    manifest.data["physical_smoke_result"] = {
        "status": "failed",
        "type": type(exc).__name__,
        "computer_success": False,
    }
    raise
finally:
    manifest.save()
    print(manifest.path)
