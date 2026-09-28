# noob-agent

## Current project

An agent uses Minecraft Java 1.21.1 to design and build a programmable 4-bit
redstone computer in a repeatable creative world. The planner proposes a circuit
and bounded build intentions; Jev executes those actions and reports observed
results. Independent in-world checks grade each module and the completed
machine.

The active build is the Minecraft redstone computer described in
[`hackathon_plan.md`](hackathon_plan.md). Infrastructure for resets, bounded
construction, planner/Jev interaction, module checks, and trial records is in
place. **No model-designed module has passed yet**, so the full computer demo is
not complete. Current evidence and the next live checkpoint are in
[`docs/redstone-trials.md`](docs/redstone-trials.md).

### Working demo slice

For a live, judgeable result today, run the bounded seeded lamp repair:

```sh
set -a; source .env; set +a
.venv/bin/python scripts/run_redstone_trial.py --trial provider --task lamp-repair \
  --keep-agent-connected \
  --planner-model deepseek-ai/DeepSeek-V4-Pro-0813 \
  --planner-project nathanweldegiorgis731-minerva-university/Noob-agent
```

The harness places a lever and lamp with one wire gap; the agent must place the
wire and operate the lever. The independent checker reads Minecraft state and
requires the lamp to transition off → on → off. This demonstrates repair of a
small seeded circuit only. The harness builds the endpoints, so this is not a
model-built circuit and does not complete the 4-bit computer. A successful live
run is recorded under `.noob-agent/redstone-trials/`.

Provider demos start or reuse one persistent Minecraft bot session by default.
If a run stalls, the bot and partial build stay in the world; the next trial
resets that same connection in place. Use `--disconnect-after-trial` only when
you want the old disconnect behavior. Stop a persistent session deliberately
with `.venv/bin/python scripts/run_redstone_trial.py --stop-agent`.

To continue the model-built register/computer attempt while keeping that same
bot session alive between retries, run the provider task with `--task computer
--keep-agent-connected`. A normal new trial resets the owned build area through
the existing bot session; `--resume` continues a stopped build after checking
the bot and all cells the previous session touched.

Both tasks automatically start a fresh session after a harness action or time
limit, keeping the same world and saved feedback. Automatic continuation runs
up to four sessions. If the limit is reached again, or the process itself is
interrupted, use the manifest path printed by the run to continue it:

```sh
.venv/bin/python scripts/run_redstone_trial.py --trial provider --task lamp-repair \
  --resume .noob-agent/redstone-trials/<run-id>/manifest.json \
  --planner-model deepseek-ai/DeepSeek-V4-Pro-0813 \
  --planner-project nathanweldegiorgis731-minerva-university/Noob-agent
```

The new session restores the saved planner feedback and checks the current build
in Minecraft before continuing. For the computer task, it tells the planner
which touched blocks changed or disappeared, so it can repair them. The optional
`--session-action-limit` sets a smaller per-session cap, which is useful for
checking that restart works.

## Run checks

Install the development environment and run the Python suite:

```sh
uv sync --group dev --group integrations
uv run pytest
```

The Minecraft sidecar checks use Node.js. Run them with:

```sh
cd src/noob_agent/connectors/minecraft_sidecar
npm ci
cd ../../../../
npm install --no-save
node --test tests/redstone_sidecar.test.cjs tests/minecraft_obs_recorder.test.mjs
npm run test:jev
```

Run lint and type checks with:

```sh
uv run ruff check src tests scripts
uv run mypy src
```

## Minecraft environment

Read the Minecraft EULA before accepting it. The setup command installs a local
vanilla server and the versioned Minecraft data packs. By default it targets
`.noob-agent/redstone-server`, creates the flat `redstone-trials` world, and
listens on port `25567` for the active redstone-computer demo:

```sh
uv run python scripts/setup_minecraft_server.py --accept-eula --player <minecraft-name>
```

Start the server using the command printed by setup. Operational details,
including the dedicated trial server and ports, are in
[`docs/minecraft-server.md`](docs/minecraft-server.md).

The current connected trial command is:

```sh
uv run python scripts/run_redstone_trial.py --trial fixture
```

This scripted fixture checks the connected build/grade/repair/reset plumbing.
It is not evidence of a model-designed circuit or a successful computer. Live
provider trials require explicit provider configuration; their latest status
and limitations are recorded in [`docs/redstone-trials.md`](docs/redstone-trials.md).

## Project map

- [`hackathon_plan.md`](hackathon_plan.md): approved scope and milestone order
- [`docs/redstone-computer-contract.md`](docs/redstone-computer-contract.md):
  observable computer behavior
- [`docs/redstone-trials.md`](docs/redstone-trials.md): implementation and live
  trial evidence
- [`docs/minecraft-server.md`](docs/minecraft-server.md): local server setup
- `src/noob_agent/redstone/`: bounded planner/Jev loop and independent graders
- `scripts/run_redstone_trial.py`: connected trial and infrastructure checks
- `scenarios/minecraft/`: versioned Minecraft fixtures and contract
