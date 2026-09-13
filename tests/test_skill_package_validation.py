"""Package-level validation for a candidate `skill.py` + `skill.json` pair.

Covers skill_contract.md's stage-1 "Package check": names, sizes, schemas, API
version, and (together with the static policy check) source hash-worthy
content. Nothing here imports or executes candidate Python.
"""

from __future__ import annotations

import json

import pytest

from noob_agent.skills import SkillMetadata, SkillValidationError, validate_skill_package

VALID_SOURCE = '''"""Locate the lantern and confirm it is lit."""

from __future__ import annotations


async def run(context, inputs):
    await context.observe()
    result = await context.call("use_object", object_id=inputs["object_id"])
    return {
        "status": "succeeded" if result.state_changed else "failed",
        "summary": "Used the object.",
        "evidence": [],
        "outputs": {},
        "primitive_actions_used": 1,
    }
'''

VALID_METADATA: dict[str, object] = {
    "name": "operate_resonator",
    "version": 1,
    "parent_version": None,
    "purpose": "Use the unfamiliar device and verify its output.",
    "input_schema": {"properties": {"object_id": {"type": "string"}}},
    "required_tools": ["observe", "use_object"],
    "max_primitive_actions": 8,
    "max_wall_time_seconds": 30,
    "success_claim": "The expected output is visible after activation.",
    "api_version": "noob-agent.skill.v1",
}

KNOWN_PRIMITIVE_NAMES = {"observe", "use_object", "move_to", "wait"}


def _metadata_json(**overrides: object) -> str:
    return json.dumps({**VALID_METADATA, **overrides})


def test_accepts_a_valid_minimal_package() -> None:
    package = validate_skill_package(
        VALID_SOURCE, _metadata_json(), known_primitive_names=KNOWN_PRIMITIVE_NAMES
    )

    assert package.source == VALID_SOURCE
    assert isinstance(package.metadata, SkillMetadata)
    assert package.metadata.name == "operate_resonator"


def test_rejects_an_oversized_source() -> None:
    oversized_source = VALID_SOURCE + ("# padding\n" * 2000)

    with pytest.raises(SkillValidationError) as excinfo:
        validate_skill_package(
            oversized_source, _metadata_json(), known_primitive_names=KNOWN_PRIMITIVE_NAMES
        )

    assert any(issue.code == "source_too_large" for issue in excinfo.value.issues)


def test_rejects_oversized_metadata() -> None:
    padded = _metadata_json(purpose="x" * 5000)

    with pytest.raises(SkillValidationError) as excinfo:
        validate_skill_package(VALID_SOURCE, padded, known_primitive_names=KNOWN_PRIMITIVE_NAMES)

    assert any(issue.code == "metadata_too_large" for issue in excinfo.value.issues)


@pytest.mark.parametrize(
    "overrides",
    [
        {"api_version": "noob-agent.skill.v2"},
        {"name": "Operate-Resonator"},
        {"max_primitive_actions": 0},
        {"max_wall_time_seconds": -1},
        {"required_tools": []},
        {"input_schema": {"properties": {f"field_{i}": {"type": "string"} for i in range(9)}}},
    ],
)
def test_rejects_invalid_metadata(overrides: dict[str, object]) -> None:
    with pytest.raises(SkillValidationError) as excinfo:
        validate_skill_package(
            VALID_SOURCE, _metadata_json(**overrides), known_primitive_names=KNOWN_PRIMITIVE_NAMES
        )

    assert any(issue.code == "invalid_metadata" for issue in excinfo.value.issues)


def test_rejects_a_name_that_shadows_a_primitive() -> None:
    with pytest.raises(SkillValidationError) as excinfo:
        validate_skill_package(
            VALID_SOURCE,
            _metadata_json(name="use_object"),
            known_primitive_names=KNOWN_PRIMITIVE_NAMES,
        )

    assert any(issue.code == "name_shadows_primitive" for issue in excinfo.value.issues)


def test_surfaces_static_policy_violations_from_the_source() -> None:
    source_with_forbidden_import = "import os\n" + VALID_SOURCE

    with pytest.raises(SkillValidationError) as excinfo:
        validate_skill_package(
            source_with_forbidden_import,
            _metadata_json(),
            known_primitive_names=KNOWN_PRIMITIVE_NAMES,
        )

    assert any(issue.check == "static_policy" for issue in excinfo.value.issues)


def test_reports_metadata_and_policy_issues_together_for_one_repair_round() -> None:
    source_with_forbidden_import = "import socket\n" + VALID_SOURCE

    with pytest.raises(SkillValidationError) as excinfo:
        validate_skill_package(
            source_with_forbidden_import,
            _metadata_json(name="use_object"),
            known_primitive_names=KNOWN_PRIMITIVE_NAMES,
        )

    checks = {issue.check for issue in excinfo.value.issues}
    assert checks == {"package", "static_policy"}
