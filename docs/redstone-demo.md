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

## Demo-trial limits and escalation

The command above uses today's exact limits unchanged (12 decisions, 24
primitives, 120s per attempt, a 4,000-token Action cap, a 1,000,000-token
Builder cap, one repair, a 600s whole-run deadline, a 1,100,000-token
ceiling, a 30-call budget). Every one of those is now also a CLI flag:
`--decisions`, `--primitives`, `--wall-time-ms`, `--action-cap`,
`--builder-cap`, `--repairs`, `--deadline-s`, `--token-ceiling`, and
`--call-budget`.

`hackathon_plan.md` section 26 (26.3/26.4, approved 2026-09-18) authorizes a
labeled non-benchmark demo-trial mode with a token budget of 1,000,000 or
more and decision limits large enough to complete the sample task, with
limits raised incrementally until the task completes:

```sh
NOOB_AGENT_SANDBOX_MODE=local uv run --env-file .env python \
  scripts/run_minecraft_redstone_demo.py --trial
```

`--trial` raises the limits to 36 decisions, 72 primitives, a 360s per-attempt
wall clock, an 8,000-token Action cap, a 32,000-token Builder cap, 3 repairs,
a 1,800s whole-run deadline, the same 1,100,000-token ceiling, and a 90-call
budget. Any explicit flag still overrides the trial default for that one
limit.

```sh
NOOB_AGENT_SANDBOX_MODE=local uv run --env-file .env python \
  scripts/run_minecraft_redstone_demo.py --escalate
```

`--escalate` implies `--trial`. Completion is the Builder accepting a skill
AND the reuse attempt lighting the lamp (a lit cold lamp is not required). If
an attempt does not complete, a fresh attempt reruns with only the limit the
records show was exhausted doubled — wall time, decision/primitive limit,
Action cap (max 32,000), Builder cap (max 128,000), whole-run deadline (max
7,200s), or the token/call ceiling (tokens max 8,000,000) — up to 4
escalations. Each attempt writes its own database and JSON at
`.noob-agent/<base-run-id>-a01.{sqlite3,json}`, `-a02`, and so on, all
labeled `non-benchmark-minecraft-redstone-demo-trial`, plus one index JSON at
`.noob-agent/<base-run-id>-index.json` listing every attempt's limits, stop
reason, tokens by phase, cold/reuse lamp-lit state, skill ID, and skill uses.

This mode's classification and doubling are pure functions
(`classify_exhausted_limit`, `double_limit` in
`scripts/run_minecraft_redstone_demo.py`), unit-tested in
`tests/test_minecraft_redstone_trial_escalation.py` against fixtures built
from live evidence, without a live model, connector, or database.

Validation includes deterministic harness/pack checks and the existing easy-runner,
Builder, and skill-validation tests. A separate scripted control check verified
barrel inspection, collection, block placement, and lamp success before the model
comparison. That control check is not a cold model result.

To roll back, stop using this runner and disable only the `redstone-lamp-v1`
datapack. Existing benchmark runners, records, and original rooms remain available.
