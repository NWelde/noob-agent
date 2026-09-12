# Game Connector Contract

## Purpose

The connector is the fixed boundary between noob-agent and a game. It gives an
agent the senses and controls that a new player receives, translates tool calls
into game actions, and returns public results. A game is supported when it
implements this contract; the Action agent, Builder, skill system, and evaluator
do not change.

The connector is written by us, not by the evaluated model. Its version and
primitive tools are frozen before a comparison begins.

## Trust boundary

The connector may read enough game state to implement reliable public
observations and actions. It must never expose private grader state, scenario
answers, clean/faulty labels, held-out configuration, or the meaning of an
unfamiliar mechanic.

The private grader is a separate component. The Action agent and generated
skills can call only the public connector.

## Required interface

Every game adapter implements these asynchronous Python methods:

```python
class GameConnector(Protocol):
    async def manifest(self) -> "ConnectorManifest": ...
    async def reset(self, scenario_id: str, seed: int) -> "Observation": ...
    async def step(self, request: "ToolRequest") -> "StepResult": ...
    async def is_terminal(self) -> bool: ...
    async def close(self) -> None: ...
```

`manifest()` returns the connector version, observation mode, timing model, and
complete primitive-tool definitions. A manifest is hashed and stored with every
episode. It cannot change after `reset()`.

Only the harness calls `reset()` and `close()`. The Action agent and generated
skills may call tools declared in the manifest.

## Observation

An observation is a complete public snapshot for one decision. It is data, not
a hint written for the model.

```python
class Observation(BaseModel):
    episode_id: str
    sequence: int                  # 0 after reset; increases after each step
    game_id: str
    scenario_id: str               # public family ID, not clean/faulty identity
    public_goal: str
    status: dict[str, int | float | str | bool | None]
    player: PublicPlayerState
    visible_objects: list[VisibleObject]
    messages: list[PublicMessage]
    last_action_id: str | None
    terminal: bool
    terminal_reason: str | None
    logical_time: int              # game ticks or connector-defined turns
```

Each visible object has an episode-local opaque `object_id`, public name or
label, relative or public world position, distance, and public properties. IDs
may change after reset. They cannot encode object purpose or private state.

`messages` contains only feedback a normal player could receive, such as an
interaction message, a visible state change, or a normal game event. The
connector must not generate strategic commentary.

Observations must be:

- JSON serializable and valid against the recorded schema version.
- Deterministically ordered before hashing.
- Captured after the game has settled for the action's declared settle period.
- Stored exactly as delivered to the model.
- Equivalent for all models in the same game and scenario condition.

Minecraft uses structured nearby state from the automation client. Doom uses
public status values and labels for objects currently visible. Neither result
may be described as pixels-only computer use.

## Primitive tool definitions

A tool definition contains `name`, plain-language `description`, JSON argument
schema, preconditions, maximum duration, and whether it can change game state.
Tool names describe ordinary controls, not solutions. For example,
`use_object(object_id)` is valid; `charge_keystone()` is not.

The minimum Minecraft BDP tool set is:

| Tool | Arguments | Meaning |
| --- | --- | --- |
| `observe` | `radius` from 1 to 8 | Refresh visible nearby state. |
| `move_to` | public `x`, `y`, `z`; tolerance 1 or 2 | Walk to a reachable position. |
| `look_at` | `object_id` | Face a visible object. |
| `inspect_object` | `object_id` | Return ordinary public name and state. |
| `collect_object` | `object_id`, count 1 to 8 | Collect a reachable item or resource. |
| `use_object` | `object_id`, optional held item ID | Perform the normal use/interact control. |
| `place_object` | held item ID and adjacent public position | Place a placeable held object. |
| `wait` | 1 to 100 ticks | Allow a visible process to advance. |

The minimum Doom BDP tool set is `observe`, `move_forward`, `move_backward`,
`strafe_left`, `strafe_right`, `turn_left`, `turn_right`, `attack`, `use`, and
`wait`. Movement and attack durations are 1 to 35 game ticks; turn amount is 1
to 90 degrees.

Game-specific tools are allowed because they represent different new-player
controls. The per-game manifest must remain identical across compared models.

## Tool request and step result

```python
class ToolRequest(BaseModel):
    action_id: str
    tool_name: str
    arguments: dict[str, object]

class StepResult(BaseModel):
    action_id: str
    sequence: int
    status: Literal["succeeded", "rejected", "failed", "unknown"]
    code: str
    message: str
    observation: Observation
    state_changed: bool | None
    primitive_actions_charged: int
    logical_duration: int
    wall_time_ms: int
```

`rejected` means the request was never sent because its name, arguments,
preconditions, or remaining budget were invalid. `failed` means it was sent and
the connector knows it did not complete. `unknown` means it may have changed the
game but the connector could not confirm the result. Unknown actions are never
retried automatically.

Every request receives one durable result with the same `action_id`. The harness
records intent before execution and records the result before another action is
allowed. One Action-agent decision may call one primitive or one learned skill.
A skill may call several primitives, and every nested primitive is recorded and
charged.

## Timing and concurrency

Only one state-changing primitive may be in flight per episode. Observations
cannot be requested concurrently with a state-changing action.

- Connector call timeout: 10 seconds for Minecraft; 3 seconds for Doom.
- Reset timeout: 30 seconds for Minecraft; 10 seconds for Doom.
- Minecraft settle period: 5 game ticks after a completed interaction.
- Doom advances only by the exact ticks requested by the tool; evaluation does
  not depend on machine frame rate.
- Timeout after confirmed non-delivery is `failed`. Timeout after possible
  delivery is `unknown` and ends the episode as an infrastructure ambiguity.

Wall time is measured with a monotonic host clock. Logical duration comes from
the game. Both are stored.

## Episode budgets

The default BDP budgets are frozen in the experiment manifest:

| Phase | Agent decisions | Primitive calls | Wall time |
| --- | ---: | ---: | ---: |
| Training attempt | 20 | 40 | 180 seconds |
| Held-out attempt | 12 | 24 | 90 seconds |
| Defect reproduction | 8 | 16 | 60 seconds |

An invalid or rejected model-selected tool consumes one agent decision but no
primitive call. A primitive that fails or becomes unknown consumes one primitive
call. `observe` and `wait` count as primitives. Nested calls made by a skill count
against the same held-out budget, so a skill cannot hide extra work.

The episode ends on verified goal completion, terminal game state, decision
limit, primitive limit, wall-time limit, an unknown action result, connector
loss, or three identical failed calls with no intervening public state change.

## Determinism, errors, and records

`reset(scenario_id, seed)` must restore the declared initial state. The harness
runs a reset smoke check before an evaluation campaign and stores a scenario
fingerprint. A mismatch aborts the campaign rather than silently mixing builds.

Standard result codes include `INVALID_TOOL`, `INVALID_ARGUMENT`,
`PRECONDITION_FAILED`, `UNREACHABLE`, `NO_VISIBLE_TARGET`, `GAME_REJECTED`,
`TIMEOUT_CONFIRMED`, `TIMEOUT_UNKNOWN`, `BUDGET_EXHAUSTED`, and
`CONNECTOR_LOST`. New codes require a connector version change.

Each episode stores the manifest, every exact observation, request, result,
timing value, and budget counter. Secrets and private grader state are never
placed in these records.

## Acceptance tests for a connector

A connector is ready when it passes schema validation, reset repeatability,
budget enforcement, action-ID idempotency, timeout classification, private-state
leak checks, and an end-to-end scripted scenario. The same scripted sequence on
the same seed must produce the same grader-relevant state, even if wall-clock
latency differs.
