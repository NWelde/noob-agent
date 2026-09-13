"""Structured, public validation failures for a candidate skill package.

These are the only errors a Builder repair prompt ever sees: no private
grader state, no held-out data, just what the package check or static policy
check actually found.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

SkillValidationCheck = Literal["package", "static_policy"]


class SkillValidationIssue(BaseModel):
    """One failure from the package check or the static policy check."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    check: SkillValidationCheck
    code: str
    message: str
    line: int | None = None
    column: int | None = None


class SkillValidationError(Exception):
    """Raised when a candidate package fails one or more validation checks."""

    def __init__(self, issues: tuple[SkillValidationIssue, ...]) -> None:
        if not issues:
            raise ValueError("SkillValidationError requires at least one issue.")
        self.issues = issues
        summary = "; ".join(f"[{issue.check}/{issue.code}] {issue.message}" for issue in issues)
        super().__init__(summary)
