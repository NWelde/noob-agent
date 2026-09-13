"""Contract tests for the non-benchmark live Minecraft loop smoke runner."""

from __future__ import annotations

import asyncio
import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest


def _load_run_script():
    path = Path("scripts/run_minecraft_live_smoke.py")
    spec = importlib.util.spec_from_file_location("minecraft_live_smoke", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_live_smoke_uses_the_requesters_fixed_safety_limits() -> None:
    module = _load_run_script()

    options = module._parse_args(["--live-smoke"])

    assert options.live_smoke is True
    assert options.deadline_seconds == 600.0
    assert options.token_budget == 2_000_000
    assert options.call_budget == 500
    assert options.primitive_budget == 1_000
    assert options.max_repairs == 5
    assert module.LIVE_SMOKE_ACTION_MAX_OUTPUT_TOKENS == 3_000
    assert module._condition_for(options) == "non-benchmark-minecraft-live-smoke"
    assert module._database_for(options, "minecraft-live-smoke-test") == Path(
        ".noob-agent/minecraft-live-smoke-test.sqlite3"
    )


def test_live_smoke_refuses_to_run_without_its_explicit_label(
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_run_script()

    with pytest.raises(SystemExit) as raised:
        module._parse_args([])

    assert raised.value.code == 2
    assert "live-smoke" in capsys.readouterr().err


def test_each_smoke_cycle_hands_evidence_to_the_builder_quickly() -> None:
    module = _load_run_script()

    experiment = module._smoke_experiment(
        experiment_id="minecraft-smoke-s01-training",
        model_id="test-model",
        connector_version="minecraft-0.2.0",
        created_at=datetime(2026, 9, 13, tzinfo=UTC),
    )

    assert experiment.condition == "non-benchmark-minecraft-live-smoke"
    assert experiment.decision_budget == 12
    assert experiment.primitive_budget == 24
    assert experiment.wall_time_budget_ms == 90_000


def test_stop_tracker_ends_a_stale_target_loop_or_no_progress_run() -> None:
    module = _load_run_script()

    stale = module.SmokeStopTracker()
    assert [stale.record(code="NO_VISIBLE_TARGET", state_changed=False) for _ in range(2)] == [
        False,
        True,
    ]

    stalled = module.SmokeStopTracker()
    assert all(
        not stalled.record(code="OK", state_changed=False)
        for _ in range(module.NO_PROGRESS_ACTION_LIMIT - 1)
    )
    assert stalled.record(code="OK", state_changed=False) is True


def test_each_cycle_and_reuse_get_unique_smoke_episode_seeds() -> None:
    module = _load_run_script()

    assert module._cycle_seeds(1) == (20260914, 20260915)
    assert module._cycle_seeds(2) == (20260916, 20260917)


def test_live_smoke_refuses_to_start_without_weave_tracing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_run_script()

    code = module.main(["--live-smoke"], environ={"NOOB_AGENT_TRACE_MODE": "disabled"})

    assert code == 2
    assert "tracing" in capsys.readouterr().err.lower()


def test_live_smoke_refuses_to_start_without_a_configured_model(
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_run_script()

    code = module.main(
        ["--live-smoke"],
        environ={"NOOB_AGENT_TRACE_MODE": "weave", "WEAVE_DISABLED": "false"},
    )

    assert code == 2
    assert "model" in capsys.readouterr().err.lower()


def test_chat_transcript_lines_include_every_model_input_and_output() -> None:
    module = _load_run_script()

    lines = module._chat_transcript_lines(
        purpose="action",
        system="Follow the public controls.",
        prompt="Choose exactly one tool.",
        reasoning="I should inspect the nearby device first.",
        reply='{"tool":"observe","arguments":{"radius":8}}',
    )

    joined = "\n".join(lines)
    assert "system" in joined
    assert "Follow the public controls." in joined
    assert "prompt" in joined
    assert "Choose exactly one tool." in joined
    assert "reasoning" in joined
    assert "inspect the nearby device" in joined
    assert "reply" in joined
    assert '"tool":"observe"' in joined
    assert all(len(line) <= module.MINECRAFT_CHAT_LIMIT for line in lines)


def test_chat_transcript_client_announces_before_and_after_a_model_call() -> None:
    module = _load_run_script()

    class Connector:
        def __init__(self) -> None:
            self.messages: list[str] = []

        async def announce(self, text: str) -> None:
            self.messages.append(text)

    class Client:
        provider = "test"

        async def complete(self, request):
            return module.ModelResponse(
                text='{"tool":"observe","arguments":{"radius":8}}',
                reasoning="I should look around.",
                input_tokens=1,
                output_tokens=1,
                model_id="test-model",
            )

    connector = Connector()
    client = module.MinecraftChatTranscriptClient(Client(), connector, purpose="action")
    asyncio.run(
        client.complete(
            module.ModelRequest(system="System prompt", prompt="User prompt", max_output_tokens=5)
        )
    )

    transcript = "\n".join(connector.messages)
    assert "System prompt" in transcript
    assert "User prompt" in transcript
    assert "I should look around." in transcript
    assert '"tool":"observe"' in transcript
