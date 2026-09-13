"""Contract tests for the non-benchmark Minecraft easy-mode live diagnostic."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from noob_agent.connectors.minecraft import MANIFEST, PUBLIC_GOAL
from noob_agent.domain.model import (
    Observation,
    PublicMessage,
    PublicPlayerState,
    PublicPosition,
    StepResult,
    ToolRequest,
    VisibleObject,
)
from noob_agent.models.client import ModelRequest, ModelResponse
from noob_agent.observability.tracing import NullTraceSink
from noob_agent.prompts.action import ACTION_SYSTEM
from noob_agent.skills.executor import DisabledSkillExecutor
from noob_agent.storage import EpisodeStore

SCRIPT = Path("scripts/run_minecraft_easy_live_smoke.py")
PACK = Path("scenarios/minecraft/easy-button-gate-v1")
FUNCTIONS = PACK / "data/noob_agent_easy/function"


def _load_run_script():
    spec = importlib.util.spec_from_file_location("minecraft_easy_live_smoke", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _function(name: str) -> str:
    return (FUNCTIONS / f"{name}.mcfunction").read_text(encoding="utf-8")


# --- Datapack ----------------------------------------------------------------


def test_easy_pack_is_isolated_from_the_resonator_pack() -> None:
    metadata = json.loads((PACK / "pack.mcmeta").read_text(encoding="utf-8"))
    tick_tag = json.loads(
        (PACK / "data/minecraft/tags/function/tick.json").read_text(encoding="utf-8")
    )

    assert metadata["pack"]["pack_format"] == 48
    assert "non-benchmark" in metadata["pack"]["description"]
    assert tick_tag == {"values": ["noob_agent_easy:tick"]}
    # A load tag would rerun reset on every `/reload` and teleport players.
    assert not (PACK / "data/minecraft/tags/function/load.json").exists()
    assert not (PACK / "data/noob_agent").exists()


def test_easy_reset_restores_the_room_and_grants_durable_night_vision() -> None:
    reset = _function("reset")
    lines = [line for line in reset.splitlines() if line and not line.startswith("#")]

    assert "forceload add 24 -4 34 4" in lines
    assert "clear @a" in lines
    assert "tp @a 27.5 100 0.5 -90 0" in lines
    assert "gamemode adventure @a" in lines
    clear_index = lines.index("effect clear @a")
    night_vision = "effect give @a minecraft:night_vision infinite 0 true"
    assert night_vision in lines
    assert lines.index(night_vision) > clear_index
    # The sealed gateway and its control are rebuilt on every reset.
    assert "fill 31 100 -1 31 102 1 minecraft:iron_bars" in lines
    assert "setblock 30 101 -2 minecraft:stone_button[face=wall,facing=west,powered=false]" in lines


def test_easy_room_has_no_hidden_recipe_container_timer_or_private_state() -> None:
    text = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(FUNCTIONS.rglob("*.mcfunction"))
    )

    for forbidden in ("barrel", "hopper", "chest", "scoreboard", "schedule", "item replace"):
        assert forbidden not in text
    assert 'CustomName:\'{"text":"Gate Button"' in text
    assert 'CustomName:\'{"text":"Iron-Bar Gateway"' in text


def test_pressing_the_button_opens_the_bars_with_public_feedback() -> None:
    tick = _function("tick")
    condition = (
        "execute if block 30 101 -2 minecraft:stone_button[powered=true] "
        "if block 31 101 0 minecraft:iron_bars run "
    )

    assert f"{condition}fill 31 100 -1 31 102 1 minecraft:air" in tick
    assert condition + "title @a[x=24,y=99,z=-4,dx=10,dy=5,dz=8] actionbar" in tick
    assert "The iron-bar gateway opens." in tick
    # The sidecar records action-bar feedback, so a duplicate chat copy would
    # show the model the same message twice.
    assert "tellraw" not in tick
    assert tick.count("The iron-bar gateway opens.") == 1
    # The message is issued before the bars disappear, so it fires exactly once.
    assert tick.index("actionbar") < tick.index("minecraft:air")


def test_button_and_whole_gateway_are_reachable_and_observable_from_the_start() -> None:
    start = (27.5, 100.0, 0.5)
    floored = (27, 100, 0)
    button = (30, 101, -2)
    bars = [(31, y, z) for y in (100, 101, 102) for z in (-1, 0, 1)]

    # Sidecar reach is 5 blocks from the feet; default observe radius is 5.
    assert sum((b - s) ** 2 for b, s in zip(button, start, strict=True)) ** 0.5 <= 5
    for block in (button, *bars):
        assert sum((b - f) ** 2 for b, f in zip(block, floored, strict=True)) <= 25


def test_easy_room_is_outside_the_resonator_room_and_its_observation_radius() -> None:
    reset = _function("reset")
    xs = [int(value) for value in re.findall(r"(?:fill|setblock) (-?\d+) ", reset)]
    resonator_max_player_x = 6
    max_observe_radius = 8

    assert min(xs) > resonator_max_player_x + max_observe_radius
    # Nothing in the easy pack calls into, or clears timers of, the Resonator pack.
    assert "noob_agent:" not in reset
    assert "noob_agent:" not in _function("tick")


# --- Runner options and labels ------------------------------------------------


def test_easy_runner_requires_its_own_explicit_flag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_run_script()

    with pytest.raises(SystemExit) as missing:
        module._parse_args([])
    assert missing.value.code == 2
    assert "live-easy-smoke" in capsys.readouterr().err

    with pytest.raises(SystemExit) as wrong:
        module._parse_args(["--live-smoke"])
    assert wrong.value.code == 2

    assert module._parse_args(["--live-easy-smoke"]).live_easy_smoke is True


def test_easy_runner_uses_an_isolated_non_benchmark_scenario_and_condition() -> None:
    module = _load_run_script()

    assert module.SCENARIO_ID == "easy-button-gate-v1"
    assert module.SCENARIO_ID != "resonator-training-v1"
    assert module.CONDITION == "non-benchmark-minecraft-easy-diagnostic"
    assert module.RESET_COMMANDS == ("function noob_agent_easy:reset",)
    settings = module._easy_settings()
    assert settings.scenario_reset_commands == ("function noob_agent_easy:reset",)
    run_id = module._default_run_id(datetime(2026, 9, 13, 12, 0, tzinfo=UTC))
    assert run_id.startswith("non-benchmark-minecraft-easy-")
    options = module._parse_args(["--live-easy-smoke"])
    assert "non-benchmark" in str(module._database_for(options, run_id))


def test_easy_runner_is_generous_and_has_no_wall_clock_deadline() -> None:
    module = _load_run_script()

    assert module.DEADLINE_SECONDS is None
    assert module.ACTION_MAX_OUTPUT_TOKENS >= 32_000
    assert module.BUILDER_MAX_OUTPUT_TOKENS >= 100_000
    assert module.EPISODE_DECISION_BUDGET >= 500
    assert module.EPISODE_PRIMITIVE_BUDGET >= 1_000
    assert module.RUN_CALL_BUDGET >= 2_000
    assert module.RUN_TOKEN_BUDGET >= 50_000_000
    experiment = module._easy_experiment(
        experiment_id="non-benchmark-minecraft-easy-x-s01-training",
        model_id="test-model",
        connector_version="minecraft-0.2.0",
        created_at=datetime(2026, 9, 13, tzinfo=UTC),
    )
    assert experiment.condition == "non-benchmark-minecraft-easy-diagnostic"
    assert experiment.decision_budget == module.EPISODE_DECISION_BUDGET
    assert experiment.primitive_budget == module.EPISODE_PRIMITIVE_BUDGET
    # Roughly thirty years: the record needs a positive value, not a real limit.
    assert experiment.wall_time_budget_ms >= 10**12


def test_easy_runner_does_not_change_the_frozen_manifest_or_normal_goal() -> None:
    module = _load_run_script()

    assert module.EASY_PUBLIC_GOAL != PUBLIC_GOAL
    assert "button" in module.EASY_PUBLIC_GOAL.lower()
    assert "iron bars" in module.EASY_PUBLIC_GOAL.lower()
    connector = module.EasyGoalConnector(_FakeMinecraft())
    assert asyncio.run(connector.manifest()) == MANIFEST
    assert "announce" not in {tool.name for tool in MANIFEST.tools}


def test_easy_runner_reuses_the_existing_chat_transcript_mechanism() -> None:
    module = _load_run_script()

    assert module.MinecraftChatTranscriptClient.__module__ == "run_minecraft_live_smoke"


def test_easy_runner_refuses_without_tracing_or_a_model(
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_run_script()

    assert module.main(["--live-easy-smoke"], environ={"NOOB_AGENT_TRACE_MODE": "disabled"}) == 2
    assert "tracing" in capsys.readouterr().err.lower()
    code = module.main(
        ["--live-easy-smoke"],
        environ={"NOOB_AGENT_TRACE_MODE": "weave", "WEAVE_DISABLED": "false"},
    )
    assert code == 2
    assert "model" in capsys.readouterr().err.lower()


# --- Public goal and success detection ---------------------------------------


def _observation(*, sequence: int, messages: tuple[str, ...] = ()) -> Observation:
    return Observation(
        episode_id="mc-easy-button-gate-v1-s1-r001",
        sequence=sequence,
        game_id="minecraft",
        scenario_id="easy-button-gate-v1",
        public_goal=PUBLIC_GOAL,
        player=PublicPlayerState(position=PublicPosition(x=27.5, y=100, z=0.5)),
        visible_objects=(
            VisibleObject(
                object_id="obj_0001",
                label="Stone Button",
                position=PublicPosition(x=30, y=101, z=-2),
                distance=3.7,
                properties={"kind": "block"},
            ),
        ),
        messages=tuple(PublicMessage(kind="game", text=text) for text in messages),
        terminal=False,
        logical_time=sequence,
    )


class _FakeMinecraft:
    def __init__(self) -> None:
        self.announcements: list[str] = []
        self.resets: list[tuple[str, int]] = []
        self.closed = False
        self._sequence = 0

    async def manifest(self):
        return MANIFEST

    async def reset(self, scenario_id: str, seed: int) -> Observation:
        self.resets.append((scenario_id, seed))
        self._sequence = 0
        return _observation(sequence=0)

    async def step(self, request: ToolRequest) -> StepResult:
        self._sequence += 1
        opened = request.tool_name == "use_object"
        return StepResult(
            action_id=request.action_id,
            sequence=self._sequence,
            status="succeeded",
            code="OK",
            message="Done.",
            observation=_observation(
                sequence=self._sequence,
                messages=("The iron-bar gateway opens.",) if opened else (),
            ),
            state_changed=opened,
            primitive_actions_charged=1,
            logical_duration=5,
            wall_time_ms=1,
        )

    async def is_terminal(self) -> bool:
        return False

    async def close(self) -> None:
        self.closed = True

    async def announce(self, text: str) -> None:
        self.announcements.append(text)


def test_reset_shows_the_easy_goal_and_is_not_terminal() -> None:
    module = _load_run_script()
    connector = module.EasyGoalConnector(_FakeMinecraft())

    observation = asyncio.run(connector.reset("easy-button-gate-v1", 1))

    assert observation.public_goal == module.EASY_PUBLIC_GOAL
    assert observation.terminal is False


def test_only_the_public_gateway_message_ends_the_episode_as_success() -> None:
    module = _load_run_script()
    inner = _FakeMinecraft()
    connector = module.EasyGoalConnector(inner)

    async def scenario() -> tuple[StepResult, StepResult]:
        await connector.reset("easy-button-gate-v1", 1)
        looked = await connector.step(ToolRequest(action_id="a_0001", tool_name="observe"))
        pressed = await connector.step(
            ToolRequest(
                action_id="a_0002", tool_name="use_object", arguments={"object_id": "obj_0001"}
            )
        )
        return looked, pressed

    looked, pressed = asyncio.run(scenario())

    assert looked.observation.terminal is False
    assert looked.observation.public_goal == module.EASY_PUBLIC_GOAL
    assert pressed.observation.terminal is True
    assert pressed.observation.terminal_reason == "gateway_open"
    assert pressed.observation.public_goal == module.EASY_PUBLIC_GOAL


# --- End-to-end orchestration with fakes -------------------------------------


class _ScriptedModel:
    provider = "test"

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if request.system == ACTION_SYSTEM:
            text = json.dumps(
                {
                    "subgoal": "Press the gate button.",
                    "expected_evidence": "The iron bars open.",
                    "tool": "use_object",
                    "arguments": {"object_id": "obj_0001"},
                }
            )
            return ModelResponse(
                text=text,
                reasoning="The button is right in front of me.",
                input_tokens=10,
                output_tokens=5,
                model_id="test-model",
            )
        return ModelResponse(
            text="I will not write a skill.",
            reasoning="Builder thinking.",
            input_tokens=20,
            output_tokens=7,
            model_id="test-model",
        )


def test_easy_run_narrates_concisely_and_preserves_full_records(tmp_path: Path) -> None:
    module = _load_run_script()
    fakes: list[_FakeMinecraft] = []

    def factory() -> _FakeMinecraft:
        fakes.append(_FakeMinecraft())
        return fakes[-1]

    model = _ScriptedModel()
    run_id = "non-benchmark-minecraft-easy-test"
    database = tmp_path / f"{run_id}.sqlite3"
    with EpisodeStore.open(database) as store:
        summary = asyncio.run(
            module._run_easy(
                store=store,
                client=module.EasyBudgetedClient(model, store, run_id),
                model_id="test-model",
                executor=DisabledSkillExecutor(),
                trace=NullTraceSink(),
                run_id=run_id,
                connector_factory=factory,
            )
        )
        calls = module._records_for_run(store, run_id)

    assert fakes[0].resets == [("easy-button-gate-v1", module.SEED)]
    assert summary["training"]["stop_reason"] == "terminal_state"
    assert summary["training"]["decisions_used"] == 1
    assert summary["training"]["primitives_used"] == 1
    assert summary["builder"]["accepted"] is False
    assert summary["reuse"] is None
    assert len(calls) == 2
    assert {request.max_output_tokens for request in model.requests} == {
        module.ACTION_MAX_OUTPUT_TOKENS,
        module.BUILDER_MAX_OUTPUT_TOKENS,
    }

    chat = "\n".join(fakes[0].announcements)
    assert "[noob:Pressing]" in chat
    assert "[noob:Learning]" in chat
    assert "The button is right in front of me." not in chat
    assert "Builder thinking." not in chat
    assert {call.reasoning for call in calls} == {
        "The button is right in front of me.",
        "Builder thinking.",
    }


def test_budgeted_client_stops_before_exceeding_the_generous_run_budget(tmp_path: Path) -> None:
    module = _load_run_script()
    model = _ScriptedModel()

    with EpisodeStore.open(tmp_path / "budget.sqlite3") as store:
        client = module.EasyBudgetedClient(model, store, "run", call_budget=0)
        with pytest.raises(module.EasyBudgetExhausted):
            asyncio.run(client.complete(ModelRequest(system="s", prompt="p", max_output_tokens=1)))

    assert model.requests == []


def test_interrupt_is_reported_rather_than_crashing_the_summary() -> None:
    module = _load_run_script()

    async def interrupted() -> None:
        raise KeyboardInterrupt

    async def exhausted() -> None:
        raise module.EasyBudgetExhausted("spent")

    async def finished() -> None:
        return None

    assert module._drive(interrupted) == "interrupted"
    assert module._drive(exhausted) == "budget_exhausted"
    assert module._drive(finished) == "completed"


def test_compact_summary_is_labelled_and_names_every_required_field() -> None:
    module = _load_run_script()

    text = "\n".join(
        module._summary_lines(
            {
                "run_kind": module.CONDITION,
                "scenario_id": module.SCENARIO_ID,
                "status": "completed",
                "database": ".noob-agent/non-benchmark-minecraft-easy-x.sqlite3",
                "calls_used": 2,
                "tokens_used": 42,
                "sequence": {
                    "training": {
                        "stop_reason": "terminal_state",
                        "decisions_used": 1,
                        "primitives_used": 1,
                    },
                    "builder": {"accepted": True, "skill": "press_gate_button@1"},
                    "reuse": None,
                },
            }
        )
    )

    for expected in (
        "non-benchmark",
        "easy-button-gate-v1",
        "terminal_state",
        "decisions",
        "primitives",
        "model calls",
        "tokens",
        "press_gate_button@1",
        ".noob-agent/non-benchmark-minecraft-easy-x.sqlite3",
    ):
        assert expected in text
    assert "held-out" not in text.lower()


class _InterruptedModel(_ScriptedModel):
    """Answers once, then behaves like Ctrl-C arriving during the next call."""

    async def complete(self, request: ModelRequest) -> ModelResponse:
        if self.requests:
            raise asyncio.CancelledError
        self.requests.append(request)
        return ModelResponse(
            text='{"tool":"observe","arguments":{"radius":5}}',
            input_tokens=10,
            output_tokens=5,
            model_id="test-model",
        )


def test_interrupted_episode_still_reports_its_durable_progress(tmp_path: Path) -> None:
    module = _load_run_script()
    fakes: list[_FakeMinecraft] = []

    def factory() -> _FakeMinecraft:
        fakes.append(_FakeMinecraft())
        return fakes[-1]

    run_id = "non-benchmark-minecraft-easy-interrupt"
    summary: dict[str, object] = {}
    with EpisodeStore.open(tmp_path / f"{run_id}.sqlite3") as store:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(
                module._run_easy(
                    store=store,
                    client=_InterruptedModel(),
                    model_id="test-model",
                    executor=DisabledSkillExecutor(),
                    trace=NullTraceSink(),
                    run_id=run_id,
                    connector_factory=factory,
                    summary=summary,
                )
            )

    assert summary["training"]["stop_reason"] == "unknown_result"
    assert summary["training"]["decisions_used"] == 1
    assert summary["training"]["primitives_used"] == 1
    assert fakes[0].closed is True


def test_echoed_chat_transcript_never_reaches_the_model_observation() -> None:
    module = _load_run_script()

    class EchoingMinecraft(_FakeMinecraft):
        async def reset(self, scenario_id: str, seed: int) -> Observation:
            return _observation(
                sequence=0,
                messages=(
                    "<noobagentbot> [noob:action reasoning 1/2] I should press it.",
                    "[noob:builder reply 1/1] raw form",
                    "The iron-bar gateway opens.",
                    "<nathanbeyene> hello [noob: is fine mid-sentence",
                ),
            )

    observation = asyncio.run(module.EasyGoalConnector(EchoingMinecraft()).reset("x", 1))

    assert [message.text for message in observation.messages] == [
        "The iron-bar gateway opens.",
        "<nathanbeyene> hello [noob: is fine mid-sentence",
    ]
    assert observation.terminal is True
