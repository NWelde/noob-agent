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
vanilla server and the versioned Minecraft data packs:

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
