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

The verified 2026-09-12 primitive-completion run passed all 47 tests, including
repeatable live reset, live observe, and a live flow through all seven
additional primitives. A live test reported as skipped means the server or
sidecar was unavailable; it is not equivalent to a passing integration check.

Useful independent checks are:

```sh
ss -ltnp | rg ':25566\b'
powershell.exe -NoProfile -Command \
  'Test-NetConnection -ComputerName <current-WSL-IP> -Port 25566'
```

The PowerShell result must show `TcpTestSucceeded : True`.

## Connector primitive surface

Connector version `minecraft-0.2.0` exposes the complete frozen Minecraft
surface: `observe`, `move_to`, `look_at`, `inspect_object`, `collect_object`,
`use_object`, `place_object`, and `wait`.

Object IDs are opaque and episode-local. An action may use an ID only from the
latest confirmed observation; after every successful action, use the IDs in
the returned observation rather than retaining older ones. Inspecting a nearby
barrel or hopper adds its ordinary visible contents to that observation as
`container_item` objects. Collected items appear as `inventory_item` objects,
whose IDs can be supplied as `held_item_id` to `use_object` or `place_object`.

`place_object` places a block item when the target block position has ordinary
support. For non-block inventory items, it performs the normal drop control
toward the supplied adjacent public position. This is the operation used to
put a collected shard onto the Resonator basin; it does not issue a server
command or read scenario-private state.

The Python connector validates tool names, exact argument sets, numeric bounds,
finite coordinates, latest-observation IDs, and collectible/inventory object
preconditions before delivery. A rejected call is recorded but consumes no
primitive action. Delivered interactions settle for five game ticks before
the sidecar returns a fresh public snapshot.

Public messages include ordinary chat and `title ... actionbar` feedback, which
arrives as its own `action_bar` packet. Diagnostic `[noob:...]` narration,
including the server's `<name> [noob:...]` echo, and operator command echoes
such as `[Server: ...]` are never added to observations.

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
passes, and the connector can reset the room and execute all eight frozen
Minecraft primitives through Mineflayer. The live acceptance flow walks to the
supply barrel, inspects its public contents, collects shards, returns to the
basin, places a shard, activates the nearby control, and waits for visible
processing.

This is still the training world, not a complete evaluation set. Separate
validation, held-out-clean, and held-out-faulty snapshots with frozen seeds and
build fingerprints remain required before a real campaign. Follow
[`minecraft_scenario.md`](../minecraft_scenario.md) for the fixed mechanic,
private predicates, reset requirements, and leakage constraints.

## Live learning-loop smoke run

To inspect the real Minecraft loop and its Weave trace together, start the
local server, install the sidecar dependencies, and run:

```sh
NOOB_AGENT_SANDBOX_MODE=local \
  uv run --env-file .env python scripts/run_minecraft_live_smoke.py --live-smoke
```

The command refuses to start unless `NOOB_AGENT_TRACE_MODE=weave` and
`WEAVE_DISABLED=false` are configured. It writes to a fresh Git-ignored
`.noob-agent/<run-id>.sqlite3` database and prints its Weave run prefix.
It has a fixed 10-minute deadline, 2,000,000-token limit, 500 model-call limit,
1,000 delivered primitive actions across the run, five Builder repairs, and a
3,000-token Action cap. Each rapid cycle gives cold exploration at most 12
decisions, 24 primitives, and 90 seconds. It ends sooner after two stale-target
failures or five actions with no visible state change, then immediately gives
the bounded public trace to the Builder before same-room skill reuse.

This is a real-server diagnostic, not a Minecraft evaluation: it resets only
the training room for both the cold and accepted-skill reuse attempts. Do not
put its records in a comparison, report, or held-out result set.

## Easy-mode live diagnostic (non-benchmark)

Before asking a model to discover the Resonator mechanic, check that it can
do something trivial in first person: press the clearly visible nearby button
so the iron bars open. This controls for task difficulty and isolates basic
perception, action selection, JSON compliance, and connector execution. It is
a local diagnostic only; its records are not evaluation, held-out,
clean/faulty, grading, learning-transfer, or model-performance evidence.

Install the separate data pack once (vanilla rejects symlinks), then reload
from the server console:

```sh
cp -r scenarios/minecraft/easy-button-gate-v1 \
  /home/nathan/.local/share/noob-agent/minecraft-server/noob-agent-training/datapacks/noob_agent_easy_button_gate_v1
tmux send-keys -t noob-agent-minecraft 'reload' Enter
```

`reload` also runs the Resonator pack's load function, which resets that room
and teleports online players into it. The easy pack has no load function.

Run the diagnostic:

```sh
NOOB_AGENT_SANDBOX_MODE=local \
  uv run --env-file .env python scripts/run_minecraft_easy_live_smoke.py --live-easy-smoke
```

- Scenario `easy-button-gate-v1` resets with `function noob_agent_easy:reset`
  in a room at `x=24..34`, away from the Resonator room. Every reset gives
  players infinite Night Vision (plus Saturation and Resistance) and starts
  them facing the gateway with the labelled `Gate Button` in reach.
- The public goal states the task directly. The harness ends the episode when
  the room's public message `The iron-bar gateway opens.` appears. The frozen
  manifest, primitives, connector behavior, and Resonator goal are unchanged.
- It runs one cold episode, one Builder pass, and a same-room reuse only if a
  skill is accepted. There is no wall-clock deadline. Action calls may use
  32,000 output tokens and Builder calls 100,000, with five repairs. Finite
  guards remain only against runaway loops: 500 decisions and 1,000 primitives
  per episode, 2,000 model calls and 50,000,000 tokens per run, and the
  runner's standard stop after three identical failed calls.
- Both smoke runners show short demo narration: aqua `Seeing`, gold `Doing`,
  green `Pressing` for buttons, and purple `Learning` during Builder work.
  Only the leading verb is colored; the sentence stays white. Observations
  use visible object labels and actions describe the selected control, without
  claiming it succeeded. Full prompts, reasoning, replies, and errors remain
  in the recorded calls and Weave. Narration is excluded from observations.
  Formatted chat uses `/tellraw` with the local bot's existing operator permission.
- Press `Ctrl-C` to stop. The open episode is recorded as `unknown_result`,
  Weave is flushed, and the summary reports status `interrupted`.
- The terminal summary names the scenario, run status, stop reason, decisions,
  primitives, model calls, tokens, accepted skill, and the Git-ignored
  `.noob-agent/non-benchmark-minecraft-easy-<timestamp>.sqlite3` database.

To remove it, delete the world's `datapacks/noob_agent_easy_button_gate_v1`
directory, run `reload`, then `forceload remove 24 -4 34 4`.
