"""Generated-skill boundaries: the non-executing validation boundary, the
immutable version registry, and the sandbox seam.
"""

from noob_agent.skills.contract import EvidenceRef, SkillBudget, SkillContext, SkillResult
from noob_agent.skills.errors import SkillValidationError, SkillValidationIssue
from noob_agent.skills.metadata import API_VERSION, SkillMetadata
from noob_agent.skills.package import SkillPackage, validate_skill_package
from noob_agent.skills.policy import check_static_policy
from noob_agent.skills.registry import (
    ConflictingAcceptedSkillError,
    IllegalTransitionError,
    RegistryError,
    SkillRegistry,
    UnknownSkillVersionError,
    content_hash,
)

__all__ = [
    "API_VERSION",
    "ConflictingAcceptedSkillError",
    "EvidenceRef",
    "IllegalTransitionError",
    "RegistryError",
    "SkillMetadata",
    "SkillBudget",
    "SkillContext",
    "SkillPackage",
    "SkillRegistry",
    "SkillResult",
    "SkillValidationError",
    "SkillValidationIssue",
    "UnknownSkillVersionError",
    "check_static_policy",
    "content_hash",
    "validate_skill_package",
]
