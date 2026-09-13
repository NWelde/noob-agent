"""Step 22.D: a multi-round loop that keeps a skill only when public practice improves."""

from __future__ import annotations

import itertools
import json
from datetime import UTC, datetime
from typing import Any

from fakes.connector import FakeClock, ScriptedConnector, ScriptedStep

from noob_agent.domain.model import StepResult, ToolRequest
from noob_agent.domain.records import StoredEpisode
from noob_agent.models.client import ModelRequest, ModelResponse
from noob_agent.prompts.builder import BUILDER_SYSTEM
from noob_agent.runtime.improvement import ImprovementLoop, PracticeScore
from noob_agent.runtime.sequence import HeldOutCell
from noob_agent.skills.executor import LocalSubprocessSkillExecutor
from noob_agent.skills.registry import SkillRegistry
from noob_agent.storage import EpisodeStore

STARTED_AT = datetime(2026, 9, 13, 8, 0, tzinfo=UTC)
TRAINING = "train-room"
PRACTICE = (HeldOutCell(TRAINING, 11), HeldOutCell(TRAINING, 12))
HELDOUT = (HeldOutCell("held-a", 21), HeldOutCell("held-b", 22))
GRADE_MARK = "PRIVATE-GRADE-MARK"
SKILL = "reach_the_post"

LOOK_SOURCE = '''from noob_agent.skills.contract import EvidenceRef, SkillContext, SkillResult


async def run(context: SkillContext, inputs: dict[str, object]) -> SkillResult:
    """Look at the post without touching it."""
    if inputs:
        return SkillResult(status="failed", summary="Takes no inputs.", primitive_actions_used=0)
    observation = await context.observe()
    return SkillResult(
        status="inconclusive",
        summary="Looked at the post.",
        evidence=(EvidenceRef(kind="observation_sequence", value=str(observation.sequence)),),
        primitive_actions_used=0,
    )
'''

USE_SOURCE = '''from noob_agent.skills.contract import EvidenceRef, SkillContext, SkillResult


async def run(context: SkillContext, inputs: dict[str, object]) -> SkillResult:
    """Use the visible post once and report the public result."""
    if inputs:
        return SkillResult(status="failed", summary="Takes no inputs.", primitive_actions_used=0)
    observation = await context.observe()
    if not observation.visible_objects:
        return SkillResult(status="failed", summary="No post visible.", primitive_actions_used=0)
    if context.remaining_budget().primitive_actions < 1:
        return SkillResult(status="failed", summary="No budget.", primitive_actions_used=0)
    result = await context.call("use_object", object_id=observation.visible_objects[0].object_id)
    used = result.primitive_actions_charged
    if result.status == "unknown":
        return SkillResult(status="inconclusive", summary=result.code, primitive_actions_used=used)
    if result.status != "succeeded":
        return SkillResult(status="failed", summary=result.code, primitive_actions_used=used)
    return SkillResult(
        status="succeeded",
        summary="Used the post.",
        evidence=(EvidenceRef(kind="action_id", value=result.action_id),),
        primitive_actions_used=used,
    )
'''


def _candidate(source: str, *, tools: list[str], name: str = SKILL, actions: int = 1) -> str:
    metadata = {
        "name": name,
        "version": 1,
        "parent_version": None,
        "purpose": "Reach the post.",
        "input_schema": {"properties": {}},
        "required_tools": tools,
        "max_primitive_actions": actions,
        "max_wall_time_seconds": 5,
        "success_claim": "The post was used.",
        "api_version": "noob-agent.skill.v1",
    }
    return f"```python\n{source}```\n\n```json\n{json.dumps(metadata)}\n```\n"


LOOK = _candidate(LOOK_SOURCE, tools=["observe"])
USE = _candidate(USE_SOURCE, tools=["use_object"])


class PostConnector(ScriptedConnector):
    """Only `use_object` ends an episode, and only on seeds in `finishing_seeds`."""

    ids = itertools.count(1)

    def __init__(self, finishing_seeds: frozenset[int] | None = None) -> None:
        super().__init__(episode_id=f"ep_post_{next(self.ids):04d}")
        self._finishing = finishing_seeds
        self._seed = 0

    async def reset(self, scenario_id: str, seed: int) -> Any:
        self._seed = seed
        return await super().reset(scenario_id, seed)

    async def step(self, request: ToolRequest) -> StepResult:
        finishes = request.tool_name == "use_object" and (
            self._finishing is None or self._seed in self._finishing
        )
        self._script = [
            ScriptedStep(terminal=finishes, terminal_reason="done" if finishes else None)
        ]
        return await super().step(request)


class Provider:
    """Training observes then uses the post; with a skill offered the Action agent calls it."""

    provider = "scripted"

    def __init__(self, *builder_replies: str) -> None:
        self.builder_replies = list(builder_replies)
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if request.system == BUILDER_SYSTEM:
            if not self.builder_replies:
                raise AssertionError("more Builder calls than scripted")
            text = self.builder_replies.pop(0)
            return ModelResponse(text=text, input_tokens=900, output_tokens=400, model_id="f")
        schema = request.response_schema or {}
        names = schema.get("properties", {}).get("action", {}).get("enum", [])
        if SKILL in names:
            action, arguments = SKILL, {}
        elif "sequence 0)" in request.prompt:
            action, arguments = "observe", {}
        else:
            action, arguments = "use_object", {"object_id": "obj_1"}
        decision = {
            "subgoal": "Reach the post.",
            "expected_evidence": "The episode ends.",
            "action": action,
            "arguments": arguments,
            "finding": None,
        }
        return ModelResponse(
            text=json.dumps(decision), input_tokens=300, output_tokens=40, model_id="f"
        )

    def builder_requests(self) -> list[ModelRequest]:
        return [r for r in self.requests if r.system == BUILDER_SYSTEM]


def _grade(stored: StoredEpisode, connector: PostConnector) -> str:
    del connector
    done = stored.outcome is not None and stored.outcome.stop_reason == "terminal_state"
    return f"{GRADE_MARK}-{done}"


def _loop(
    store: EpisodeStore,
    provider: Provider,
    *,
    finishing_seeds: frozenset[int] | None = None,
    registry: SkillRegistry | None = None,
    **options: Any,
) -> ImprovementLoop[PostConnector, str]:
    return ImprovementLoop(
        connector_factory=lambda: PostConnector(finishing_seeds),
        client=provider,
        model_id="f",
        store=store,
        registry=registry if registry is not None else SkillRegistry(),
        executor=LocalSubprocessSkillExecutor(),
        grade=_grade,
        grade_success=lambda grade: grade.endswith("True"),
        clock=FakeClock(wall=STARTED_AT),
        **options,
    )


async def _run(loop: ImprovementLoop[PostConnector, str], *, curve: bool = False) -> Any:
    return await loop.run(
        sequence_id="improve",
        training_scenario_id=TRAINING,
        training_seed=7,
        practice=PRACTICE,
        heldout=HELDOUT,
        curve=curve,
    )


def test_a_higher_terminal_rate_wins_and_fewer_primitives_break_ties() -> None:
    assert PracticeScore(terminal_rate=1.0, primitives=9).better_than(
        PracticeScore(terminal_rate=0.5, primitives=2)
    )
    assert PracticeScore(terminal_rate=0.5, primitives=2).better_than(
        PracticeScore(terminal_rate=0.5, primitives=3)
    )
    assert not PracticeScore(terminal_rate=0.5, primitives=3).better_than(
        PracticeScore(terminal_rate=0.5, primitives=3)
    )


async def test_a_refinement_that_improves_practice_replaces_the_incumbent(
    store: EpisodeStore,
) -> None:
    provider = Provider(LOOK, USE)
    registry = SkillRegistry()

    result = await _run(_loop(store, provider, registry=registry))

    incumbent, refined = result.rounds
    assert (incumbent.decision, incumbent.score.terminal_rate) == ("incumbent", 0.0)
    assert (refined.decision, refined.score.terminal_rate) == ("kept", 1.0)
    assert result.stop_reason == "perfect_practice"
    versions = registry.versions(SKILL)
    assert [(v.version, v.status) for v in versions if v.version in (1, 2)][-2:] == [
        (1, "retired"),
        (2, "accepted"),
    ]
    assert result.final_version is not None and result.final_version.version == 2
    assert result.final_version.parent_version == 1
    assert [e.offered_skills for e in result.heldout] == [(SKILL,), (SKILL,)]
    assert all(e.grade.endswith("True") for e in result.heldout)


async def test_practice_uses_training_split_seeds_under_held_out_budgets(
    store: EpisodeStore,
) -> None:
    result = await _run(_loop(store, Provider(LOOK, USE)))

    for round_report in result.rounds:
        for practice in round_report.practice:
            stored = store.read_episode(practice.episode_id)
            assert stored.episode.split == "training"
            assert (stored.episode.scenario_id, stored.episode.seed) in {
                (c.scenario_id, c.seed) for c in PRACTICE
            }
            assert stored.episode.experiment_id == "improve-practice"
    assert store.read_episode(result.rounds[0].practice[0].episode_id).outcome.total_decisions <= 12


async def test_no_held_out_or_private_value_reaches_a_builder_prompt(store: EpisodeStore) -> None:
    provider = Provider(LOOK, USE)

    await _run(_loop(store, provider))

    builder = provider.builder_requests()
    assert len(builder) == 2
    for request in builder:
        text = request.prompt + request.system
        assert GRADE_MARK not in text
        for cell in HELDOUT:
            assert cell.scenario_id not in text
    refine = builder[1].prompt
    assert LOOK_SOURCE.strip() in refine
    assert "0 of 2" in refine


async def test_held_out_runs_only_after_the_last_builder_call(store: EpisodeStore) -> None:
    provider = Provider(LOOK, USE)

    await _run(_loop(store, provider))

    last_builder = max(i for i, r in enumerate(provider.requests) if r.system == BUILDER_SYSTEM)
    heldout = [
        i
        for i, r in enumerate(provider.requests)
        if r.system != BUILDER_SYSTEM and any(c.scenario_id in r.prompt for c in HELDOUT)
    ]
    assert heldout and min(heldout) > last_builder


def _look_variant(label: str) -> str:
    return _candidate(LOOK_SOURCE.replace("Looked at the post.", label), tools=["observe"])


async def test_one_round_without_improvement_does_not_stop_the_loop(store: EpisodeStore) -> None:
    registry = SkillRegistry()

    result = await _run(
        _loop(store, Provider(LOOK, _look_variant("Looked again."), USE), registry=registry)
    )

    assert [r.decision for r in result.rounds] == ["incumbent", "not_improved", "kept"]
    assert result.stop_reason == "perfect_practice"
    statuses = {v.version: v.status for v in registry.versions(SKILL)}
    assert statuses == {1: "retired", 2: "rejected", 3: "accepted"}
    # The next refinement after a rejection still starts from the incumbent.
    assert registry.get(SKILL, 3).parent_version == 1
    rejected = registry.get(SKILL, 2)
    assert "did not improve practice score" in rejected.status_reason


async def test_two_rounds_in_a_row_without_improvement_stop_the_loop(store: EpisodeStore) -> None:
    provider = Provider(LOOK, _look_variant("Looked again."), _look_variant("Looked once more."))

    result = await _run(_loop(store, provider))

    assert [r.decision for r in result.rounds] == ["incumbent", "not_improved", "not_improved"]
    assert result.stop_reason == "no_improvement"
    assert result.final_version is not None and result.final_version.version == 1


async def test_a_failed_validation_counts_as_a_round_without_improvement(
    store: EpisodeStore,
) -> None:
    provider = Provider(LOOK, "No code this time.", _look_variant("Looked again."))

    result = await _run(_loop(store, provider))

    assert [r.decision for r in result.rounds] == ["incumbent", "not_validated", "not_improved"]
    assert result.stop_reason == "no_improvement"


async def test_a_kept_version_resets_the_patience_count(store: EpisodeStore) -> None:
    provider = Provider(
        LOOK,
        _look_variant("Looked again."),
        USE,
        _candidate(USE_SOURCE.replace("Used the post.", "Used it."), tools=["use_object"]),
        _candidate(USE_SOURCE.replace("Used the post.", "Used it once."), tools=["use_object"]),
    )

    result = await _run(
        _loop(store, provider, finishing_seeds=frozenset({PRACTICE[0].seed}), max_rounds=5)
    )

    assert [r.decision for r in result.rounds] == [
        "incumbent",
        "not_improved",
        "kept",
        "not_improved",
        "not_improved",
    ]
    assert result.stop_reason == "no_improvement"


async def test_refinement_calls_are_recorded_with_the_refine_purpose(store: EpisodeStore) -> None:
    await _run(_loop(store, Provider(LOOK, USE)))

    purposes = [call.purpose for call in store.read_model_calls() if call.purpose != "action"]
    assert purposes == ["build", "refine"]


async def test_the_loop_stops_after_its_maximum_rounds(store: EpisodeStore) -> None:
    provider = Provider(LOOK, USE)

    result = await _run(
        _loop(store, provider, finishing_seeds=frozenset({PRACTICE[0].seed}), max_rounds=1)
    )

    assert [r.decision for r in result.rounds] == ["incumbent", "kept"]
    assert result.rounds[1].score.terminal_rate == 0.5
    assert result.stop_reason == "max_rounds"


async def test_the_learning_budget_stops_before_an_unsent_refinement(store: EpisodeStore) -> None:
    provider = Provider(LOOK, USE)

    result = await _run(_loop(store, provider, learning_token_budget=15_000))

    assert result.stop_reason == "learning_budget"
    assert len(provider.builder_requests()) == 1
    assert result.learning_tokens <= 15_000


async def test_no_skill_means_no_practice_and_no_held_out(store: EpisodeStore) -> None:
    result = await _run(_loop(store, Provider("I cannot write a skill.")))

    assert result.stop_reason == "no_skill"
    assert result.rounds == () and result.heldout == ()


async def test_the_curve_evaluates_each_kept_version_only_after_the_loop(
    store: EpisodeStore,
) -> None:
    provider = Provider(LOOK, USE)

    result = await _run(_loop(store, provider), curve=True)

    assert [point.version for point in result.curve] == [1, 2]
    last_builder = max(i for i, r in enumerate(provider.requests) if r.system == BUILDER_SYSTEM)
    assert all(point.learning_tokens > 0 for point in result.curve)
    for point in result.curve:
        assert len(point.heldout) == len(HELDOUT)
    curve_prompts = [
        i
        for i, r in enumerate(provider.requests)
        if r.system != BUILDER_SYSTEM and any(c.scenario_id in r.prompt for c in HELDOUT)
    ]
    assert min(curve_prompts) > last_builder
    assert result.curve[0].heldout[0].grade.endswith("False")
    assert result.curve[1].heldout[0].grade.endswith("True")


async def test_practice_grades_are_recorded_for_agreement_only(store: EpisodeStore) -> None:
    result = await _run(_loop(store, Provider(LOOK, USE)))

    refined = result.rounds[1]
    assert all(p.grade.endswith("True") for p in refined.practice)
    assert result.practice_agreement == 1.0
