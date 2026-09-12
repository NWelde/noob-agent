"""Non-executing validation boundary for a candidate skill package."""

from noob_agent.skills.errors import SkillValidationError, SkillValidationIssue
from noob_agent.skills.metadata import API_VERSION, SkillMetadata
from noob_agent.skills.package import SkillPackage, validate_skill_package
from noob_agent.skills.policy import check_static_policy

__all__ = [
    "API_VERSION",
    "SkillMetadata",
    "SkillPackage",
    "SkillValidationError",
    "SkillValidationIssue",
    "check_static_policy",
    "validate_skill_package",
]
