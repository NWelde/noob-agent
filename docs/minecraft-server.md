# Local Minecraft Server

This is the dedicated Java Edition server for the noob-agent Minecraft BDP. It
is separate from TLauncher client data and from the Git repository so worlds,
logs, allowlist state, and generated scenario data are never committed.

## Frozen baseline

| Setting | Value |
| --- | --- |
| Server implementation | Official vanilla Minecraft Java server |
| Minecraft version | 1.21.1 |
| Java runtime | OpenJDK 21.0.12 |
| Server directory | `/home/nathan/.local/share/noob-agent/minecraft-server` |
| World directory | `/home/nathan/.local/share/noob-agent/minecraft-server/noob-agent-training` |
| Server jar | `server-1.21.1.jar` |
| SHA-1 | `59353fb40c36d304f2035d51e7d6e6baa98dc05c` |
| Host | WSL2 local host; `127.0.0.1` from WSL |
| Port | `25566` |
| Client address | `<current-WSL-IP>:25566` from Windows; see Client profile below |
| Authentication | Offline mode; no Microsoft or TLauncher credentials |
| Agent player name | `noobagentbot` |
| Verified human player name | `nathanbeyene` |
| World name | `noob-agent-training` |
| tmux session | `noob-agent-minecraft` |

The official download source was Mojang's version manifest and the official
server jar. Mojang notes that use of the server software is subject to the
[Minecraft EULA](https://www.minecraft.net/en-us/eula).

## Configuration

The server accepts only allowlisted offline-mode player names. This is suitable
only for a private local development environment; never expose this server to a
public network or forward its port.

Important non-default `server.properties` values:

```properties
server-ip=
server-port=25566
online-mode=false
enforce-secure-profile=false
white-list=true
enforce-whitelist=true
enable-command-block=true
spawn-protection=0
level-name=noob-agent-training
motd=noob-agent local evaluation server
```

`server-ip` is deliberately blank so the Java server binds to its available
interfaces. The server runs under WSL2. On the verified machine setup, Windows
could reach the WSL address but not `localhost:25566`; do not change the
connector's port to compensate.

The world spawn is fixed at `0 100 -5` with `spawnRadius=0`, directly inside
the Resonator room. This prevents a human client from first appearing at the
natural world spawn's powder snow. The scenario reset also teleports all
connected players to `0.5 100 -5.5`, sets Adventure mode, and reconstructs the
room.

## Operations

Start the server in its named tmux session:

```sh
tmux new-session -d -s noob-agent-minecraft \
  'cd /home/nathan/.local/share/noob-agent/minecraft-server && ./start-server.sh'
```

Attach to its console with `tmux attach -t noob-agent-minecraft`. Detach
without stopping it with `Ctrl-b d`. Before starting another copy, check
`tmux has-session -t noob-agent-minecraft` or `ss -ltnp | rg ':25566\b'`;
another process using the same world will correctly fail on `session.lock`.

At the Minecraft server console, add the human client and connector bot:

```text
whitelist add <your-TLauncher-player-name>
whitelist add noobagentbot
```

The bot does not need an account or secret. The approved step-1 connector uses
the scenario data pack's deterministic reset function, so grant only its local
development identity permission to issue that command:

```text
op noobagentbot
```

Never reuse this offline-mode operator identity on a public server.

Initialize or reset the scenario while clients are connected:

```text
function noob_agent:reset
```

If the world is rebuilt and its spawn is no longer in the room, restore it:

```text
setworldspawn 0 100 -5 0
gamerule spawnRadius 0
```

Install the connector sidecar dependency from its own package directory:

```sh
cd src/noob_agent/connectors/minecraft_sidecar
npm ci
```

Stop cleanly with `stop` at the server console. Do not kill the Java process;
the clean stop saves the world.

## Client profile

TLauncher is only a client launcher. Create/select a **vanilla 1.21.1** profile
to join this server; Forge profiles for another version are incompatible. The
verified local player is `nathanbeyene`. Minecraft Java names are limited to 16
characters, so the earlier `nathanweldegiorgis` name cannot encode the login
packet even if it is allowlisted.

Get the current WSL address immediately before connecting:

```sh
hostname -I
```

Use the first address followed by `:25566` in Multiplayer -> Direct Connection.
For example, the address observed on 2026-09-12 was
`172.17.247.132:25566`; WSL may assign a different address after a restart.
Enter the address with no leading spaces, protocol prefix, or trailing text.

## Verification

With the server running, verify the real Mineflayer protocol connection,
scenario reset, and public observation path:

```sh
cd /home/nathan/noob-agent
uv run pytest tests/test_connectors_minecraft.py -rs
```

The verified 2026-09-12 run passed all 23 tests, including both live tests. A
live test reported as skipped means the server or sidecar was unavailable; it
is not equivalent to a passing integration check.

Useful independent checks are:

```sh
ss -ltnp | rg ':25566\b'
powershell.exe -NoProfile -Command \
  'Test-NetConnection -ComputerName <current-WSL-IP> -Port 25566'
```

The PowerShell result must show `TcpTestSucceeded : True`.

## TLauncher troubleshooting

| Client symptom | Verified cause | Fix |
| --- | --- | --- |
| `Connection refused` for `localhost` | Windows-to-WSL localhost forwarding is unavailable | Use the first WSL IP from `hostname -I` with port `25566` |
| `Unknown host` for a numeric IP | Leading spaces were saved before the address | Select the whole address and type the IP with the first digit as the first character |
| `Failed to encode packet serverbound/minecraft:hello` | Player name exceeds Minecraft's 16-character limit | Use an allowlisted name of at most 16 characters, currently `nathanbeyene` |
| Client joins in powder snow | Natural world spawn was used instead of the scenario room | Run `setworldspawn 0 100 -5 0`, set `spawnRadius 0`, then run `function noob_agent:reset` |
| Client can join but connector tests skip | Mineflayer dependency or server process is unavailable | Run `npm ci` in the sidecar directory and confirm port `25566` is listening |

## Current scenario state

The resettable Resonator training data pack is implemented in
`scenarios/minecraft/resonator-training-v1` and deployed under the world's
`datapacks/noob_agent_resonator_v1` directory. Its executable two-run oracle
passes, and the connector can reset and observe the room through Mineflayer.

This is still the training world, not a complete evaluation set. Separate
validation, held-out-clean, and held-out-faulty snapshots with frozen seeds and
build fingerprints remain required before a real campaign. Follow
[`minecraft_scenario.md`](../minecraft_scenario.md) for the fixed mechanic,
private predicates, reset requirements, and leakage constraints.
