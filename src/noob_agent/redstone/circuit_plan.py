"""Optional planner-authored circuit notes, persisted separately from feedback.

Plans describe intended hardware only. They are never consulted by the grader.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from noob_agent.redstone.contract import FrozenModel


class PlannedInput(FrozenModel):
    id: str = Field(min_length=1, max_length=80)
    role: str = Field(min_length=1, max_length=40)
    position: list[int] = Field(min_length=3, max_length=3)
    encoding: str = Field(default="", max_length=160)


class PlannedStorageNode(FrozenModel):
    id: str = Field(min_length=1, max_length=80)
    purpose: str = Field(min_length=1, max_length=160)
    position: list[int] = Field(min_length=3, max_length=3)


class PlannedSignalPath(FrozenModel):
    id: str = Field(min_length=1, max_length=80)
    source: str = Field(min_length=1, max_length=80)
    target: str = Field(min_length=1, max_length=80)
    positions: list[list[int]] = Field(default_factory=list, max_length=96)
    expected_polarity: Literal["active_high", "active_low", "unknown"] = "unknown"


class PlannedConnection(FrozenModel):
    source: str = Field(min_length=1, max_length=80)
    target: str = Field(min_length=1, max_length=80)
    reason: str = Field(default="", max_length=240)


class CircuitPlan(FrozenModel):
    """A compact design memory written by the planner, never ground truth."""

    module: str = Field(default="", max_length=40)
    inputs: list[PlannedInput] = Field(default_factory=list, max_length=32)
    storage_nodes: list[PlannedStorageNode] = Field(default_factory=list, max_length=64)
    signal_paths: list[PlannedSignalPath] = Field(default_factory=list, max_length=64)
    unfinished_connections: list[PlannedConnection] = Field(default_factory=list, max_length=64)
    notes: list[str] = Field(default_factory=list, max_length=16)
