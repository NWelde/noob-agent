"""Immutable records for generated skill candidates and their registry versions.

A candidate's content — source, metadata, parent, and content hash — is fixed
the moment it is recorded. Improving a skill adds a new version; it never edits
an existing one, which is what keeps the experiment's improvement visible and
reversible.

These records carry no private state: no grader predicate, no held-out
configuration, no scenario answer. Like the public connector models they forbid
undeclared fields, so such a value cannot arrive through an extra key.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

# The registry states named in skill_contract.md.
SkillStatus = Literal["proposed", "validating", "accepted", "rejected", "retired"]


class SkillRecord(BaseModel):
    """Immutable, JSON-safe base for every recorded skill fact."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SkillPackage(SkillRecord):
    """One submitted candidate: its source and the metadata declared beside it.

    `metadata` stays an opaque JSON object here. Typed `noob-agent.skill.v1`
    metadata models, package checks, and static policy checks belong to the
    validation boundary that decides whether a candidate is safe. The registry
    records what it was handed and does not interpret it.
    """

    name: str = Field(min_length=1)
    source: str = Field(min_length=1)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    api_version: str = Field(default="noob-agent.skill.v1", min_length=1)


class SkillVersion(SkillRecord):
    """One immutable registry entry for a candidate skill.

    The registry, not the Builder, assigns `version` and `content_hash`. A
    repair points `parent_version` at the rejected version it was written from,
    so a skill's whole lineage stays readable after the fact.
    """

    name: str = Field(min_length=1)
    version: int = Field(ge=1)
    content_hash: str = Field(min_length=1)
    package: SkillPackage
    parent_version: int | None = Field(default=None, ge=1)
    authoring_episode_id: str = Field(min_length=1)
    authoring_model_id: str = Field(min_length=1)
    status: SkillStatus
    status_reason: str
    created_at: datetime

    @model_validator(mode="after")
    def version_describes_its_own_package(self) -> Self:
        """A version must name the package it records and follow its parent."""
        if self.package.name != self.name:
            raise ValueError("The recorded package belongs to a different skill.")
        if self.parent_version is not None and self.parent_version >= self.version:
            raise ValueError("A parent version must precede the version repairing it.")
        return self
