"""Package-level validation for a candidate `skill.py` + `skill.json` pair.

This is skill_contract.md's validation pipeline stage 1 ("Package check")
combined with stage 2 ("Static policy check"). It never imports or executes
candidate Python, and never adds a sandbox dependency.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from pydantic import ValidationError

from noob_agent.skills.errors import SkillValidationError, SkillValidationIssue
from noob_agent.skills.metadata import SkillMetadata
from noob_agent.skills.policy import check_static_policy

MAX_SOURCE_BYTES = 12 * 1024
MAX_METADATA_BYTES = 4 * 1024


@dataclass(frozen=True)
class SkillPackage:
    """A candidate that has passed the package check and the static policy check."""

    source: str
    metadata: SkillMetadata


def validate_skill_package(
    source: str,
    metadata_json: str,
    *,
    known_primitive_names: Collection[str],
) -> SkillPackage:
    """Validate a candidate package without importing or executing `source`.

    Raises `SkillValidationError` carrying every issue found, across both the
    package check and the static policy check, so a Builder repair prompt can
    address them in one round.
    """
    issues: list[SkillValidationIssue] = []

    source_bytes = len(source.encode("utf-8"))
    if source_bytes > MAX_SOURCE_BYTES:
        issues.append(
            SkillValidationIssue(
                check="package",
                code="source_too_large",
                message=(
                    f"skill.py is {source_bytes} bytes, over the {MAX_SOURCE_BYTES}-byte limit."
                ),
            )
        )

    metadata_bytes = len(metadata_json.encode("utf-8"))
    if metadata_bytes > MAX_METADATA_BYTES:
        issues.append(
            SkillValidationIssue(
                check="package",
                code="metadata_too_large",
                message=(
                    f"skill.json is {metadata_bytes} bytes, over the "
                    f"{MAX_METADATA_BYTES}-byte limit."
                ),
            )
        )

    metadata: SkillMetadata | None = None
    try:
        metadata = SkillMetadata.model_validate_json(metadata_json)
    except ValidationError as error:
        for detail in error.errors():
            location = ".".join(str(part) for part in detail["loc"])
            message = f"{location}: {detail['msg']}" if location else detail["msg"]
            issues.append(
                SkillValidationIssue(check="package", code="invalid_metadata", message=message)
            )

    if metadata is not None and metadata.name in known_primitive_names:
        issues.append(
            SkillValidationIssue(
                check="package",
                code="name_shadows_primitive",
                message=f"Skill name {metadata.name!r} shadows a primitive tool name.",
            )
        )

    issues.extend(check_static_policy(source))

    if issues:
        raise SkillValidationError(tuple(issues))

    assert metadata is not None  # no issues means metadata parsed successfully
    return SkillPackage(source=source, metadata=metadata)
