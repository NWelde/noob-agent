"""The Builder agent: bounded public evidence in, one recorded candidate out.

This implements the slow improvement loop's authoring half (`hackathon_plan.md`
section 7.2, steps 4-8). The ordering matters more than it looks:

1. The candidate is recorded in the registry as `proposed` *before* it is
   validated, so a rejected attempt stays visible instead of disappearing.
2. Validation is the existing non-executing package and static-policy check.
   Nothing here imports, compiles, or runs candidate source.
3. A rejection produces a *new* version carrying `parent_version`, never an
   edit of the rejected record, and the repair budget is small and finite.

The Builder receives only public validation errors on a repair. Private grader
predicates and held-out data never reach it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from noob_agent.agents.evidence import TraceEvidence
from noob_agent.domain.skills import SkillPackage as RegistryPackage
from noob_agent.domain.skills import SkillVersion
from noob_agent.models.client import ModelClient, ModelRequest
from noob_agent.prompts.builder import (
    BUILDER_SYSTEM,
    render_builder_prompt,
    render_repair_prompt,
)
from noob_agent.skills.errors import SkillValidationError, SkillValidationIssue
from noob_agent.skills.package import validate_skill_package
from noob_agent.skills.registry import SkillRegistry

DEFAULT_MAX_OUTPUT_TOKENS = 2048

_PYTHON_BLOCK = re.compile(r"```python\s*\n(.*?)```", re.DOTALL)
_JSON_BLOCK = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)

BuilderStopReason = Literal[
    "accepted",
    "repair_budget_exhausted",
    "unusable_reply",
]


class BuilderError(RuntimeError):
    """Base class for Builder failures that are not ordinary rejections."""


class UnusableReplyError(BuilderError):
    """The model's reply does not contain a candidate this harness can record."""


class ModelUsage(BaseModel):
    """What one authoring attempt cost, for the Model call record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: str = Field(min_length=1)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class BuilderOutcome(BaseModel):
    """The result of one authoring run, accepted or not."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: bool
    stop_reason: BuilderStopReason
    attempts: int = Field(ge=1)
    version: SkillVersion | None = None
    usage: tuple[ModelUsage, ...] = ()


class _Candidate(BaseModel):
    """A parsed reply, before anything has judged whether it is any good."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    source: str = Field(min_length=1)
    metadata_json: str = Field(min_length=1)
    metadata: dict[str, JsonValue]


def parse_candidate(reply: str) -> _Candidate:
    """Pull the source and metadata blocks out of one model reply."""
    source_match = _PYTHON_BLOCK.search(reply)
    metadata_match = _JSON_BLOCK.search(reply)
    if source_match is None or metadata_match is None:
        raise UnusableReplyError("The reply is missing a python or json block.")

    metadata_json = metadata_match.group(1).strip()
    try:
        metadata = json.loads(metadata_json)
    except json.JSONDecodeError as error:
        raise UnusableReplyError(f"The metadata block is not valid JSON: {error}") from error
    if not isinstance(metadata, dict):
        raise UnusableReplyError("The metadata block must be a JSON object.")

    name = metadata.get("name")
    if not isinstance(name, str) or not name:
        raise UnusableReplyError("The metadata block declares no skill name.")

    return _Candidate(
        name=name,
        source=source_match.group(1),
        metadata_json=metadata_json,
        metadata=metadata,
    )


def _issue_summary(issues: Sequence[SkillValidationIssue]) -> str:
    return "; ".join(f"{issue.check}/{issue.code}: {issue.message}" for issue in issues)


class BuilderAgent:
    """Writes one skill candidate from a bounded public trace, and repairs it once."""

    def __init__(
        self,
        client: ModelClient,
        registry: SkillRegistry,
        *,
        max_repairs: int = 1,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    ) -> None:
        if max_repairs < 0:
            raise ValueError("max_repairs cannot be negative.")
        self._client = client
        self._registry = registry
        self._max_repairs = max_repairs
        self._max_output_tokens = max_output_tokens

    async def build(
        self,
        evidence: TraceEvidence,
        *,
        primitive_names: Iterable[str],
        authoring_model_id: str,
        created_at: datetime,
    ) -> BuilderOutcome:
        """Author, record, and validate one candidate, repairing it while budget lasts."""
        names = tuple(primitive_names)
        prompt = render_builder_prompt(evidence, primitive_names=names)
        usage: list[ModelUsage] = []
        parent_version: int | None = None
        attempts = 0

        while True:
            attempts += 1
            response = await self._client.complete(
                ModelRequest(
                    system=BUILDER_SYSTEM,
                    prompt=prompt,
                    max_output_tokens=self._max_output_tokens,
                )
            )
            usage.append(
                ModelUsage(
                    model_id=response.model_id,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                )
            )

            try:
                candidate = parse_candidate(response.text)
            except UnusableReplyError:
                return BuilderOutcome(
                    accepted=False,
                    stop_reason="unusable_reply",
                    attempts=attempts,
                    usage=tuple(usage),
                )

            # Recorded before validation: a rejected attempt must stay visible.
            recorded = self._registry.propose(
                RegistryPackage(
                    name=candidate.name,
                    source=candidate.source,
                    metadata=candidate.metadata,
                ),
                authoring_episode_id=evidence.episode_id,
                authoring_model_id=authoring_model_id,
                created_at=created_at,
                parent_version=parent_version,
                reason="Candidate written by the Builder.",
            )
            self._registry.begin_validation(
                candidate.name, recorded.version, reason="Package and static policy check."
            )

            try:
                validate_skill_package(
                    candidate.source,
                    candidate.metadata_json,
                    known_primitive_names=names,
                )
            except SkillValidationError as error:
                issues = tuple(error.issues)
                self._registry.reject(
                    candidate.name, recorded.version, reason=_issue_summary(issues)
                )
                if len(usage) > self._max_repairs:
                    return BuilderOutcome(
                        accepted=False,
                        stop_reason="repair_budget_exhausted",
                        attempts=attempts,
                        usage=tuple(usage),
                    )
                parent_version = recorded.version
                prompt = render_repair_prompt(previous_source=candidate.source, issues=issues)
                continue

            accepted = self._registry.accept(
                candidate.name,
                recorded.version,
                reason="Package check and static policy check passed.",
            )
            return BuilderOutcome(
                accepted=True,
                stop_reason="accepted",
                attempts=attempts,
                version=accepted,
                usage=tuple(usage),
            )
