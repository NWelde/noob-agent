"""The local skill registry: every candidate recorded, only accepted ones exposed.

Uses the standard library and the immutable records in `domain/skills.py`. The
registry owns two responsibilities the rest of the harness depends on:

1. It assigns each candidate a version number and a content hash, so an
   accepted artifact can be pinned for a whole comparison cell.
2. It decides what an Action agent may see. Only an `accepted` version appears
   in the available-skill list; a proposed, validating, rejected, or retired
   candidate never does, because showing one would contaminate the held-out
   evaluation.

Status advances only along the transitions named in `skill_contract.md`. An
advance records a new version record rather than editing recorded content:
source, metadata, parent, and content hash never change once written.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from noob_agent.domain.skills import SkillPackage, SkillStatus, SkillVersion


class RegistryError(RuntimeError):
    """Base class for every skill-registry failure."""


class UnknownSkillVersionError(RegistryError):
    """The named skill version, or the parent it repairs, is not recorded."""


class IllegalTransitionError(RegistryError):
    """The requested status change is not part of the declared state machine."""


class ConflictingAcceptedSkillError(RegistryError):
    """Another version of this skill is already accepted and must be retired first."""


# proposed -> validating -> accepted | rejected, and retired only from accepted.
_ALLOWED_TRANSITIONS: dict[SkillStatus, frozenset[SkillStatus]] = {
    "proposed": frozenset({"validating"}),
    "validating": frozenset({"accepted", "rejected"}),
    "accepted": frozenset({"retired"}),
    "rejected": frozenset(),
    "retired": frozenset(),
}


def content_hash(package: SkillPackage) -> str:
    """Fingerprint a candidate's content.

    Keys are sorted, so a package built in a different order still hashes
    identically. Any change to source, metadata, name, or API version produces a
    different hash and therefore requires a new evaluation run.
    """
    canonical = json.dumps(
        {
            "api_version": package.api_version,
            "metadata": package.metadata,
            "name": package.name,
            "source": package.source,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class SkillRegistry:
    """Append-only versions for every generated skill candidate."""

    def __init__(self) -> None:
        self._versions: dict[str, list[SkillVersion]] = {}

    def propose(
        self,
        package: SkillPackage,
        *,
        authoring_episode_id: str,
        authoring_model_id: str,
        created_at: datetime,
        parent_version: int | None = None,
        reason: str = "Candidate submitted.",
    ) -> SkillVersion:
        """Record a new candidate. A repair names the rejected version it follows."""
        if parent_version is not None:
            parent = self._find(package.name, parent_version)
            if parent.status != "rejected":
                raise IllegalTransitionError(
                    f"Skill {package.name!r} version {parent_version} is "
                    f"{parent.status!r}; only a rejected version can be repaired."
                )

        recorded = self._versions.setdefault(package.name, [])
        version = SkillVersion(
            name=package.name,
            version=len(recorded) + 1,
            content_hash=content_hash(package),
            package=package,
            parent_version=parent_version,
            authoring_episode_id=authoring_episode_id,
            authoring_model_id=authoring_model_id,
            status="proposed",
            status_reason=reason,
            created_at=created_at,
        )
        recorded.append(version)
        return version

    def begin_validation(self, name: str, version: int, *, reason: str) -> SkillVersion:
        """Take a proposed candidate into validation."""
        return self._transition(name, version, to="validating", reason=reason)

    def accept(self, name: str, version: int, *, reason: str) -> SkillVersion:
        """Accept a validated candidate as the skill the Action agent may use."""
        return self._transition(name, version, to="accepted", reason=reason)

    def reject(self, name: str, version: int, *, reason: str) -> SkillVersion:
        """Reject a validated candidate, keeping it recorded for repair and audit."""
        return self._transition(name, version, to="rejected", reason=reason)

    def retire(self, name: str, version: int, *, reason: str) -> SkillVersion:
        """Withdraw an accepted skill so it no longer appears in the tool list."""
        return self._transition(name, version, to="retired", reason=reason)

    def get(self, name: str, version: int) -> SkillVersion:
        """Read one recorded version, whatever its status."""
        return self._find(name, version)

    def versions(self, name: str) -> tuple[SkillVersion, ...]:
        """Every recorded version of one skill, in the order it was proposed."""
        return tuple(self._versions.get(name, ()))

    def accepted_skill(self, name: str) -> SkillVersion | None:
        """The single accepted version of one skill, or `None` when there is none."""
        for recorded in self._versions.get(name, ()):
            if recorded.status == "accepted":
                return recorded
        return None

    def available_skills(self) -> tuple[SkillVersion, ...]:
        """Exactly the versions an Action agent may be offered: accepted ones."""
        return tuple(
            recorded
            for name in sorted(self._versions)
            for recorded in self._versions[name]
            if recorded.status == "accepted"
        )

    def _find(self, name: str, version: int) -> SkillVersion:
        for recorded in self._versions.get(name, ()):
            if recorded.version == version:
                return recorded
        raise UnknownSkillVersionError(f"Skill {name!r} version {version} is not recorded.")

    def _transition(self, name: str, version: int, *, to: SkillStatus, reason: str) -> SkillVersion:
        """Advance one version along a declared transition, refusing every other move."""
        current = self._find(name, version)
        if to not in _ALLOWED_TRANSITIONS[current.status]:
            raise IllegalTransitionError(
                f"Skill {name!r} version {version} is {current.status!r} and cannot become {to!r}."
            )
        if to == "accepted":
            live = self.accepted_skill(name)
            if live is not None:
                raise ConflictingAcceptedSkillError(
                    f"Skill {name!r} version {live.version} is already accepted; "
                    "retire it before accepting another version."
                )

        # Rebuilt through validation rather than `model_copy`, which would skip it.
        replacement = SkillVersion.model_validate(
            current.model_dump() | {"status": to, "status_reason": reason}
        )
        recorded = self._versions[name]
        recorded[recorded.index(current)] = replacement
        return replacement
