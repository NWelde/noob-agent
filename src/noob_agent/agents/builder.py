"""The Builder agent: bounded public evidence in, one recorded candidate out.

This implements the slow improvement loop's authoring half (`hackathon_plan.md`
section 7.2, steps 4-8). The ordering matters more than it looks:

1. The candidate is recorded in the registry as `proposed` *before* it is
   validated, so a rejected attempt stays visible instead of disappearing.
2. Validation executes the candidate only through the configured isolated
   executor against public replay, negative-case, and variation fixtures.
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
from noob_agent.domain.records import StoredEpisode
from noob_agent.domain.skills import SkillPackage as RegistryPackage
from noob_agent.domain.skills import SkillVersion
from noob_agent.models.client import ModelClient, ModelRequest
from noob_agent.prompts.builder import (
    BUILDER_SYSTEM,
    DEFAULT_BUILD_MAX_OUTPUT_TOKENS,
    DEFAULT_REPAIR_MAX_OUTPUT_TOKENS,
    estimated_tokens,
    render_builder_prompt,
    render_repair_prompt,
)
from noob_agent.skills.errors import SkillValidationError, SkillValidationIssue
from noob_agent.skills.executor import SkillExecutor
from noob_agent.skills.registry import SkillRegistry
from noob_agent.verification.validator import SkillValidationReport, validate_candidate

DEFAULT_MAX_OUTPUT_TOKENS = DEFAULT_BUILD_MAX_OUTPUT_TOKENS

_PYTHON_BLOCK = re.compile(r"```python\s*\n(.*?)```", re.DOTALL)
_JSON_BLOCK = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)

BuilderStopReason = Literal[
    "accepted",
    "repair_budget_exhausted",
    "unusable_reply",
    # The reply ended at its output cap before a complete candidate.
    "truncated_reply",
    # The next call could exceed the sequence's learning token or call budget.
    "learning_budget_exhausted",
    # A refinement passed validation and awaits its caller's decision.
    "validated",
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
    # Model calls sent; zero when the learning budget was already spent.
    attempts: int = Field(ge=0)
    version: SkillVersion | None = None
    validation: SkillValidationReport | None = None
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
        executor: SkillExecutor,
        max_repairs: int = 1,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        thinking: bool | None = None,
        repair_max_output_tokens: int = DEFAULT_REPAIR_MAX_OUTPUT_TOKENS,
        learning_token_budget: int | None = None,
        learning_call_budget: int | None = None,
        validation_concurrency: int = 1,
    ) -> None:
        if max_repairs < 0:
            raise ValueError("max_repairs cannot be negative.")
        self._client = client
        self._registry = registry
        self._executor = executor
        self._max_repairs = max_repairs
        self._max_output_tokens = max_output_tokens
        self._thinking = thinking
        self._repair_max_output_tokens = repair_max_output_tokens
        self._learning_token_budget = learning_token_budget
        self._learning_call_budget = learning_call_budget
        self._validation_concurrency = validation_concurrency

    def _over_budget(self, request: ModelRequest, *, spent_tokens: int, spent_calls: int) -> bool:
        """Whether this call could push the sequence past its learning budget.

        The projection counts the whole output cap, so the budget can never be
        exceeded by a call that was allowed to start.
        """
        if self._learning_call_budget is not None and spent_calls + 1 > self._learning_call_budget:
            return True
        if self._learning_token_budget is None:
            return False
        projected = (
            spent_tokens
            + estimated_tokens(request.system)
            + estimated_tokens(request.prompt)
            + request.max_output_tokens
        )
        return projected > self._learning_token_budget

    async def build(
        self,
        evidence: TraceEvidence,
        *,
        training_trace: StoredEpisode,
        primitive_names: Iterable[str],
        authoring_model_id: str,
        created_at: datetime,
        spent_tokens: int = 0,
        spent_calls: int = 0,
    ) -> BuilderOutcome:
        """Author, record, and validate one candidate, repairing it while budget lasts.

        `spent_tokens` and `spent_calls` are the sequence's learning spend before
        the Builder starts, so the learning budget covers training and authoring.
        """
        names = tuple(primitive_names)
        tools = training_trace.episode.manifest.tools
        return await self._author(
            render_builder_prompt(evidence, primitive_names=names, tools=tools),
            evidence=evidence,
            training_trace=training_trace,
            names=names,
            authoring_model_id=authoring_model_id,
            created_at=created_at,
            spent_tokens=spent_tokens,
            spent_calls=spent_calls,
            parent_version=None,
            current_name=None,
            current_source=None,
            accept=True,
        )

    async def refine(
        self,
        incumbent: SkillVersion,
        *,
        prompt: str,
        evidence: TraceEvidence,
        training_trace: StoredEpisode,
        primitive_names: Iterable[str],
        authoring_model_id: str,
        created_at: datetime,
        spent_tokens: int = 0,
        spent_calls: int = 0,
    ) -> BuilderOutcome:
        """Write one refinement of an accepted skill and validate it, without accepting it.

        The refinement is a new version whose parent is the incumbent. It stops
        at `validated`, still `validating` in the registry, so a caller can
        compare it in practice before accepting or rejecting it. Repairs follow
        the same rules as a build, and the name may not change.
        """
        return await self._author(
            prompt,
            evidence=evidence,
            training_trace=training_trace,
            names=tuple(primitive_names),
            authoring_model_id=authoring_model_id,
            created_at=created_at,
            spent_tokens=spent_tokens,
            spent_calls=spent_calls,
            parent_version=incumbent.version,
            current_name=incumbent.name,
            current_source=incumbent.package.source,
            accept=False,
        )

    async def _author(
        self,
        prompt: str,
        *,
        evidence: TraceEvidence,
        training_trace: StoredEpisode,
        names: tuple[str, ...],
        authoring_model_id: str,
        created_at: datetime,
        spent_tokens: int,
        spent_calls: int,
        parent_version: int | None,
        current_name: str | None,
        current_source: str | None,
        accept: bool,
    ) -> BuilderOutcome:
        tools = training_trace.episode.manifest.tools
        usage: list[ModelUsage] = []
        attempts = 0

        while True:
            request = ModelRequest(
                system=BUILDER_SYSTEM,
                prompt=prompt,
                max_output_tokens=(
                    self._max_output_tokens if attempts == 0 else self._repair_max_output_tokens
                ),
                thinking=self._thinking,
            )
            used = sum(item.input_tokens + item.output_tokens for item in usage)
            if self._over_budget(
                request, spent_tokens=spent_tokens + used, spent_calls=spent_calls + attempts
            ):
                return BuilderOutcome(
                    accepted=False,
                    stop_reason="learning_budget_exhausted",
                    attempts=attempts,
                    usage=tuple(usage),
                )
            attempts += 1
            response = await self._client.complete(request)
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
                    stop_reason=(
                        "truncated_reply"
                        if response.finish_reason == "length"
                        else "unusable_reply"
                    ),
                    attempts=attempts,
                    usage=tuple(usage),
                )

            if current_name is not None and candidate.name != current_name:
                # A repair must extend the rejected version's lineage, so a renamed
                # reply is a public rejection that spends a repair, not a new skill.
                assert current_source is not None and parent_version is not None
                if len(usage) > self._max_repairs:
                    return BuilderOutcome(
                        accepted=False,
                        stop_reason="repair_budget_exhausted",
                        attempts=attempts,
                        usage=tuple(usage),
                    )
                prompt = render_repair_prompt(
                    previous_source=current_source,
                    issues=(
                        SkillValidationIssue(
                            check="package",
                            code="renamed_repair",
                            message=(
                                f"A repair must keep the skill name {current_name!r}; "
                                f"the reply named it {candidate.name!r}."
                            ),
                        ),
                    ),
                    evidence=evidence,
                    primitive_names=names,
                    tools=tools,
                    skill_name=current_name,
                )
                continue
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
                validation = await validate_candidate(
                    candidate.source,
                    candidate.metadata_json,
                    known_primitive_names=names,
                    training_trace=training_trace,
                    executor=self._executor,
                    concurrency=self._validation_concurrency,
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
                current_name = candidate.name
                current_source = candidate.source
                prompt = render_repair_prompt(
                    previous_source=candidate.source,
                    issues=issues,
                    evidence=evidence,
                    primitive_names=names,
                    tools=tools,
                    skill_name=candidate.name,
                )
                continue

            if not accept:
                return BuilderOutcome(
                    accepted=False,
                    stop_reason="validated",
                    attempts=attempts,
                    version=self._registry.get(candidate.name, recorded.version),
                    validation=validation,
                    usage=tuple(usage),
                )
            accepted = self._registry.accept(
                candidate.name,
                recorded.version,
                reason="All mandatory isolated validation checks passed.",
            )
            return BuilderOutcome(
                accepted=True,
                stop_reason="accepted",
                attempts=attempts,
                version=accepted,
                validation=validation,
                usage=tuple(usage),
            )
