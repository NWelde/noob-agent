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
| Server jar | `server-1.21.1.jar` |
| SHA-1 | `59353fb40c36d304f2035d51e7d6e6baa98dc05c` |
| Host | WSL2 local host; `127.0.0.1` from WSL |
| Port | `25566` |
| Client address | `localhost:25566` from Windows, subject to WSL localhost forwarding |
| Authentication | Offline mode; no Microsoft or TLauncher credentials |
| Agent player name | `noobagentbot` |
| World name | `noob-agent-training` |

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
interfaces. The server runs under WSL2. Windows normally reaches it through
`localhost:25566`; if that is unavailable, verify WSL localhost forwarding and
the Windows firewall rather than changing the connector's port ad hoc.

## Operations

Start the server:

```sh
cd /home/nathan/.local/share/noob-agent/minecraft-server
./start-server.sh
```

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

Install the connector sidecar dependency from its own package directory:

```sh
cd src/noob_agent/connectors/minecraft_sidecar
npm install
```

Stop cleanly with `stop` at the server console. Do not kill the Java process;
the clean stop saves the world.

## Client profile

TLauncher is only a client launcher. Create/select a **vanilla 1.21.1** profile
to join this server; the installed Forge 1.12.2 profile is incompatible. Use
`localhost:25566` in Multiplayer. Your display name must be allowlisted.

## Before implementing the scenario

The baseline server is not yet the Resonator scenario. Before evaluations,
create separate training, validation, held-out-clean, and held-out-faulty world
snapshots; freeze their seeds and build fingerprints. Follow
[`minecraft_scenario.md`](../minecraft_scenario.md) for the public mechanic,
private predicates, reset requirements, and leakage constraints.
