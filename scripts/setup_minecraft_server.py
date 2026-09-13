"""Set up a local vanilla Minecraft 1.21.1 server for the noob-agent demos.

One command replaces the manual steps in `docs/minecraft-server.md`: it downloads
and verifies the official server jar, writes `server.properties`, allowlists and
grants operator permission to the offline-mode connector bot, installs every
scenario data pack from `scenarios/minecraft/`, and installs the Mineflayer
sidecar dependency. You must accept the Minecraft EULA yourself:

    uv run python scripts/setup_minecraft_server.py --accept-eula --player <your-name>

Then start the server with the command it prints. The server listens only on
127.0.0.1 by default and runs in offline mode, so never expose it to a network.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
import uuid
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = REPO_ROOT / "scenarios" / "minecraft"
SIDECAR = REPO_ROOT / "src" / "noob_agent" / "connectors" / "minecraft_sidecar"
DEFAULT_DIRECTORY = REPO_ROOT / ".noob-agent" / "minecraft-server"

# Official Mojang server jar for 1.21.1. The URL path is the jar's SHA-1.
SERVER_JAR_SHA1 = "59353fb40c36d304f2035d51e7d6e6baa98dc05c"
SERVER_JAR_URL = f"https://piston-data.mojang.com/v1/objects/{SERVER_JAR_SHA1}/server.jar"
EULA_URL = "https://aka.ms/MinecraftEULA"

# Must match `MinecraftSettings` in src/noob_agent/connectors/minecraft.py.
BOT_NAME = "noobagentbot"
PORT = 25566
WORLD = "noob-agent-training"


def offline_uuid(name: str) -> str:
    """The UUID an offline-mode server assigns to a player name."""
    digest = bytearray(hashlib.md5(f"OfflinePlayer:{name}".encode()).digest())
    digest[6] = digest[6] & 0x0F | 0x30
    digest[8] = digest[8] & 0x3F | 0x80
    return str(uuid.UUID(bytes=bytes(digest)))


def download(url: str, destination: Path) -> None:
    with urllib.request.urlopen(url, timeout=120) as response, destination.open("wb") as out:
        shutil.copyfileobj(response, out)


def _sha1(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def _server_jar(directory: Path) -> bool:
    jar = directory / "server.jar"
    if jar.is_file() and _sha1(jar) == SERVER_JAR_SHA1:
        print(f"Server jar already present: {jar}")
        return True
    print(f"Downloading the official Minecraft 1.21.1 server jar to {jar} ...", flush=True)
    download(SERVER_JAR_URL, jar)
    if _sha1(jar) != SERVER_JAR_SHA1:
        jar.unlink(missing_ok=True)
        print("The downloaded server jar failed its SHA-1 check.", file=sys.stderr)
        return False
    return True


def _properties(server_ip: str) -> str:
    return "\n".join(
        [
            f"server-ip={server_ip}",
            f"server-port={PORT}",
            "online-mode=false",
            "enforce-secure-profile=false",
            "white-list=true",
            "enforce-whitelist=true",
            "enable-command-block=true",
            "spawn-protection=0",
            f"level-name={WORLD}",
            "motd=noob-agent local evaluation server",
            "",
        ]
    )


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")


def _players(directory: Path, extra_players: Sequence[str]) -> None:
    names = [BOT_NAME, *[name for name in extra_players if name != BOT_NAME]]
    _write_json(
        directory / "whitelist.json",
        [{"uuid": offline_uuid(name), "name": name} for name in names],
    )
    _write_json(
        directory / "ops.json",
        [
            {
                "uuid": offline_uuid(BOT_NAME),
                "name": BOT_NAME,
                "level": 4,
                "bypassesPlayerLimit": False,
            }
        ],
    )


def _datapacks(directory: Path) -> list[str]:
    target = directory / WORLD / "datapacks"
    target.mkdir(parents=True, exist_ok=True)
    installed = []
    for pack in sorted(SCENARIOS.iterdir()):
        if not (pack / "pack.mcmeta").is_file():
            continue
        shutil.copytree(pack, target / pack.name, dirs_exist_ok=True)
        installed.append(pack.name)
    return installed


def _npm_install() -> bool:
    npm = shutil.which("npm")
    if npm is None:
        print("npm was not found. Install Node.js 22 or newer, then rerun.", file=sys.stderr)
        return False
    print("Installing the Mineflayer sidecar dependency (npm ci) ...", flush=True)
    return subprocess.run([npm, "ci"], cwd=SIDECAR).returncode == 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument(
        "--accept-eula",
        action="store_true",
        help=f"Confirm you have read and accept the Minecraft EULA ({EULA_URL}).",
    )
    parser.add_argument(
        "--player",
        action="append",
        default=[],
        help="Allowlist a player name so you can watch from a Minecraft 1.21.1 client.",
    )
    parser.add_argument(
        "--server-ip",
        default="127.0.0.1",
        help="Interface to bind. Use 0.0.0.0 only to watch from Windows into WSL.",
    )
    parser.add_argument("--skip-npm", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if not args.accept_eula:
        print(
            f"Read the Minecraft EULA at {EULA_URL}, then rerun with --accept-eula.",
            file=sys.stderr,
        )
        return 2

    directory: Path = args.dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if not _server_jar(directory):
        return 1
    (directory / "eula.txt").write_text("eula=true\n")
    (directory / "server.properties").write_text(_properties(args.server_ip))
    _players(directory, args.player)
    packs = _datapacks(directory)
    if not args.skip_npm and not _npm_install():
        return 1

    print(f"\nInstalled data packs: {', '.join(packs)}")
    print("Start the server (Java 21 required) and leave it running:\n")
    print(f'  cd "{directory}" && java -Xms1G -Xmx2G -jar server.jar nogui\n')
    print(f"It is ready when the console prints 'Done'. It listens on {args.server_ip}:{PORT}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
