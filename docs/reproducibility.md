# Reproducing Minecraft checks

## Local automated checks

Install the Python development dependencies and run the complete suite:

```sh
uv sync --group dev --group integrations
uv run pytest
```

The tests do not make model-provider calls. Minecraft live connector tests need
the dedicated server; sidecar tests also need the Node dependencies installed
with `npm ci` under `src/noob_agent/connectors/minecraft_sidecar/`.

## Connected fixture trial

With the dedicated test server running, run:

```sh
uv run python scripts/run_redstone_trial.py --trial fixture
```

This creates a unique manifest under `.noob-agent/redstone-trials/`. It
verifies reset, bounded construction, a deliberate public-check failure,
repair, and final reset using scripted fixture choices. It makes no provider
calls and cannot establish model-designed module success.

## Live provider trial

Provider mode is explicit and requires configured credentials. The frozen
trial contract, exact command options, latest attempts, and reset evidence are
maintained in [`redstone-trials.md`](redstone-trials.md). Record live model
results from the generated manifest; do not infer acceptance from a planner
reply, a placement acknowledgement, or a unit test.
