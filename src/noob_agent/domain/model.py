"""Public data exchanged between the evaluation harness and a game connector.

These models deliberately contain only information a new player may receive or
request. Connector implementations, the harness, and the private grader own
all behavior and private state.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class PublicModel(BaseModel):
    """Immutable, JSON-safe base for recorded public connector data."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class PublicPosition(PublicModel):
    """A connector-defined public or relative three-dimensional position."""

    x: float
    y: float
    z: float


class PublicPlayerState(PublicModel):
    """Public player data that may be supplied by a game connector."""

    position: PublicPosition | None = None
    orientation: dict[str, JsonValue] = Field(default_factory=dict)
    properties: dict[str, JsonValue] = Field(default_factory=dict)


class VisibleObject(PublicModel):
    """An episode-local object visible to the player."""

    object_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    position: PublicPosition | None = None
    distance: float | None = Field(default=None, ge=0)
    properties: dict[str, JsonValue] = Field(default_factory=dict)


class PublicMessage(PublicModel):
    """Ordinary, non-strategic feedback available to the player."""

    kind: str = Field(min_length=1)
    text: str = Field(min_length=1)


class Observation(PublicModel):
    """The complete public snapshot for one agent decision."""

    episode_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    game_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    public_goal: str = Field(min_length=1)
    status: dict[str, JsonValue] = Field(default_factory=dict)
    player: PublicPlayerState
    visible_objects: tuple[VisibleObject, ...] = ()
    messages: tuple[PublicMessage, ...] = ()
    last_action_id: str | None = None
    terminal: bool
    terminal_reason: str | None = None
    logical_time: int = Field(ge=0)


class ToolDefinition(PublicModel):
    """One frozen primitive control exposed by a connector manifest."""

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    argument_schema: dict[str, JsonValue]
    preconditions: tuple[str, ...] = ()
    max_duration: int = Field(gt=0)
    state_changing: bool


class ConnectorManifest(PublicModel):
    """The immutable public interface for one connector version and episode."""

    schema_version: str = "noob-agent.connector.v1"
    connector_version: str = Field(min_length=1)
    game_id: str = Field(min_length=1)
    observation_mode: str = Field(min_length=1)
    timing_model: str = Field(min_length=1)
    tools: tuple[ToolDefinition, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def tool_names_are_unique(self) -> Self:
        """Prevent an ambiguous primitive-tool manifest."""
        names = [tool.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("Tool definition names must be unique.")
        return self


class ToolRequest(PublicModel):
    """One durable request from the agent or an invoked skill."""

    action_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class StepResult(PublicModel):
    """The durable public result of exactly one primitive-tool request."""

    action_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    status: Literal["succeeded", "rejected", "failed", "unknown"]
    code: str = Field(min_length=1)
    message: str
    observation: Observation
    state_changed: bool | None
    primitive_actions_charged: int = Field(ge=0)
    logical_duration: int = Field(ge=0)
    wall_time_ms: int = Field(ge=0)
