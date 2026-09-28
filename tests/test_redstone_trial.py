"""Durable evidence must exist even when setup cannot reach Minecraft."""

import json
from pathlib import Path

import pytest

from noob_agent.redstone.rcon import RconError
from noob_agent.redstone.trial import TrialManifest, can_auto_continue, run_preflight


def test_unique_manifests_start_incomplete_and_journal_pending_attempt(tmp_path: Path) -> None:
    first = TrialManifest(tmp_path)
    second = TrialManifest(tmp_path)
    assert first.path != second.path
    first.attempt("trusted_probe", {"command": "seed"})
    saved = json.loads(first.path.read_text())
    assert saved["completion"]["status"] == "incomplete"
    assert saved["events"][0]["outcome"] == "unknown"
    assert saved["events"][0]["result"] is None
    assert saved["recording"]["status"] == "missing"


def test_manifest_can_be_reopened_as_a_new_session(tmp_path: Path) -> None:
    first = TrialManifest(tmp_path)
    first.data.update(kind="connected_trial", configuration={"task": "lamp_repair"})
    first.data["continuation"] = {"ready": True, "state": {"history": []}}
    first.save()

    resumed = TrialManifest.reopen(first.path)

    assert resumed.path == first.path
    assert len(resumed.data["sessions"]) == 1
    assert resumed.data["sessions"][0]["number"] == 1
    assert resumed.data["continuation"]["state"] == {"history": []}
    assert resumed.data["completion"]["status"] == "incomplete"


def test_only_a_current_task_limit_triggers_automatic_continuation() -> None:
    evidence = {
        "continuation": {
            "ready": True,
            "session_number": 1,
            "stop_reason": "LoopLimit",
        },
        "sessions": [{"number": 1}],
        "loop": {"status": "stopped"},
    }

    assert can_auto_continue(evidence, "lamp_repair") is True
    assert can_auto_continue(evidence, "computer") is True
    stale = {**evidence, "continuation": {**evidence["continuation"], "session_number": 0}}
    assert can_auto_continue(stale, "lamp_repair") is False
    exhausted = {
        **evidence,
        "continuation": {**evidence["continuation"], "session_number": 4},
        "sessions": [{"number": number} for number in range(1, 5)],
    }
    assert can_auto_continue(exhausted, "lamp_repair") is False


def test_computer_continuation_rechecks_player_and_touched_blocks(tmp_path: Path) -> None:
    from noob_agent.redstone.reset import BASELINE
    from noob_agent.redstone.trial import verify_continuation

    manifest = TrialManifest(tmp_path)
    manifest.data["continuation"] = {"state": {}}
    manifest.data["events"].append(
        {
            "kind": "bounded_action",
            "outcome": "observed",
            "request": {
                "action": "place",
                "position": [0, 64, 0],
                "block": "minecraft:stone",
                "properties": None,
            },
            "result": {
                "after": {"name": "minecraft:stone", "properties": {}},
                "effect_verified": True,
            },
        }
    )

    class Actions:
        observed = []

        def read(self, request):
            assert request == {"op": "player"}
            return {
                "username": "noobagentbot",
                "gameMode": "creative",
                "dimension": "overworld",
                "orientationUnits": "radians",
                "position": [48.5, 64, 98.5],
                "yaw": 0,
                "pitch": 0,
                "inventory": BASELINE["inventory"],
            }

        def observe(self, position):
            self.observed.append(position)
            return {"name": "minecraft:stone", "properties": {}, "position": position}

    actions = Actions()
    result = verify_continuation(manifest, "computer", actions=actions)

    assert result["resume_check"]["blocks_checked"] == 1
    assert actions.observed == [[0, 64, 0]]

    actions.observed.clear()
    actions.observe = lambda position: {
        "name": "minecraft:air",
        "properties": {},
        "position": position,
    }
    result = verify_continuation(manifest, "computer", actions=actions)
    assert result["resume_check"]["mismatches"] == [
        {
            "position": [0, 64, 0],
            "expected_states": [{"name": "minecraft:stone", "properties": {}}],
            "actual": {"name": "minecraft:air", "properties": {}, "position": [0, 64, 0]},
        }
    ]
    assert manifest.data["continuation"]["state"]["world_states"] == [
        {"name": "minecraft:air", "properties": {}, "position": [0, 64, 0]}
    ]


def test_failure_before_connection_leaves_explicit_incomplete_evidence(tmp_path: Path) -> None:
    def unavailable() -> None:
        raise RconError("fixture unavailable")

    manifest = run_preflight(tmp_path, connect=unavailable)
    saved = json.loads(manifest.path.read_text())
    assert saved["completion"]["status"] == "incomplete"
    assert saved["errors"] == [{"stage": "connect", "type": "RconError"}]
    assert saved["contract"]["sha256"]
    assert saved["limits"]["primitive_actions"] == 20000
    assert saved["initial_conditions"]["verified"] is False
    assert saved["template"]["verified"] is False
    assert saved["completion"]["ended_at"]


def test_unexpected_exception_text_is_never_persisted(tmp_path: Path) -> None:
    def unavailable() -> None:
        raise RuntimeError("password=do-not-log-this")

    manifest = run_preflight(tmp_path, connect=unavailable)
    assert "do-not-log-this" not in manifest.path.read_text()


@pytest.mark.parametrize("close_fails", [False, True])
def test_lost_probe_remains_unknown_and_close_failure_is_recorded(
    tmp_path: Path, close_fails: bool
) -> None:
    class Disconnected:
        closed = False

        def command(self, command: str) -> str:
            # Inspect on-disk intent before responding to the first command.
            saved = json.loads(next(tmp_path.glob("*/manifest.json")).read_text())
            assert saved["events"][0]["request"] == {"command": command}
            assert saved["events"][0]["outcome"] == "unknown"
            raise RconError("lost connection")

        def close(self) -> None:
            self.closed = True
            if close_fails:
                raise RuntimeError("secret-bearing third-party error")

    transport = Disconnected()
    manifest = run_preflight(tmp_path, connect=lambda: transport)
    saved = json.loads(manifest.path.read_text())
    assert transport.closed
    assert saved["events"][0]["outcome"] == "unknown"
    assert saved["completion"]["status"] == "incomplete"
    assert saved["errors"][0] == {"stage": "probe", "type": "RconError"}
    if close_fails:
        assert saved["errors"][1] == {"stage": "close", "type": "RuntimeError"}
        assert "secret-bearing" not in manifest.path.read_text()


def test_interrupted_preflight_preserves_cause_and_pending_probe(tmp_path: Path) -> None:
    class Interrupted:
        def command(self, command: str) -> str:
            raise KeyboardInterrupt

        def close(self) -> None:
            pass

    with pytest.raises(KeyboardInterrupt):
        run_preflight(tmp_path, connect=Interrupted)
    saved = json.loads(next(tmp_path.glob("*/manifest.json")).read_text())
    assert saved["errors"] == [{"stage": "probe", "type": "KeyboardInterrupt"}]
    assert saved["events"][0]["outcome"] == "unknown"
    assert saved["completion"]["status"] == "incomplete"
