"""Bridge validated candidate packages into the immutable skill registry."""

from __future__ import annotations

from collections.abc import Collection

from noob_agent.domain.skills import SkillPackage as RegistrySkillPackage
from noob_agent.skills.package import validate_skill_package


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
