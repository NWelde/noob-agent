# noob-agent

noob-agent is a Python harness for evaluating whether an action agent can learn
reliable, reusable skills from interaction with unfamiliar games.

The authoritative product specification is
[hackathon_plan.md](hackathon_plan.md). The connector, skill, evaluation, and
Minecraft scenario contracts define its implementation boundaries.

## Development

Use Python 3.11 or newer. Install the project and its development tools with:

```sh
uv sync --group dev
```

Run the test suite with:

```sh
uv run pytest
```

The current `main` branch includes the typed connector contracts, SQLite episode
store, bounded cold-episode runner, best-effort Weave mirror, generated-skill
package checks and immutable registry, resettable Minecraft training scenario,
Minecraft reset/observe connector, and Builder-generated candidate path. See
[`docs/current-status.md`](docs/current-status.md) for the dated implementation
map, stacked-branch state, and next steps.

## Integration configuration

Copy `.env.example` to `.env` and provide only the credentials required for the
integration you enable. The application reads environment variables; it does not
load or transmit credentials during import or tests.

Install the optional integration SDKs when an approved milestone implements an
adapter:

```sh
uv sync --group dev --group integrations
```

The integration seams default to disabled. SQLite remains the source of truth;
the Weave adapter mirrors durable local events on a best-effort basis and never
makes episode finalization depend on a remote service.

## Local game environments

- [`docs/minecraft-server.md`](docs/minecraft-server.md) records the dedicated
  server and world locations, tmux operations, TLauncher connection details,
  live connector verification, and known setup failures.
- [`docs/doom-env.md`](docs/doom-env.md) records the non-production ViZDoom
  environment check for the later Doom connector milestone.
