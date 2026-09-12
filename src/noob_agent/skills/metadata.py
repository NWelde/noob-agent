"""Typed `skill.json` metadata for the `noob-agent.skill.v1` package contract.

See skill_contract.md's "Metadata" section for the field definitions this
mirrors.
"""

from __future__ import annotations

import re
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

API_VERSION = "noob-agent.skill.v1"
MAX_INPUT_FIELDS = 8

_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class SkillMetadata(BaseModel):
    """One candidate's declared identity, purpose, inputs, and limits."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    version: int = Field(ge=1)
    parent_version: int | None = Field(default=None, ge=1)
    purpose: str = Field(min_length=1)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    required_tools: tuple[str, ...] = Field(min_length=1)
    max_primitive_actions: int = Field(gt=0)
    max_wall_time_seconds: float = Field(gt=0)
    success_claim: str = Field(min_length=1)
    api_version: str

    @field_validator("name")
    @classmethod
    def name_is_lower_snake_case(cls, value: str) -> str:
        if not _NAME_PATTERN.fullmatch(value):
            raise ValueError(
                "name must start with a lowercase letter and contain only "
                "lowercase letters, digits, and underscores."
            )
        return value

    @field_validator("api_version")
    @classmethod
    def api_version_is_supported(cls, value: str) -> str:
        if value != API_VERSION:
            raise ValueError(f"api_version must be {API_VERSION!r}, got {value!r}.")
        return value

    @field_validator("required_tools")
    @classmethod
    def required_tools_are_named(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not tool for tool in value):
            raise ValueError("required_tools cannot contain an empty tool name.")
        return value

    @model_validator(mode="after")
    def input_schema_within_field_limit(self) -> Self:
        properties = self.input_schema.get("properties", {})
        if not isinstance(properties, dict):
            raise ValueError("input_schema.properties must be an object.")
        if len(properties) > MAX_INPUT_FIELDS:
            raise ValueError(
                f"input_schema declares {len(properties)} fields, more than the "
                f"maximum of {MAX_INPUT_FIELDS}."
            )
        return self
