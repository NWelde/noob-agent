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

The current pre-BDP foundation establishes only the package and test layout.
It does not yet implement a connector, runner, model integration, skill runtime,
or game integration.

## Integration configuration

Copy `.env.example` to `.env` and provide only the credentials required for the
integration you enable. The application reads environment variables; it does not
load or transmit credentials during import or tests.

Install the optional integration SDKs when an approved milestone implements an
adapter:

```sh
uv sync --group dev --group integrations
```

The initial seams default to disabled. SQLite remains the source of truth; a
future Weave adapter must mirror completed local events rather than make episode
finalization depend on a remote service.
