"""Deterministic builders for durable-record tests.

Every value here is fixed. Nothing reads the clock, the environment, or a
network service, so a failing assertion always means a real behavior change.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest

from noob_agent.domain.model import (
    ConnectorManifest,
    Observation,
    PublicMessage,
    PublicPlayerState,
    PublicPosition,
    StepResult,
    ToolDefinition,
    ToolRequest,
    VisibleObject,
)
from noob_agent.domain.records import (
    EpisodeOutcome,
    EpisodeRecord,
    ExperimentRecord,
    StepRecord,
)
from noob_agent.storage import EpisodeStore

STARTED_AT = datetime(2026, 9, 12, 15, 0, 0, tzinfo=UTC)
FINISHED_AT = datetime(2026, 9, 12, 15, 4, 30, tzinfo=UTC)

EXPERIMENT_ID = "exp_0001"
EPISODE_ID = "ep_0001"
GAME_ID = "minecraft"
SCENARIO_ID = "mc_signal_post_a"


@pytest.fixture
def manifest() -> ConnectorManifest:
    return ConnectorManifest(
        connector_version="fake-minecraft-0.1.0",
        game_id=GAME_ID,
        observation_mode="structured_nearby_state",
        timing_model="game_ticks",
        tools=(
            ToolDefinition(
                name="observe",
                description="Refresh the visible nearby state within a radius.",
                argument_schema={"type": "object"},
                max_duration=10,
                state_changing=False,
            ),
            ToolDefinition(
                name="use_object",
                description="Perform the normal use control on a visible object.",
                argument_schema={"type": "object"},
                preconditions=("The object is reachable.",),
                max_duration=30,
                state_changing=True,
            ),
        ),
    )


@pytest.fixture
def observation_factory() -> Callable[..., Observation]:
    def build(
        sequence: int,
        *,
        episode_id: str = EPISODE_ID,
        last_action_id: str | None = None,
        terminal: bool = False,
        terminal_reason: str | None = None,
        logical_time: int = 0,
    ) -> Observation:
        return Observation(
            episode_id=episode_id,
            sequence=sequence,
            game_id=GAME_ID,
            scenario_id=SCENARIO_ID,
            public_goal="Light the lantern on the post.",
            status={"health": 20, "on_ground": True},
            player=PublicPlayerState(
                position=PublicPosition(x=4.0, y=64.0, z=1.0),
                orientation={"pitch": 0.0, "yaw": 0.0},
                properties={"held_item_id": "item_1"},
            ),
            visible_objects=(
                VisibleObject(
                    object_id="obj_1",
                    label="lantern",
                    position=PublicPosition(x=12.0, y=65.0, z=-4.0),
                    distance=9.4,
                    properties={"lit": False},
                ),
            ),
            messages=(PublicMessage(kind="observation", text="Nearby state refreshed."),),
            last_action_id=last_action_id,
            terminal=terminal,
            terminal_reason=terminal_reason,
            logical_time=logical_time,
        )

    return build


@pytest.fixture
def experiment() -> ExperimentRecord:
    return ExperimentRecord(
        experiment_id=EXPERIMENT_ID,
        model_id="fake-model-a",
        condition="cold",
        connector_version="fake-minecraft-0.1.0",
        decision_budget=20,
        primitive_budget=40,
        wall_time_budget_ms=180_000,
        created_at=STARTED_AT,
    )


@pytest.fixture
def episode(
    manifest: ConnectorManifest, observation_factory: Callable[..., Observation]
) -> EpisodeRecord:
    return EpisodeRecord(
        episode_id=EPISODE_ID,
        experiment_id=EXPERIMENT_ID,
        game_id=GAME_ID,
        scenario_id=SCENARIO_ID,
        seed=7,
        split="training",
        manifest=manifest,
        reset_observation=observation_factory(0),
        started_at=STARTED_AT,
    )


@pytest.fixture
def step_factory(observation_factory: Callable[..., Observation]) -> Callable[..., StepRecord]:
    def build(
        sequence: int,
        *,
        episode_id: str = EPISODE_ID,
        action_id: str | None = None,
        tool_name: str = "observe",
        status: Literal["succeeded", "rejected", "failed", "unknown"] = "succeeded",
        code: str = "OK",
        terminal: bool = False,
        terminal_reason: str | None = None,
    ) -> StepRecord:
        resolved_action_id = action_id if action_id is not None else f"a_{sequence:04d}"
        return StepRecord(
            episode_id=episode_id,
            sequence=sequence,
            request=ToolRequest(
                action_id=resolved_action_id,
                tool_name=tool_name,
                arguments={"radius": 8},
            ),
            result=StepResult(
                action_id=resolved_action_id,
                sequence=sequence,
                status=status,
                code=code,
                message="Visible nearby state refreshed.",
                observation=observation_factory(
                    sequence,
                    episode_id=episode_id,
                    last_action_id=resolved_action_id,
                    terminal=terminal,
                    terminal_reason=terminal_reason,
                ),
                state_changed=False,
                primitive_actions_charged=1,
                logical_duration=0,
                wall_time_ms=38,
            ),
        )

    return build


@pytest.fixture
def outcome() -> EpisodeOutcome:
    return EpisodeOutcome(
        episode_id=EPISODE_ID,
        stop_reason="goal_completed",
        terminal=True,
        total_decisions=3,
        total_primitives=3,
        finished_at=FINISHED_AT,
    )


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    return tmp_path / "runs.db"


@pytest.fixture
def store(database_path: Path) -> Iterator[EpisodeStore]:
    with EpisodeStore.open(database_path) as opened:
        yield opened
