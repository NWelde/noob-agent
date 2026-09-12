# Public Connector Data Model

`model.py` defines the immutable, JSON-safe public boundary between the
evaluation harness and a game connector. It does not implement connector
behavior or expose private scenario/grader state.

## Data flow

```text
ConnectorManifest
  └─ ToolDefinition[]

Observation → ToolRequest → StepResult → next Observation
```

## Models

- `ConnectorManifest` freezes the connector version, game ID, observation and
  timing modes, schema version, and unique primitive-tool definitions.
- `ToolDefinition` describes a generic control: its name, description,
  JSON-compatible argument schema, preconditions, duration limit, and whether
  it changes game state.
- `Observation` is the complete public state at one decision. It includes an
  episode-local sequence number, goal, public status, player state, visible
  objects, public messages, terminal state, and logical time.
- `PublicPlayerState`, `PublicPosition`, `VisibleObject`, and `PublicMessage`
  represent public world data. Object IDs must be opaque and episode-local.
- `ToolRequest` gives every requested primitive a durable action ID and
  JSON-compatible arguments.
- `StepResult` records the matching action ID, outcome, next observation, and
  primitive/logical/wall-time accounting. Its only allowed statuses are
  `succeeded`, `rejected`, `failed`, and `unknown`.

The models reject undeclared fields so a connector cannot accidentally put a
private grader field into a top-level record. That structural check is not a
complete privacy guarantee: connector implementations must still ensure that
public strings, IDs, and properties never encode hidden scenario answers.

## Deferred responsibilities

The next fake-connector milestone will enforce action-ID idempotency, sequence
progression across calls, budgets, reset determinism, and tool-specific
argument/precondition validation. Those rules require episode state and do not
belong in immutable data objects.
