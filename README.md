# Noob Agent

**Can an AI model build something that works, find its mistakes, and fix them?**

Noob Agent tests this in Minecraft. It asks a model to build a small programmable
computer from redstone, the game's wiring and logic parts. A bot carries out the
actions. Separate tests check whether the circuit works.

This gives us a concrete way to test planning, tool use, and recovery from
mistakes. We can inspect what the model built, what failed, and what it repaired.

[Code](https://github.com/NWelde/noob-agent) ·
[W&B project and run traces](https://wandb.ai/nathanweldegiorgis731-minerva-university/Noob-agent/overview)

## The computer

The project builds a 4-bit computer inside Minecraft Java 1.21.1. It has memory,
arithmetic, stored instructions, and four lamps that show its output in binary.
The computation happens in the redstone circuit.

| Instruction | What it does |
| --- | --- |
| `LOAD n` | Store a number in the working register |
| `ADD n` | Add a number; values wrap around at 16 |
| `OUT` | Copy the result to the output lamps |
| `HALT` | Stop execution and hold the result |

The machine contract defines two programs for the same hardware:

- `LOAD 3 → OUT → ADD 5 → OUT → HALT`: show **3, then 8**.
- `LOAD 14 → OUT → ADD 5 → OUT → HALT`: show **14, then 3**.

Only the stored program changes between tests. See the
[machine contract](docs/redstone-computer-contract.md) for the full specification.

## Results and demo

Most modules now pass their checks, including arithmetic. The polished demo is
ready; its viewing link will be added here.

- **Memory:** the 4-bit register passed all 49 load, hold, and reset checks.
- **Arithmetic:** checks pass in the latest build.
- **Repair:** the agent repaired a seeded lamp circuit and passed independent
  off → on → off checks.
- **Recovery:** a lamp-repair run completed across four bounded sessions, with
  verified resets.

Module checks and full-program checks are recorded separately. The final
computer test requires both programs to run on the same hardware and produce
the expected outputs.

The demonstration build used extra design and construction help. We keep its
results separate from tests of an unaided model so comparisons stay fair.
Earlier trial records include failed and partial runs.

## How it works

1. **Plan:** a model proposes a circuit and a small set of build actions.
2. **Act:** Jev selects actions, and a Mineflayer bot places blocks and operates
   controls.
3. **Check:** separate tests read the Minecraft world and check circuit behavior.
4. **Repair:** failures go back to the planner so it can revise the build.
5. **Record:** the system saves decisions, actions, results, and errors.

A model saying “done” does not pass a test. The circuit must produce the right
behavior in the game.

## Why the system is ready for real use

Agent runs can fail halfway through. Noob Agent is built to make those failures
visible and recoverable:

- Fixed task rules and build bounds keep each trial well defined.
- Action, time, and model-call limits bound each run.
- Saved trial records preserve failed actions and repair attempts.
- A persistent bot keeps the partial build available after a stopped run.
- Before resuming, the system checks the player and previously touched blocks.
- Independent tests judge the result from world state.

These features make the project useful as an evaluation system: judges and
researchers can inspect how a result was reached, not just watch the final clip.

## Tech stack

| Part | Tools |
| --- | --- |
| Planning | DeepSeek through W&B Inference |
| Action selection | Jev through the Vercel AI SDK |
| Trial loop and validation | Python, asyncio, Pydantic, Typer |
| Minecraft bot | Node.js, Mineflayer |
| Environment | Minecraft Java 1.21.1 |
| Traces and run records | W&B Weave, trial manifests, JSONL event journals |
| Recording | Replay Mod |
| Code checks | pytest, Node.js tests, Ruff, mypy |

## Run a trial

Use Python 3.11+, Node.js 22+, Java 21, and `uv`. Run these commands from the
repository root:

```sh
uv sync --group dev --group integrations
npm ci
npm --prefix src/noob_agent/connectors/minecraft_sidecar ci
```

Follow the [server setup guide](docs/minecraft-server.md) to create and start the
dedicated Minecraft server. The trial server uses game port `25567` and RCON
port `25577`.

Put `WANDB_API_KEY` and `AI_GATEWAY_API_KEY` in a local `.env` file. Choose an
available W&B model ID for `MODEL_ID` below.

Start with the small lamp-repair task:

```sh
uv run --env-file .env python scripts/run_redstone_trial.py \
  --trial provider --task lamp-repair \
  --planner-model MODEL_ID \
  --planner-project nathanweldegiorgis731-minerva-university/Noob-agent
```

For a computer trial, replace `--task lamp-repair` with `--task computer`.
A fresh trial resets the owned build area. To continue a stopped trial, add
`--resume .noob-agent/redstone-trials/<run-id>/manifest.json` with the same
provider configuration.

Provider runs keep the bot connected by default. To disconnect it deliberately:

```sh
uv run python scripts/run_redstone_trial.py --stop-agent
```

Run records are saved under `.noob-agent/redstone-trials/`. The
[trial documentation](docs/redstone-trials.md) explains grading, limits, and
recovery. The [memory demo guide](docs/memory-demo.md) covers the separate memory
and wire-repair demonstration.

## Check the code

```sh
uv run pytest
uv run ruff check src tests scripts
uv run mypy src
node --test tests/redstone_sidecar.test.cjs tests/minecraft_obs_recorder.test.mjs
npm run test:jev
```

Code tests check the software. Live Minecraft tests check the circuit.
