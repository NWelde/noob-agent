"""Flush and archive the actual world before a new recorded trial."""

import tarfile
from datetime import UTC, datetime
from pathlib import Path

from noob_agent.redstone.rcon import RconClient

world = Path(".noob-agent/redstone-server/redstone-trials")
assert world.is_dir(), "Inspect the server world path before archiving"
destination = Path("demo/world-backups") / (
    datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-before-takeover-trial.tar.gz"
)
destination.parent.mkdir(parents=True, exist_ok=True)
with RconClient.dedicated() as server:
    print(server.command("save-all flush"), flush=True)
with tarfile.open(destination, "w:gz") as archive:
    archive.add(world, arcname="world")
print(destination, flush=True)
