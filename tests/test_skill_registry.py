"""The registry records every skill candidate immutably and exposes only accepted ones."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from pydantic import JsonValue, ValidationError

from noob_agent.domain.skills import SkillPackage, SkillStatus, SkillVersion
from noob_agent.skills import (
    ConflictingAcceptedSkillError,
    IllegalTransitionError,
    SkillRegistry,
    UnknownSkillVersionError,
)

AUTHORED_AT = datetime(2026, 9, 12, 17, 0, 0, tzinfo=UTC)
SKILL_NAME = "operate_resonator"
SOURCE = "def run(context):\n    return {'status': 'succeeded'}\n"


@pytest.fixture
def package_factory() -> Callable[..., SkillPackage]:
    def build(
        *,
        name: str = SKILL_NAME,
        source: str = SOURCE,
        metadata: dict[str, JsonValue] | None = None,
    ) -> SkillPackage:
        return SkillPackage(
            name=name,
            source=source,
            metadata={"purpose": "Use the device."} if metadata is None else metadata,
        )

    return build


@pytest.fixture
def registry() -> SkillRegistry:
    return SkillRegistry()


@pytest.fixture
def proposed(registry: SkillRegistry, package_factory: Callable[..., SkillPackage]) -> SkillVersion:
    return registry.propose(
        package_factory(),
        authoring_episode_id="ep_0001",
        authoring_model_id="fake-model-a",
        created_at=AUTHORED_AT,
    )


@pytest.fixture
def advance_to(
    registry: SkillRegistry, proposed: SkillVersion
) -> Callable[[SkillStatus], SkillVersion]:
    """Drive one candidate to a reached status through legal transitions only."""

    def drive(status: SkillStatus) -> SkillVersion:
        version = proposed.version
        if status == "proposed":
            return registry.get(SKILL_NAME, version)
        registry.begin_validation(SKILL_NAME, version, reason="Package check ran.")
        if status == "validating":
            return registry.get(SKILL_NAME, version)
        if status == "rejected":
            return registry.reject(SKILL_NAME, version, reason="Forbidden import.")
        registry.accept(SKILL_NAME, version, reason="All mandatory checks passed.")
        if status == "accepted":
            return registry.get(SKILL_NAME, version)
        return registry.retire(SKILL_NAME, version, reason="Superseded.")

    return drive


@pytest.fixture
def accepted(advance_to: Callable[[SkillStatus], SkillVersion]) -> SkillVersion:
    return advance_to("accepted")


@pytest.fixture
def rejected(advance_to: Callable[[SkillStatus], SkillVersion]) -> SkillVersion:
    return advance_to("rejected")


def test_assigns_sequential_versions_and_a_content_hash(
    registry: SkillRegistry, package_factory: Callable[..., SkillPackage]
) -> None:
    first = registry.propose(
        package_factory(),
        authoring_episode_id="ep_0001",
        authoring_model_id="fake-model-a",
        created_at=AUTHORED_AT,
    )
    second = registry.propose(
        package_factory(source="def run(context):\n    return 'other'\n"),
        authoring_episode_id="ep_0002",
        authoring_model_id="fake-model-a",
        created_at=AUTHORED_AT,
    )

    assert (first.version, second.version) == (1, 2)
    assert first.status == "proposed"
    assert first.parent_version is None
    assert len(first.content_hash) == 64
    assert first.authoring_episode_id == "ep_0001"


def test_content_hash_ignores_metadata_key_order(
    registry: SkillRegistry, package_factory: Callable[..., SkillPackage]
) -> None:
    """The same package must fingerprint identically however its keys were built."""
    forward = registry.propose(
        package_factory(metadata={"purpose": "Use the device.", "max_primitive_actions": 8}),
        authoring_episode_id="ep_0001",
        authoring_model_id="fake-model-a",
        created_at=AUTHORED_AT,
    )
    reversed_keys = registry.propose(
        package_factory(metadata={"max_primitive_actions": 8, "purpose": "Use the device."}),
        authoring_episode_id="ep_0001",
        authoring_model_id="fake-model-a",
        created_at=AUTHORED_AT,
    )

    assert forward.content_hash == reversed_keys.content_hash


def test_a_changed_source_produces_a_different_hash(
    registry: SkillRegistry, package_factory: Callable[..., SkillPackage]
) -> None:
    original = registry.propose(
        package_factory(),
        authoring_episode_id="ep_0001",
        authoring_model_id="fake-model-a",
        created_at=AUTHORED_AT,
    )
    changed = registry.propose(
        package_factory(source="def run(context):\n    return 'changed'\n"),
        authoring_episode_id="ep_0001",
        authoring_model_id="fake-model-a",
        created_at=AUTHORED_AT,
    )

    assert original.content_hash != changed.content_hash


def test_a_recorded_version_cannot_be_mutated(proposed: SkillVersion) -> None:
    with pytest.raises(ValidationError):
        proposed.status = "accepted"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        proposed.package.source = "def run(context):\n    return 'smuggled'\n"  # type: ignore[misc]


def test_a_later_candidate_never_overwrites_an_earlier_version(
    registry: SkillRegistry, package_factory: Callable[..., SkillPackage]
) -> None:
    """Version 1's source and metadata survive a second candidate for the same skill."""
    first = registry.propose(
        package_factory(),
        authoring_episode_id="ep_0001",
        authoring_model_id="fake-model-a",
        created_at=AUTHORED_AT,
    )
    registry.propose(
        package_factory(source="def run(context):\n    return 'second'\n", metadata={"note": "b"}),
        authoring_episode_id="ep_0002",
        authoring_model_id="fake-model-a",
        created_at=AUTHORED_AT,
    )

    assert registry.get(SKILL_NAME, 1) == first
    assert len(registry.versions(SKILL_NAME)) == 2


def test_does_not_alias_the_callers_metadata(
    registry: SkillRegistry, package_factory: Callable[..., SkillPackage]
) -> None:
    """A caller that keeps mutating its own dict cannot reach into the record."""
    metadata: dict[str, object] = {"purpose": "Use the device."}
    recorded = registry.propose(
        package_factory(metadata=metadata),
        authoring_episode_id="ep_0001",
        authoring_model_id="fake-model-a",
        created_at=AUTHORED_AT,
    )

    metadata["purpose"] = "Something else entirely."

    assert recorded.package.metadata["purpose"] == "Use the device."


def test_moves_through_the_declared_states(registry: SkillRegistry, proposed: SkillVersion) -> None:
    version = proposed.version
    validating = registry.begin_validation(SKILL_NAME, version, reason="Package check ran.")
    accepted = registry.accept(SKILL_NAME, version, reason="All mandatory checks passed.")
    retired = registry.retire(SKILL_NAME, version, reason="Superseded by a repair.")

    assert validating.status == "validating"
    assert accepted.status == "accepted"
    assert retired.status == "retired"
    assert retired.status_reason == "Superseded by a repair."


@pytest.mark.parametrize(
    ("reached", "attempted"),
    [
        ("proposed", "accept"),
        ("proposed", "reject"),
        ("proposed", "retire"),
        ("validating", "begin_validation"),
        ("validating", "retire"),
        ("accepted", "begin_validation"),
        ("accepted", "accept"),
        ("accepted", "reject"),
        ("rejected", "begin_validation"),
        ("rejected", "accept"),
        ("rejected", "retire"),
        ("retired", "accept"),
        ("retired", "begin_validation"),
        ("retired", "retire"),
    ],
)
def test_refuses_every_illegal_transition(
    registry: SkillRegistry,
    advance_to: Callable[[SkillStatus], SkillVersion],
    reached: SkillStatus,
    attempted: str,
) -> None:
    version = advance_to(reached)

    with pytest.raises(IllegalTransitionError):
        getattr(registry, attempted)(SKILL_NAME, version.version, reason="Not allowed.")

    assert registry.get(SKILL_NAME, version.version).status == reached


def test_accepting_a_second_version_requires_retiring_the_first(
    registry: SkillRegistry,
    package_factory: Callable[..., SkillPackage],
    accepted: SkillVersion,
) -> None:
    """The BDP exposes one accepted skill, so a silent second acceptance is refused."""
    rival = registry.propose(
        package_factory(source="def run(context):\n    return 'rival'\n"),
        authoring_episode_id="ep_0002",
        authoring_model_id="fake-model-a",
        created_at=AUTHORED_AT,
    )
    registry.begin_validation(SKILL_NAME, rival.version, reason="Package check ran.")

    with pytest.raises(ConflictingAcceptedSkillError):
        registry.accept(SKILL_NAME, rival.version, reason="Also passed.")

    registry.retire(SKILL_NAME, accepted.version, reason="Superseded.")
    promoted = registry.accept(SKILL_NAME, rival.version, reason="Also passed.")

    assert promoted.status == "accepted"
    assert registry.accepted_skill(SKILL_NAME) == promoted


def test_a_repair_keeps_its_parent_and_leaves_the_rejected_version_untouched(
    registry: SkillRegistry,
    package_factory: Callable[..., SkillPackage],
    rejected: SkillVersion,
) -> None:
    repair = registry.propose(
        package_factory(source="def run(context):\n    return 'repaired'\n"),
        authoring_episode_id="ep_0002",
        authoring_model_id="fake-model-a",
        created_at=AUTHORED_AT,
        parent_version=rejected.version,
    )

    assert repair.parent_version == rejected.version
    assert repair.version == rejected.version + 1
    assert registry.get(SKILL_NAME, rejected.version) == rejected


def test_a_repair_must_parent_a_rejected_version(
    registry: SkillRegistry,
    package_factory: Callable[..., SkillPackage],
    proposed: SkillVersion,
) -> None:
    with pytest.raises(IllegalTransitionError):
        registry.propose(
            package_factory(source="def run(context):\n    return 'repaired'\n"),
            authoring_episode_id="ep_0002",
            authoring_model_id="fake-model-a",
            created_at=AUTHORED_AT,
            parent_version=proposed.version,
        )


def test_a_repair_must_name_a_recorded_parent(
    registry: SkillRegistry, package_factory: Callable[..., SkillPackage]
) -> None:
    with pytest.raises(UnknownSkillVersionError):
        registry.propose(
            package_factory(),
            authoring_episode_id="ep_0002",
            authoring_model_id="fake-model-a",
            created_at=AUTHORED_AT,
            parent_version=99,
        )


@pytest.mark.parametrize("reached", ["proposed", "validating", "rejected", "retired"])
def test_only_accepted_versions_are_available(
    registry: SkillRegistry, advance_to: Callable[[SkillStatus], SkillVersion], reached: SkillStatus
) -> None:
    """A candidate the Action agent must never see stays out of the tool list."""
    advance_to(reached)

    assert registry.available_skills() == ()
    assert registry.accepted_skill(SKILL_NAME) is None


def test_available_skills_lists_the_accepted_version(
    registry: SkillRegistry, accepted: SkillVersion
) -> None:
    assert registry.available_skills() == (accepted,)
    assert registry.accepted_skill(SKILL_NAME) == accepted


def test_reading_an_unrecorded_version_is_refused(registry: SkillRegistry) -> None:
    with pytest.raises(UnknownSkillVersionError):
        registry.get(SKILL_NAME, 1)
    with pytest.raises(UnknownSkillVersionError):
        registry.accept("never_proposed", 1, reason="Nothing to accept.")

    assert registry.versions(SKILL_NAME) == ()
