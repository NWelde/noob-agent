# Minecraft redstone comparison

This is a non-benchmark, same-task cold versus learned-skill demonstration.
It does not establish held-out transfer or discovery of unfamiliar redstone rules.

The barrel contains one redstone block. The goal is to light the nearby lamp.
The public success message is emitted only when Minecraft reports the lamp lit.
The connector exposes the lamp, power block, and ordinary lit state to both attempts.

The runner uses the existing easy-demo orchestration in an isolated module:
cold attempt → Builder and validation → fresh agent and reset with an accepted
skill available. Both attempts have 12 decisions, 24 primitives, 120 seconds,
and 4,000 maximum output tokens per Action call. Builder gets 1,000,000 output
tokens and one repair. The entire sequence has a 600-second deadline and a
1,100,000-token call-start ceiling; one in-flight call can overshoot that ceiling.
Builder tokens are reported separately. An available skill need not be invoked;
the summary reports actual skill uses.

For a fresh machine, `uv run python scripts/setup_minecraft_server.py --accept-eula`
creates a local server with this data pack already installed (see the README's
"Run the demo yourself"). On an existing server, copy
`scenarios/minecraft/redstone-lamp-v1` into the world's `datapacks` directory and
run `reload` at its console. The room occupies
x=48..58, y=99..105, z=-4..4. Reset rebuilds that room and clears the bot's
inventory; human players are moved to a spectator viewpoint.

```sh
NOOB_AGENT_SANDBOX_MODE=local uv run --env-file .env python scripts/run_minecraft_redstone_demo.py
```

Weave tracing and a configured model are required. Local skill execution is
explicitly a local-subprocess demonstration, not a strong isolation claim.
The runner writes a separate SQLite trace and JSON summary under `.noob-agent/`.
Screen recording must start before the command; recording is not built into the runner.

Validation includes deterministic harness/pack checks and the existing easy-runner,
Builder, and skill-validation tests. A separate scripted control check verified
barrel inspection, collection, block placement, and lamp success before the model
comparison. That control check is not a cold model result.

To roll back, stop using this runner and disable only the `redstone-lamp-v1`
datapack. Existing benchmark runners, records, and original rooms remain available.
