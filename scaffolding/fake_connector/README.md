# Fake-Connector Transcript (non-production scaffolding)

**This is disposable scaffolding, not production code.** Nothing in the main
application imports, calls, builds, or deploys it. It adds no dependency, starts
no service, reads no credential, touches no shared runtime configuration, and
changes no CI. It is safe to delete at any time — see [Removal](#removal).

It provides a deterministic, standalone scripted transcript of one fake
Minecraft episode, shaped to the public examples in
[`connector_contract.md`](../../connector_contract.md), plus a validator that
checks the transcript against that contract.

## Run

```sh
uv run python scaffolding/fake_connector/validate.py
```

Exit code `0` prints a check count and a stable SHA-256 of the transcript bytes;
exit code `1` prints every violation found. The script imports only the Python
standard library, so `python scaffolding/fake_connector/validate.py` works
equally well with any Python 3.11+ interpreter.

Point it at a different directory to validate a copy — this is how the scaffold's
own negative checks are exercised:

```sh
uv run python scaffolding/fake_connector/validate.py /tmp/mutated-transcript
```

## What the transcript covers

`transcript/` holds one episode, in filename order. Every file is canonical JSON:
sorted keys, two-space indent, trailing newline — so the bytes are stable and
hashable. The validator normalizes line endings before comparing or hashing, so
the reported fingerprint is identical on an LF or a CRLF checkout.

| File | Covers |
| --- | --- |
| `manifest.json` | `ConnectorManifest` with the eight minimum Minecraft BDP primitive tools |
| `00_reset.json` | `reset` — the sequence-0 observation |
| `01_observe.json` | `observe` — a non-state-changing primitive |
| `02_move_to.json` | one successful state-changing primitive action |
| `03_invalid_tool.json` | one rejected action, `INVALID_TOOL`, charged no primitive call |
| `04_use_object_terminal.json` | the terminal observation, `terminal_reason: goal_reached` |

Each step file is an envelope — `{"kind": "step", "request": …, "result": …}` —
so that a `ToolRequest` stays paired with its `StepResult`. The objects *inside*
the envelope carry exactly the contract's fields and nothing else; the validator
rejects any missing or extra key.

## What the validator checks

Four groups are mandatory for this scaffold:

- **Sequence progression** — `0` after reset, then one increment per recorded
  step, with each embedded observation carrying its result's sequence.
- **Action-ID matching** — every request receives one durable result with the
  same `action_id`, no `action_id` is reused, and `last_action_id` names the
  action that actually reached the game.
- **Result statuses** — `status` and `code` come from the contract's sets, and
  the budget rules hold: a rejected request is charged no primitive call,
  advances no logical time, and reports no state change; an executed primitive is
  charged at least once.
- **Absence of private fields** — a recursive scan rejects any key or string
  value carrying a clean/faulty label, grader state, held-out configuration, or
  a hidden-mechanic answer, and `scenario_id` may not encode clean/faulty
  identity.

It also checks manifest shape and tool-name uniqueness, that non-rejected tools
are declared in the manifest while an `INVALID_TOOL` rejection is not, exact
field sets on every contract object, `terminal_reason` present exactly when
`terminal` is true, no entry after the terminal observation, unique
episode-local `object_id`s, and canonical byte ordering.

## Scaffold conventions and known limitations

These are this scaffold's choices where `connector_contract.md` is silent. They
are assumptions to confirm against the real connector, not contract facts.

- **`OK` success code.** The contract enumerates failure codes only. This
  transcript uses `OK` for a result that completed normally.
- **A rejected request still advances `sequence`.** The contract says sequence
  increases after each step; this scaffold treats a rejected request as a
  recorded step, so the sequence advances while `logical_time` does not. If the
  real connector instead leaves sequence untouched on rejection, this fixture and
  the corresponding check both need updating.
- **The envelope keys** (`kind`, `request`, `result`, `observation`) are a
  transcript-file convention, not part of the connector contract.
- **Single scripted episode.** One game, one seed's worth of content, no reset
  repeatability, timeout classification, budget exhaustion, or `failed`/`unknown`
  status coverage. Those need more fixtures.
- **The validator restates the contract's field sets by hand** rather than
  importing the production models, because scaffolding may not import production
  code. The duplication is deliberate: drift shows up as a validator failure,
  but the validator is not proof that the production models agree.
- **No live anything.** No game server, no network, no credentials, no W&B.

## Removal

The scaffold is self-contained in this directory. To remove it:

```sh
rm -rf scaffolding/fake_connector
```

Nothing else references it, so no other file needs editing and no test breaks.
Add a `[FIXED]` or `[DOCUMENTED]` entry to `CHANGELOG.md` noting the removal.

Promoting any of this into the main application requires an explicitly approved
`hackathon_plan.md` section, a failing test written first, and a separate pull
request.
