"""Read-only independent server predicates; never alter a control or block."""

import json
from pathlib import Path

from noob_agent.redstone.rcon import RconClient

queries = [
    "tick query",
    "execute if block 8 64 9 minecraft:redstone_wall_torch[lit=true]",
    "execute if block 9 64 9 minecraft:repeater[powered=true]",
    "execute if block 10 64 9 minecraft:repeater[locked=true]",
]
with RconClient.dedicated() as server:
    results = [{"query": query, "response": server.command(query)} for query in queries]
Path("demo/register-server-snapshot.json").write_text(json.dumps(results, indent=2))
print(json.dumps(results, indent=2))
