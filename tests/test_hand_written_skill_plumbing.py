"""One hand-written skill through the validation and registry plumbing."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from noob_agent.skills import SkillRegistry, SkillValidationError, check_static_policy
from noob_agent.skills.contract import EvidenceRef, SkillBudget, SkillResult
from noob_agent.verification.validator import validate_for_registry

FIXTURE = Path(__file__).parent / "fixtures" / "skills" / "hand_written_observer"
PRIMITIVES = {"observe", "move_to", "use_object", "wait"}


def test_skill_contract_requires_evidence_for_a_success_claim() -> None:
    with pytest.raises(ValidationError):
        SkillResult(
            status="succeeded",
            summary="Claimed success without evidence.",
            evidence=(),
            outputs={},
            primitive_actions_used=0,
        )

    budget = SkillBudget(primitive_actions=0, wall_time_seconds=0)
    assert budget.primitive_actions == 0


def test_hand_written_fixture_validates_and_enters_the_registry() -> None:
    source = (FIXTURE / "skill.py").read_text(encoding="utf-8")
    metadata_json = (FIXTURE / "skill.json").read_text(encoding="utf-8")

    package = validate_for_registry(
        source,
        metadata_json,
        known_primitive_names=PRIMITIVES,
    )
    registry = SkillRegistry()
    proposed = registry.propose(
        package,
        authoring_episode_id="ep_training_0001",
        authoring_model_id="hand-written-plumbing-fixture",
        created_at=datetime(2026, 9, 12, 17, 30, tzinfo=UTC),
    )
    registry.begin_validation(package.name, proposed.version, reason="Automated checks passed.")
    accepted = registry.accept(package.name, proposed.version, reason="Fixture accepted.")

    assert accepted.content_hash
    assert registry.available_skills() == (accepted,)
    assert "hand-written" in source.lower()


def test_unsafe_candidate_is_rejected_before_it_can_enter_the_registry() -> None:
    metadata_json = (FIXTURE / "skill.json").read_text(encoding="utf-8")

    with pytest.raises(SkillValidationError):
        validate_for_registry(
            "import socket\n\nasync def run(context, inputs):\n    return None\n",
            metadata_json,
            known_primitive_names=PRIMITIVES,
        )


def test_only_public_skill_contract_names_may_be_imported() -> None:
    allowed = check_static_policy(
        "from noob_agent.skills.contract import SkillContext, SkillResult\n"
    )
    refused = check_static_policy(
        "from noob_agent.skills.contract import SkillContractModel\n"
    )

    assert allowed == []
    assert any(issue.code == "forbidden_import" for issue in refused)


def test_skill_evidence_is_immutable_and_publicly_referenceable() -> None:
    evidence = EvidenceRef(kind="action_id", value="a_0001")
    result = SkillResult(
        status="succeeded",
        summary="A public action result confirms the postcondition.",
        evidence=(evidence,),
        outputs={"confirmed": True},
        primitive_actions_used=1,
    )

    with pytest.raises(ValidationError):
        result.status = "failed"  # type: ignore[misc]
    assert result.evidence == (evidence,)
