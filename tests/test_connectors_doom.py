"""Acceptance tests for the ViZDoom connector."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fakes.connector import FakeClock, ScriptedPolicy

from noob_agent.connectors import ConnectorError, GameConnector, doom
from noob_agent.connectors.doom import DoomConnector
from noob_agent.domain.model import Observation, StepResult, ToolRequest
from noob_agent.domain.records import ExperimentRecord
from noob_agent.runtime import EpisodeRunner
from noob_agent.storage import EpisodeStore

TRAINING = "doom-basic-training"
HELDOUT_A = "doom-basic-heldout-a"
HELDOUT_B = "doom-basic-heldout-b"
SEED = 20260912
STARTED_AT = datetime(2026, 9, 12, 22, 0, 0, tzinfo=UTC)
CENTERED_SEED = 101
MANIFEST_PATH = Path("scenarios/doom/basic-v1/manifest.json")
APPROVED_GOAL = (
    "Eliminate the hostile target in this area. Use visible results as evidence. "
    "Finish within the action budget."
)
AGENT_FACING_PACKAGES = ("agents", "prompts", "skills", "runtime", "connectors")
PRIVATE_WORDS = ("kill", "dead", "death", "timeout", "timed_out")


def _target(observation: Observation) -> int:
    targets = [item for item in observation.visible_objects if item.label == "Cacodemon"]
    assert len(targets) == 1, observation.visible_objects
    offset = targets[0].properties["screen_offset"]
    assert isinstance(offset, int)
    return offset


def _without_episode_id(observation: Observation) -> dict[str, object]:
    return observation.model_dump(exclude={"episode_id"})


async def test_fresh_connectors_give_unique_seed_free_episode_ids() -> None:
    episode_ids = []
    for _ in range(3):
        connector = DoomConnector()
        try:
            episode_ids.append((await connector.reset(HELDOUT_A, SEED)).episode_id)
        finally:
            await connector.close()

    assert len(set(episode_ids)) == 3, episode_ids
    for episode_id in episode_ids:
        assert episode_id.startswith(f"{HELDOUT_A}-")
        assert "doom-doom-" not in episode_id
        assert str(SEED) not in episode_id
        for word in PRIVATE_WORDS:
            assert word not in episode_id.lower()


async def test_episodes_from_fresh_connectors_share_one_store(store: EpisodeStore) -> None:
    experiment = ExperimentRecord(
        experiment_id="exp_doom_ids",
        model_id="scripted-policy",
        condition="cold",
        connector_version=doom.CONNECTOR_VERSION,
        decision_budget=1,
        primitive_budget=1,
        wall_time_budget_ms=90_000,
        created_at=STARTED_AT,
    )
    store.create_experiment(experiment)

    recorded = []
    for seed in (101, 102, 103):
        runner = EpisodeRunner(
            connector=DoomConnector(),
            store=store,
            policy=ScriptedPolicy(("observe", {})),
            clock=FakeClock(wall=STARTED_AT),
        )
        result = await runner.run(
            experiment=experiment, scenario_id=HELDOUT_A, seed=seed, split="held-out"
        )
        recorded.append(store.read_episode(result.episode_id).episode.seed)

    assert recorded == [101, 102, 103]


async def test_manifest_declares_the_ten_doom_bdp_primitives() -> None:
    manifest = await DoomConnector().manifest()

    assert manifest.connector_version == doom.CONNECTOR_VERSION == "doom-vizdoom-v2"
    assert [tool.name for tool in manifest.tools] == [
        "observe",
        "move_forward",
        "move_backward",
        "strafe_left",
        "strafe_right",
        "turn_left",
        "turn_right",
        "attack",
        "use",
        "wait",
    ]


async def test_reset_and_every_primitive_produce_public_schema_valid_results() -> None:
    connector = DoomConnector()
    try:
        reset = await connector.reset(TRAINING, SEED)
        assert reset.sequence == 0
        assert reset.game_id == "doom-vizdoom"
        assert reset.public_goal == doom.PUBLIC_GOAL == APPROVED_GOAL
        assert "health" in reset.status
        assert all(item.object_id.startswith("obj_") for item in reset.visible_objects)

        requests = [
            ToolRequest(action_id="a1", tool_name="observe"),
            ToolRequest(action_id="a2", tool_name="move_forward", arguments={"ticks": 1}),
            ToolRequest(action_id="a3", tool_name="move_backward", arguments={"ticks": 1}),
            ToolRequest(action_id="a4", tool_name="strafe_left", arguments={"ticks": 1}),
            ToolRequest(action_id="a5", tool_name="strafe_right", arguments={"ticks": 1}),
            ToolRequest(action_id="a6", tool_name="turn_left", arguments={"degrees": 15}),
            ToolRequest(action_id="a7", tool_name="turn_right", arguments={"degrees": 15}),
            ToolRequest(action_id="a8", tool_name="attack", arguments={"ticks": 1}),
            ToolRequest(action_id="a9", tool_name="use", arguments={"ticks": 1}),
            ToolRequest(action_id="a10", tool_name="wait", arguments={"ticks": 1}),
        ]
        for sequence, request in enumerate(requests, 1):
            result = await connector.step(request)
            assert StepResult.model_validate(result.model_dump()) == result
            assert result.status == "succeeded"
            assert result.sequence == sequence
            assert result.observation.last_action_id == request.action_id
            if result.observation.terminal:
                break
    finally:
        await connector.close()


async def test_invalid_arguments_are_rejected_without_charging_a_primitive() -> None:
    connector = DoomConnector()
    try:
        await connector.reset(TRAINING, SEED)
        result = await connector.step(
            ToolRequest(action_id="bad", tool_name="turn_left", arguments={"degrees": 91})
        )
    finally:
        await connector.close()
    assert result.status == "rejected"
    assert result.code == "INVALID_ARGUMENT"
    assert result.primitive_actions_charged == 0


async def test_a_rejected_request_advances_the_public_sequence() -> None:
    connector = DoomConnector()
    try:
        await connector.reset(TRAINING, SEED)
        rejected = await connector.step(ToolRequest(action_id="r1", tool_name="jump"))
        accepted = await connector.step(ToolRequest(action_id="r2", tool_name="observe"))
    finally:
        await connector.close()

    assert rejected.code == "INVALID_TOOL"
    assert rejected.sequence == 1
    assert rejected.observation.sequence == 1
    assert rejected.observation.last_action_id == "r1"
    assert accepted.sequence == 2
    assert accepted.observation.sequence == 2


async def test_an_undeclared_scenario_id_fails_reset() -> None:
    connector = DoomConnector()
    try:
        with pytest.raises(ConnectorError, match="undeclared"):
            await connector.reset("basic.cfg", SEED)
    finally:
        await connector.close()


async def test_reset_on_a_fixed_scenario_and_seed_is_repeatable() -> None:
    connector = DoomConnector()
    try:
        first = await connector.reset(HELDOUT_A, SEED)
        await connector.step(
            ToolRequest(action_id="m1", tool_name="turn_right", arguments={"degrees": 30})
        )
        second = await connector.reset(HELDOUT_A, SEED)
    finally:
        await connector.close()

    assert _without_episode_id(first) == _without_episode_id(second)


async def test_heldout_scenarios_start_away_from_training_under_one_manifest() -> None:
    offsets: dict[str, int] = {}
    manifests = []
    for scenario_id in (TRAINING, HELDOUT_A, HELDOUT_B):
        connector = DoomConnector()
        try:
            manifests.append(await connector.manifest())
            observation = await connector.reset(scenario_id, SEED)
        finally:
            await connector.close()
        assert observation.scenario_id == scenario_id
        assert observation.public_goal == doom.PUBLIC_GOAL
        offsets[scenario_id] = _target(observation)

    assert manifests[0] == manifests[1] == manifests[2]
    assert len(set(offsets.values())) == 3, offsets


async def test_visible_objects_carry_a_bounded_screen_offset_and_omit_the_player() -> None:
    connector = DoomConnector()
    try:
        observation = await connector.reset(TRAINING, SEED)
    finally:
        await connector.close()

    assert observation.visible_objects
    assert all(item.label != "DoomPlayer" for item in observation.visible_objects)
    for item in observation.visible_objects:
        offset = item.properties["screen_offset"]
        assert isinstance(offset, int) and not isinstance(offset, bool)
        assert -100 <= offset <= 100


async def test_screen_offset_is_negative_for_a_target_left_of_center() -> None:
    connector = DoomConnector()
    try:
        start = _target(await connector.reset(TRAINING, CENTERED_SEED))
        turned = await connector.step(
            ToolRequest(action_id="t1", tool_name="turn_right", arguments={"degrees": 15})
        )
    finally:
        await connector.close()

    after = _target(turned.observation)
    assert after < start
    assert after < 0


async def test_public_records_carry_no_private_outcome_or_seed() -> None:
    connector = DoomConnector()
    records: list[str] = []
    try:
        records.append((await connector.reset(TRAINING, SEED)).model_dump_json())
        for index, (tool, arguments) in enumerate(
            [("observe", {}), ("attack", {"ticks": 1}), ("wait", {"ticks": 35})], 1
        ):
            result = await connector.step(
                ToolRequest(action_id=f"p{index}", tool_name=tool, arguments=arguments)
            )
            records.append(result.model_dump_json())
    finally:
        await connector.close()

    for record in records:
        lowered = record.lower()
        assert str(SEED) not in lowered
        for word in PRIVATE_WORDS:
            assert word not in lowered, (word, record)


async def _center_and_fire(connector: DoomConnector) -> None:
    for index in range(40):
        latest = await connector.step(ToolRequest(action_id=f"o{index}", tool_name="observe"))
        if latest.observation.terminal:
            return
        offset = _target(latest.observation)
        if abs(offset) <= 3:
            request = ToolRequest(action_id=f"f{index}", tool_name="attack", arguments={"ticks": 1})
        else:
            degrees = max(1, min(90, round(abs(offset) * 0.45)))
            tool = "turn_right" if offset > 0 else "turn_left"
            request = ToolRequest(
                action_id=f"f{index}", tool_name=tool, arguments={"degrees": degrees}
            )
        if (await connector.step(request)).observation.terminal:
            return
        settled = await connector.step(
            ToolRequest(action_id=f"w{index}", tool_name="wait", arguments={"ticks": 8})
        )
        if settled.observation.terminal:
            return
    raise AssertionError("The scripted center-and-fire sequence did not end the episode.")


async def test_private_outcome_reports_a_kill_after_center_and_fire() -> None:
    connector = DoomConnector()
    try:
        await connector.reset(TRAINING, CENTERED_SEED)
        await _center_and_fire(connector)
        assert await connector.is_terminal()
    finally:
        await connector.close()

    outcome = connector.private_outcome()
    assert outcome.finished is True
    assert outcome.kills == 1
    assert outcome.player_dead is False
    assert outcome.timed_out is False


async def test_private_outcome_reports_zero_kills_after_a_timeout() -> None:
    connector = DoomConnector()
    try:
        await connector.reset(TRAINING, SEED)
        for index in range(120):
            result = await connector.step(
                ToolRequest(action_id=f"w{index}", tool_name="wait", arguments={"ticks": 35})
            )
            if result.observation.terminal:
                break
        assert await connector.is_terminal()
        late = await connector.step(ToolRequest(action_id="late", tool_name="observe"))
        assert late.observation.terminal
    finally:
        await connector.close()

    outcome = connector.private_outcome()
    assert outcome.finished is True
    assert outcome.kills == 0
    assert outcome.timed_out is True


async def test_private_outcome_is_harness_only_and_needs_a_reset() -> None:
    assert not hasattr(GameConnector, "private_outcome")
    with pytest.raises(ConnectorError):
        DoomConnector().private_outcome()


def test_manifest_declares_the_connector_scenarios_and_distinct_precommitted_seeds() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    assert manifest["connector_version"] == doom.CONNECTOR_VERSION
    assert manifest["base_config"] == "basic.cfg"
    scenarios = manifest["scenarios"]
    assert tuple(scenarios) == doom.SCENARIO_IDS
    assert scenarios[TRAINING]["split"] == "training"
    assert len(scenarios[TRAINING]["seeds"]) == 1
    for heldout in (HELDOUT_A, HELDOUT_B):
        assert scenarios[heldout]["split"] == "held-out"
        assert len(scenarios[heldout]["seeds"]) == 3
    seeds = [seed for scenario in scenarios.values() for seed in scenario["seeds"]]
    assert all(isinstance(seed, int) and not isinstance(seed, bool) for seed in seeds)
    assert len(seeds) == len(set(seeds))


async def test_every_precommitted_seed_shows_the_target_at_observation_zero() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    connector = DoomConnector()
    try:
        for scenario_id, scenario in manifest["scenarios"].items():
            for seed in scenario["seeds"]:
                _target(await connector.reset(scenario_id, seed))
    finally:
        await connector.close()


def test_agent_facing_code_never_references_the_doom_scenario_manifest() -> None:
    package = Path("src/noob_agent")
    for name in AGENT_FACING_PACKAGES:
        for source in (package / name).rglob("*.py"):
            text = source.read_text(encoding="utf-8")
            assert "scenarios/doom" not in text, source
            assert "manifest.json" not in text, source


def _without_ids(result: StepResult) -> dict[str, object]:
    dumped = result.model_dump(exclude={"wall_time_ms"})
    dumped["observation"] = _without_episode_id(result.observation)
    return dumped


async def test_a_reused_connector_after_a_finished_episode_matches_a_fresh_one() -> None:
    requests = (
        ToolRequest(action_id="p1", tool_name="turn_left", arguments={"degrees": 10}),
        ToolRequest(action_id="p2", tool_name="attack", arguments={"ticks": 4}),
        ToolRequest(action_id="p3", tool_name="move_forward", arguments={"ticks": 6}),
    )
    reused = DoomConnector()
    fresh = DoomConnector()
    try:
        await reused.reset(TRAINING, CENTERED_SEED)
        await _center_and_fire(reused)
        assert await reused.is_terminal()
        finished_id = (await reused.reset(HELDOUT_B, SEED)).episode_id

        again = await reused.reset(HELDOUT_B, SEED)
        baseline = await fresh.reset(HELDOUT_B, SEED)
        assert again.episode_id != finished_id
        assert _without_episode_id(again) == _without_episode_id(baseline)
        assert reused.private_outcome().kills == 0
        assert reused.private_outcome().finished is False
        for request in requests:
            assert _without_ids(await reused.step(request)) == _without_ids(
                await fresh.step(request)
            )
    finally:
        await reused.close()
        await fresh.close()
