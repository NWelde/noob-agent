"""Offline subprocess boundary checks; no provider or credentials used."""

import sys

import pytest

from noob_agent.redstone.jev import JevError, JevSubprocess, action_request, selected_action


def test_request_and_exact_choice_preserve_metadata():
    request = action_request({"observed": "air"}, {"place-1": "Place one stone"})
    assert request["questions"]["action"] == {
        "type": "choice",
        "instructions": "Choose exactly one offered bounded action.",
        "criteria": {"place-1": "Place one stone"},
    }
    response = {
        "answers": {"action": {"choice": "place-1"}},
        "usage": {"totalTokens": 7},
        "response": {"modelId": "fixture/jev"},
    }
    assert selected_action(response, request) == "place-1"
    assert response["usage"]["totalTokens"] == 7


@pytest.mark.parametrize("choice", ["unknown", 1, True, None, ["place-1"]])
def test_invalid_choice_is_rejected(choice):
    with pytest.raises(JevError):
        selected_action(
            {"answers": {"action": {"choice": choice}}}, action_request({}, {"place-1": "Place"})
        )


@pytest.mark.parametrize("mode", ["failure", "malformed", "timeout", "overflow"])
def test_subprocess_failures_are_bounded_and_sanitized(mode):
    adapter = JevSubprocess(
        command=[sys.executable, "tests/fixtures/redstone/jev_process.py", mode]
    )
    with pytest.raises(JevError) as caught:
        adapter.evaluate(action_request({}, {"place-1": "Place"}), timeout=0.2)
    assert "secret-fixture" not in str(caught.value)


def test_real_subprocess_fixture_retains_identity_and_usage():
    adapter = JevSubprocess(
        command=[sys.executable, "tests/fixtures/redstone/jev_process.py", "ok"]
    )
    result = adapter.evaluate(action_request({}, {"place-1": "Place"}), timeout=2)
    assert result["response"]["modelId"] == "fixture/jev"
    assert result["usage"] == {"totalTokens": 7}


def test_timeout_reaps_process_and_never_captures_stderr(monkeypatch):
    import subprocess
    import time

    processes = []
    original = subprocess.Popen

    def launch(*args, **kwargs):
        assert kwargs["stderr"] == subprocess.DEVNULL
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr("noob_agent.redstone.jev.subprocess.Popen", launch)
    adapter = JevSubprocess(
        command=[sys.executable, "tests/fixtures/redstone/jev_process.py", "timeout"]
    )
    started = time.monotonic()
    with pytest.raises(JevError):
        adapter.evaluate(action_request({}, {"place-1": "Place"}), timeout=0.1)
    assert time.monotonic() - started < 2
    assert len(processes) == 1
    assert processes[0].poll() is not None


def test_oversized_request_never_dispatches(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("oversized request dispatched")

    monkeypatch.setattr("noob_agent.redstone.jev.subprocess.Popen", forbidden)
    with pytest.raises(JevError):
        JevSubprocess().evaluate(action_request({"large": "x" * 300000}, {"a": "Place"}))
