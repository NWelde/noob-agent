"""Validate generated skills by executing only controlled public fixtures.

The executable pipeline deliberately has no connector factory, scenario loader,
grader, or held-out inputs.  It replays one durable training trace through the
configured isolated executor, generates contract-negative cases from that same
public data, and creates one deterministic object-ID variation locally.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Collection
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, JsonValue

from noob_agent.domain.model import Observation, StepResult, ToolDefinition
from noob_agent.domain.records import StepRecord, StoredEpisode
from noob_agent.domain.skills import SkillPackage as RegistrySkillPackage
from noob_agent.skills.contract import SkillBudget, SkillResult
from noob_agent.skills.errors import SkillValidationError, SkillValidationIssue
from noob_agent.skills.executor import SkillExecution, SkillExecutor, SkillHost, SkillLimits
from noob_agent.skills.metadata import SkillMetadata
from noob_agent.skills.package import validate_skill_package

MANDATORY_CHECKS = (
    "package",
    "static_policy",
    "load",
    "contract",
    "training_replay",
    "negative_cases",
    "repeatability",
    "validation_variation",
)


class SkillValidationReport(BaseModel):
    """Public acceptance evidence for one immutable candidate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: Literal[True] = True
    checks: tuple[str, ...] = MANDATORY_CHECKS
    repeatability_runs: int = 3
    variation_object_ids: tuple[str, ...] = ()


class _ReplayHost(SkillHost):
    """A fake connector capability backed only by recorded public results."""

    def __init__(
        self,
        *,
        observation: Observation,
        responses: tuple[StepRecord, ...],
        manifest_tools: dict[str, ToolDefinition],
        declared_tools: frozenset[str],
        primitive_limit: int,
        forced_status: Literal["failed", "unknown"] | None = None,
        targets_missing: bool = False,
    ) -> None:
        self.observation = observation
        self._responses = responses
        self._manifest_tools = manifest_tools
        self._declared_tools = declared_tools
        self._primitive_limit = primitive_limit
        self._forced_status = forced_status
        self._targets_missing = targets_missing
        self.calls: list[tuple[str, dict[str, JsonValue]]] = []
        self.logs: list[dict[str, JsonValue]] = []
        self.charged = 0
        self.replay_misses: list[str] = []
        self.contract_violations: list[str] = []

    async def observe(self) -> Observation:
        return self.observation

    async def call(self, tool_name: str, arguments: dict[str, JsonValue]) -> StepResult:
        self.calls.append((tool_name, arguments))
        sequence = self.observation.sequence + 1
        action_id = f"validation.{len(self.calls)}"
        definition = self._manifest_tools.get(tool_name)
        if definition is None:
            self.contract_violations.append(f"{tool_name!r} is not in the connector manifest")
            return self._refused(action_id, "INVALID_TOOL")
        if tool_name not in self._declared_tools:
            self.contract_violations.append(f"{tool_name!r} is not declared in required_tools")
            return self._refused(action_id, "UNDECLARED_TOOL")
        argument_issue = _schema_issue(arguments, definition.argument_schema)
        if argument_issue is not None:
            self.contract_violations.append(
                f"Arguments for {tool_name!r} violate its manifest schema: {argument_issue}"
            )
            return self._refused(action_id, "INVALID_ARGUMENT")
        if self.charged >= self._primitive_limit:
            return self._refused(action_id, "BUDGET_EXHAUSTED")

        recorded = next(
            (step for step in self._responses if step.request.tool_name == tool_name), None
        )
        if recorded is None:
            self.replay_misses.append(tool_name)
            return self._refused(action_id, "REPLAY_MISS", status="failed", charged=1)

        recorded_result = recorded.result
        status = self._forced_status or recorded_result.status
        code = (
            "VALIDATION_FORCED_FAILED"
            if status == "failed" and self._forced_status
            else "VALIDATION_FORCED_UNKNOWN"
            if status == "unknown" and self._forced_status
            else recorded_result.code
        )
        observation = recorded_result.observation.model_copy(
            update={
                "sequence": sequence,
                "last_action_id": action_id,
                "visible_objects": ()
                if self._targets_missing
                else recorded_result.observation.visible_objects,
            }
        )
        result = recorded_result.model_copy(
            update={
                "action_id": action_id,
                "sequence": sequence,
                "status": status,
                "code": code,
                "observation": observation,
                "state_changed": None if status == "unknown" else recorded_result.state_changed,
                "primitive_actions_charged": 1,
            }
        )
        self.charged += 1
        self.observation = observation
        return result

    def remaining_budget(self) -> SkillBudget:
        return SkillBudget(
            primitive_actions=max(self._primitive_limit - self.charged, 0),
            wall_time_seconds=5,
        )

    def log(self, event: str, fields: dict[str, JsonValue]) -> None:
        self.logs.append({"event": event, "fields": fields})

    def _refused(
        self,
        action_id: str,
        code: str,
        *,
        status: Literal["rejected", "failed"] = "rejected",
        charged: int = 0,
    ) -> StepResult:
        if charged:
            self.charged += charged
        return StepResult(
            action_id=action_id,
            sequence=self.observation.sequence,
            status=status,
            code=code,
            message=code.replace("_", " ").title(),
            observation=self.observation,
            state_changed=False,
            primitive_actions_charged=charged,
            logical_duration=0,
            wall_time_ms=0,
        )


class _Run:
    def __init__(self, execution: SkillExecution, host: _ReplayHost) -> None:
        self.execution = execution
        self.host = host

    @property
    def signature(self) -> tuple[object, ...]:
        result_status = None if self.execution.result is None else self.execution.result.status
        calls = tuple((name, json.dumps(args, sort_keys=True)) for name, args in self.host.calls)
        return self.execution.status, self.execution.code, result_status, calls


def validate_for_registry(
    source: str,
    metadata_json: str,
    *,
    known_primitive_names: Collection[str],
) -> RegistrySkillPackage:
    """Validate source and metadata before constructing a registry candidate.

    This function does not import or execute candidate source. It applies the
    existing package and AST policy checks, then converts their typed metadata
    into the immutable JSON representation owned by the registry.
    """
    validated = validate_skill_package(
        source,
        metadata_json,
        known_primitive_names=known_primitive_names,
    )
    metadata = validated.metadata.model_dump(mode="json")
    return RegistrySkillPackage(
        name=validated.metadata.name,
        source=validated.source,
        metadata=metadata,
        api_version=validated.metadata.api_version,
    )


async def validate_candidate(
    source: str,
    metadata_json: str,
    *,
    known_primitive_names: Collection[str],
    training_trace: StoredEpisode,
    executor: SkillExecutor,
    concurrency: int = 1,
) -> SkillValidationReport:
    """Run every mandatory validation stage and return public pass evidence.

    A failure raises ``SkillValidationError`` with only candidate-owned data and
    public fixture evidence.  Validation reports the first failing stage so a
    bounded repair gets one concrete hypothesis to address.

    With `concurrency` above 1, every execution starts up front, at most that
    many at a time, and the checks are then applied in the same stage order, so
    the verdict and the first reported failure never depend on concurrency.
    """
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1.")
    package = validate_skill_package(
        source, metadata_json, known_primitive_names=known_primitive_names
    )
    metadata = package.metadata
    manifest = training_trace.episode.manifest
    manifest_tools = {tool.name: tool for tool in manifest.tools}
    manifest_names = frozenset(manifest_tools)
    unknown_declarations = sorted(set(metadata.required_tools) - manifest_names)
    if unknown_declarations:
        raise SkillValidationError(
            (
                SkillValidationIssue(
                    check="contract",
                    code="unknown_required_tool",
                    message=(
                        "required_tools names primitives absent from the public manifest: "
                        f"{unknown_declarations!r}."
                    ),
                    fixture="training_replay",
                    public_inputs={},
                    public_result={"required_tools": list(metadata.required_tools)},
                ),
            )
        )
    responses = training_trace.steps
    inputs = _fixture_inputs(metadata, training_trace)

    gate = asyncio.Semaphore(concurrency)

    async def bounded(**options: Any) -> _Run:
        async with gate:
            return await _run(source, metadata, executor, manifest_tools=manifest_tools, **options)

    def three(**options: Any) -> list[Awaitable[_Run]]:
        return [bounded(**options) for _ in range(3)]

    negative_specs: tuple[
        tuple[
            str,
            Observation,
            dict[str, JsonValue],
            Literal["failed", "unknown"] | None,
            int,
            bool,
        ],
        ...,
    ] = (
        (
            "missing_target",
            _without_targets(training_trace.episode.reset_observation),
            inputs,
            None,
            metadata.max_primitive_actions,
            True,
        ),
        (
            "failed_primitive",
            training_trace.episode.reset_observation,
            inputs,
            "failed",
            metadata.max_primitive_actions,
            False,
        ),
        (
            "unknown_primitive",
            training_trace.episode.reset_observation,
            inputs,
            "unknown",
            metadata.max_primitive_actions,
            False,
        ),
        (
            "invalid_input",
            training_trace.episode.reset_observation,
            _invalid_inputs(metadata, inputs),
            None,
            metadata.max_primitive_actions,
            False,
        ),
        (
            "exhausted_budget",
            training_trace.episode.reset_observation,
            inputs,
            None,
            0,
            False,
        ),
    )
    variation_observation, variation_responses, variation_inputs, ids = _variation(
        training_trace.episode.reset_observation, responses, inputs
    )
    # Three phases, each run concurrently and checked in stage order; a failing
    # phase stops validation before the next one spends any executions.
    base_runs = await asyncio.gather(
        *three(
            observation=training_trace.episode.reset_observation,
            responses=responses,
            inputs=inputs,
        )
    )
    _require_completed(base_runs[0], check="load", fixture="training_replay", inputs=inputs)
    _require_contract(base_runs[0], fixture="training_replay", inputs=inputs)
    _require_replay(base_runs[0], inputs=inputs)
    _require_repeatable(base_runs, fixture="training_replay", inputs=inputs)

    negative_runs = await asyncio.gather(
        *(
            run
            for _, observation, case_inputs, forced, limit, targets_missing in negative_specs
            for run in three(
                observation=observation,
                responses=responses,
                inputs=case_inputs,
                forced_status=forced,
                primitive_limit=limit,
                targets_missing=targets_missing,
            )
        )
    )
    for index, (fixture, _, case_inputs, _, _, _) in enumerate(negative_specs):
        case_runs = negative_runs[3 * index : 3 * index + 3]
        _require_completed(case_runs[0], check="negative_case", fixture=fixture, inputs=case_inputs)
        _require_negative(case_runs[0], fixture=fixture, inputs=case_inputs)
        _require_repeatable(case_runs, fixture=fixture, inputs=case_inputs)

    variation_runs = await asyncio.gather(
        *three(
            observation=variation_observation,
            responses=variation_responses,
            inputs=variation_inputs,
        )
    )
    _require_completed(
        variation_runs[0],
        check="validation_variation",
        fixture="object_id_variation",
        inputs=variation_inputs,
    )
    _require_contract(variation_runs[0], fixture="object_id_variation", inputs=variation_inputs)
    _require_replay(variation_runs[0], inputs=variation_inputs, check="validation_variation")
    _require_repeatable(variation_runs, fixture="object_id_variation", inputs=variation_inputs)
    return SkillValidationReport(variation_object_ids=ids)


async def _run(
    source: str,
    metadata: SkillMetadata,
    executor: SkillExecutor,
    *,
    observation: Observation,
    responses: tuple[StepRecord, ...],
    manifest_tools: dict[str, ToolDefinition],
    inputs: dict[str, JsonValue],
    forced_status: Literal["failed", "unknown"] | None = None,
    primitive_limit: int | None = None,
    targets_missing: bool = False,
) -> _Run:
    limit = metadata.max_primitive_actions if primitive_limit is None else primitive_limit
    host = _ReplayHost(
        observation=observation,
        responses=responses,
        manifest_tools=manifest_tools,
        declared_tools=frozenset(metadata.required_tools),
        primitive_limit=limit,
        forced_status=forced_status,
        targets_missing=targets_missing,
    )
    execution = await executor.run(
        source=source,
        inputs=inputs,
        host=host,
        limits=SkillLimits(
            primitive_actions=limit,
            wall_time_seconds=metadata.max_wall_time_seconds,
        ),
    )
    return _Run(execution, host)


def _issue(
    run: _Run,
    *,
    check: Literal[
        "load",
        "contract",
        "training_replay",
        "negative_case",
        "repeatability",
        "validation_variation",
    ],
    code: str,
    message: str,
    fixture: str,
    inputs: dict[str, JsonValue],
) -> SkillValidationError:
    execution = run.execution
    result: dict[str, JsonValue] = (
        {"execution_status": execution.status, "code": execution.code, "message": execution.message}
        if execution.result is None
        else cast(dict[str, JsonValue], execution.result.model_dump(mode="json"))
    )
    return SkillValidationError(
        (
            SkillValidationIssue(
                check=check,
                code=code,
                message=message,
                fixture=fixture,
                public_inputs=inputs,
                public_result=result,
                public_logs=tuple(run.host.logs),
            ),
        )
    )


def _require_completed(
    run: _Run,
    *,
    check: Literal["load", "negative_case", "validation_variation"],
    fixture: str,
    inputs: dict[str, JsonValue],
) -> None:
    if run.execution.status != "completed" or run.execution.result is None:
        if check == "load" and run.execution.code != "SKILL_LOAD_FAILED":
            raise _issue(
                run,
                check="contract",
                code=run.execution.code,
                message=f"{fixture} violated the runtime contract: {run.execution.message}",
                fixture=fixture,
                inputs=inputs,
            )
        raise _issue(
            run,
            check=check,
            code=run.execution.code,
            message=f"{fixture} could not execute the candidate: {run.execution.message}",
            fixture=fixture,
            inputs=inputs,
        )


def _require_contract(run: _Run, *, fixture: str, inputs: dict[str, JsonValue]) -> None:
    if run.host.contract_violations:
        raise _issue(
            run,
            check="contract",
            code="tool_contract_violation",
            message="; ".join(run.host.contract_violations),
            fixture=fixture,
            inputs=inputs,
        )
    result = run.execution.result
    assert result is not None
    if result.primitive_actions_used != run.host.charged:
        raise _issue(
            run,
            check="contract",
            code="incorrect_action_accounting",
            message=(
                f"SkillResult reports {result.primitive_actions_used} primitive actions; "
                f"the fake connector recorded {run.host.charged}."
            ),
            fixture=fixture,
            inputs=inputs,
        )
    if result.status == "succeeded" and not _evidence_resolves(result, run.host):
        raise _issue(
            run,
            check="contract",
            code="unresolved_success_evidence",
            message="The success evidence does not resolve in this public fixture.",
            fixture=fixture,
            inputs=inputs,
        )


def _require_replay(
    run: _Run,
    *,
    inputs: dict[str, JsonValue],
    check: Literal["training_replay", "validation_variation"] = "training_replay",
) -> None:
    if run.host.replay_misses:
        raise _issue(
            run,
            check=check,
            code="trace_replay_miss",
            message=f"No recorded public response was available for {run.host.replay_misses!r}.",
            fixture="training_replay" if check == "training_replay" else "object_id_variation",
            inputs=inputs,
        )


def _require_repeatable(runs: list[_Run], *, fixture: str, inputs: dict[str, JsonValue]) -> None:
    if any(run.signature != runs[0].signature for run in runs[1:]):
        raise _issue(
            runs[0],
            check="repeatability",
            code="non_repeatable",
            message=(
                f"{fixture} did not return the same status and primitive call sequence 3 times."
            ),
            fixture=fixture,
            inputs=inputs,
        )


def _require_negative(run: _Run, *, fixture: str, inputs: dict[str, JsonValue]) -> None:
    result = run.execution.result
    assert result is not None
    primitive_case = fixture in {"failed_primitive", "unknown_primitive", "exhausted_budget"}
    invalid_case_applies = "__unexpected_validation_input" in inputs
    # With an empty open input schema there is no invalid input, so the
    # invalid-input fixture reuses valid inputs and a success there is legitimate.
    vacuous_input_case = fixture == "invalid_input" and not invalid_case_applies
    if (
        result.status == "succeeded"
        and not vacuous_input_case
        and (not primitive_case or run.host.calls)
    ):
        raise _issue(
            run,
            check="negative_case",
            code=f"{fixture}_claimed_success",
            message=f"The candidate claimed success during the {fixture} negative case.",
            fixture=fixture,
            inputs=inputs,
        )
    if fixture == "unknown_primitive" and len(run.host.calls) > 1:
        raise _issue(
            run,
            check="negative_case",
            code="unknown_primitive_retried",
            message="The candidate called another primitive after an unknown result.",
            fixture=fixture,
            inputs=inputs,
        )
    if fixture == "unknown_primitive" and run.host.calls and result.status != "inconclusive":
        raise _issue(
            run,
            check="negative_case",
            code="unknown_primitive_not_inconclusive",
            message="An unknown primitive result must be surfaced as inconclusive.",
            fixture=fixture,
            inputs=inputs,
        )
    if fixture == "failed_primitive" and run.host.calls and result.status != "failed":
        raise _issue(
            run,
            check="negative_case",
            code="failed_primitive_not_failed",
            message="A known failed primitive result must be surfaced as failed.",
            fixture=fixture,
            inputs=inputs,
        )
    if (
        fixture == "invalid_input"
        and invalid_case_applies
        and (run.host.calls or result.status != "failed")
    ):
        raise _issue(
            run,
            check="negative_case",
            code="invalid_input_not_rejected",
            message="Invalid input must return failed before calling a primitive.",
            fixture=fixture,
            inputs=inputs,
        )


def _fixture_inputs(metadata: SkillMetadata, trace: StoredEpisode) -> dict[str, JsonValue]:
    properties = metadata.input_schema.get("properties", {})
    assert isinstance(properties, dict)
    values: dict[str, JsonValue] = {}
    for name, raw_schema in properties.items():
        schema = raw_schema if isinstance(raw_schema, dict) else {}
        recorded = next(
            (
                step.request.arguments[name]
                for step in trace.steps
                if name in step.request.arguments
            ),
            None,
        )
        if recorded is not None:
            values[name] = recorded
        elif "const" in schema:
            values[name] = schema["const"]
        elif isinstance(schema.get("enum"), list) and schema["enum"]:
            values[name] = schema["enum"][0]
        elif "default" in schema:
            values[name] = schema["default"]
        elif name.endswith("object_id") and trace.episode.reset_observation.visible_objects:
            values[name] = trace.episode.reset_observation.visible_objects[0].object_id
        else:
            values[name] = _sample_value(schema.get("type"))
    return values


def _sample_value(kind: object) -> JsonValue:
    if kind == "integer":
        return 1
    if kind == "number":
        return 1.0
    if kind == "boolean":
        return True
    if kind == "array":
        return []
    if kind == "object":
        return {}
    return "validation"


def _schema_issue(value: JsonValue, schema: dict[str, JsonValue]) -> str | None:
    """Validate the connector's small JSON-schema subset without a dependency."""
    kind = schema.get("type")
    matches = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "null": value is None,
    }
    if isinstance(kind, str) and kind in matches and not matches[kind]:
        return f"expected {kind}"
    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and value not in enum_values:
        return "value is not in enum"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            return f"value is below minimum {minimum}"
        if isinstance(maximum, (int, float)) and value > maximum:
            return f"value is above maximum {maximum}"
    if not isinstance(value, dict):
        return None
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return "properties is not an object"
    required = schema.get("required", [])
    if isinstance(required, list):
        missing = [name for name in required if isinstance(name, str) and name not in value]
        if missing:
            return f"missing required fields {missing!r}"
    if schema.get("additionalProperties") is False:
        extras = sorted(set(value) - set(properties))
        if extras:
            return f"unexpected fields {extras!r}"
    for name, item in value.items():
        child_schema = properties.get(name)
        if isinstance(child_schema, dict):
            issue = _schema_issue(item, child_schema)
            if issue is not None:
                return f"{name}: {issue}"
    return None


def _invalid_inputs(metadata: SkillMetadata, inputs: dict[str, JsonValue]) -> dict[str, JsonValue]:
    properties = metadata.input_schema.get("properties", {})
    if properties or metadata.input_schema.get("additionalProperties") is False:
        return {**inputs, "__unexpected_validation_input": True}
    # An empty open schema accepts every dictionary, so it has no invalid value
    # within the entry point's fixed ``dict`` API type.
    return inputs


def _without_targets(observation: Observation) -> Observation:
    return observation.model_copy(update={"visible_objects": ()})


def _variation(
    observation: Observation,
    responses: tuple[StepRecord, ...],
    inputs: dict[str, JsonValue],
) -> tuple[Observation, tuple[StepRecord, ...], dict[str, JsonValue], tuple[str, ...]]:
    mapping = {
        item.object_id: f"validation_obj_{index}"
        for index, item in enumerate(observation.visible_objects, start=1)
    }
    changed_observation = Observation.model_validate(
        _replace_ids(observation.model_dump(), mapping)
    )
    changed_responses = tuple(
        StepRecord.model_validate(_replace_ids(step.model_dump(), mapping)) for step in responses
    )
    changed_inputs = _replace_ids(inputs, mapping)
    assert isinstance(changed_inputs, dict)
    return changed_observation, changed_responses, changed_inputs, tuple(mapping.values())


def _replace_ids(value: object, mapping: dict[str, str]) -> object:
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [_replace_ids(item, mapping) for item in value]
    if isinstance(value, tuple):
        return tuple(_replace_ids(item, mapping) for item in value)
    if isinstance(value, dict):
        return {key: _replace_ids(item, mapping) for key, item in value.items()}
    return value


def _evidence_resolves(result: SkillResult, host: _ReplayHost) -> bool:
    for reference in result.evidence:
        if reference.kind == "observation_sequence":
            try:
                if 0 <= int(reference.value) <= host.observation.sequence:
                    return True
            except ValueError:
                pass
        elif reference.kind == "object_id":
            if any(item.object_id == reference.value for item in host.observation.visible_objects):
                return True
        elif reference.kind == "action_id":
            if reference.value.startswith("validation."):
                return True
        elif any(message.text == reference.value for message in host.observation.messages):
            return True
    return False
