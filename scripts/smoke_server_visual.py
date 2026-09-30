"""Check visual transport and one independently verified temporary placement."""
import os

from noob_agent.redstone.actions import Actions
from noob_agent.redstone.rcon import RconClient
from noob_agent.redstone.sidecar import Sidecar
from noob_agent.redstone.trial import TrialManifest

os.environ["NOOB_PHYSICAL_ACTIONS"] = "0"
os.environ["NOOB_SERVER_VISUALS"] = "1"
manifest = TrialManifest()
with RconClient.dedicated() as transport, Sidecar(manifest, keep_connected=True) as reader:
    actions = Actions(manifest, transport, reader)
    assert actions.observe([10, 64, 10])["name"] == "minecraft:air"
    assert actions.observe([22, 64, 46])["name"] == "minecraft:air"
    print("PLACE", actions.apply("place", [22, 64, 46], "minecraft:stone"), flush=True)
    print("CLEANUP", actions.apply("break", [22, 64, 46]), flush=True)
    print("HARDWARE", actions.read({"op": "hardware"}), flush=True)
print("SMOKE_VERIFIED", manifest.path, flush=True)
