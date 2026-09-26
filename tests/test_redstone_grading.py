"""Trusted control behavior; no circuit or provider success fixtures."""

import pytest
from test_redstone_modules import declaration

from noob_agent.redstone.actions import ActionLimit, Actions
from noob_agent.redstone.grading import GraderControl
from noob_agent.redstone.modules import validate_declaration
from noob_agent.redstone.trial import TrialManifest


class Server:
    def __init__(self):
        self.commands = []
        self.fail = False

    def command(self, command):
        self.commands.append(command)
        if self.fail:
            raise TimeoutError("private transport detail")
        return (
            "Test passed, count: 1"
            if "minecraft:redstone_wire[power=0]" in command
            else "Test failed"
        )

    def request(self, request):
        pytest.fail("Trusted probes must not rely on client chunk visibility")


def harness(tmp_path, maximum=100):
    server = Server()
    actions = Actions(TrialManifest(tmp_path), server, server, max_actions=maximum)
    value = declaration()
    value["probes"]["a"][0]["position"] = [95, 95, 95]
    return GraderControl(actions, validate_declaration(value, actions.contract)), actions, server


def test_distant_declared_probe_reads_server_and_charges(tmp_path):
    grader, actions, server = harness(tmp_path)
    assert grader.probe("a", 0)["properties"] == {"power": 0}
    assert "95 95 95" in server.commands[0]
    assert actions.used == 1
    assert actions.manifest.data["grading_budget"]["operations"] == 1


def test_invalid_control_and_timing_attempts_retained_without_commands(tmp_path):
    grader, actions, server = harness(tmp_path)
    for operation in [
        lambda: grader.set_control("missing", True),
        lambda: grader.set_control("step", True),
        lambda: grader.wait_ticks(201),
        lambda: grader.wait_ticks(True),
    ]:
        with pytest.raises(ValueError):
            operation()
    assert not server.commands
    assert actions.used == 4
    assert len(actions.manifest.data["errors"]) == 4


def test_shared_runtime_budget_cannot_be_bypassed(tmp_path):
    grader, actions, server = harness(tmp_path, 1)
    grader.probe("a", 0)
    count = len(server.commands)
    with pytest.raises(ActionLimit):
        grader.probe("a", 1)
    assert len(server.commands) == count


def test_uncertain_failure_stops_runtime_and_preserves_attempt(tmp_path):
    grader, actions, server = harness(tmp_path)
    server.fail = True
    with pytest.raises(RuntimeError):
        grader.probe("a", 0)
    assert actions.stopped
    assert actions.manifest.data["events"][-1]["outcome"] == "unknown"
    assert "private transport detail" not in actions.manifest.path.read_text()


def test_no_privileged_operations_on_runtime_sidecar():
    from noob_agent.redstone.sidecar import Sidecar

    sidecar = object.__new__(Sidecar)
    sidecar.process = object()
    for op in ("grader", "pulse", "set_control", "wait_ticks", "command"):
        with pytest.raises(ValueError):
            sidecar.request({"op": op})


class TimingServer(Server):
    def __init__(self, elapsed=2):
        super().__init__()
        self.elapsed = elapsed

    def command(self, command):
        self.commands.append(command)
        if command.startswith("execute if block"):
            return (
                "Test passed, count: 1"
                if "face=floor,facing=north,powered=false" in command
                else "Test failed"
            )
        if command.startswith("scoreboard players get"):
            _, _, _, name, objective = command.split()
            if name == "done":
                value = 1
            elif name.startswith("s"):
                offset = int(name[1:])
                value = 100 + offset + (self.elapsed - 2 if offset >= 2 else 0)
            elif name.startswith("x"):
                value = 1
            elif name.startswith("b"):
                value = 0
            else:
                raise AssertionError(f"Unexpected timing score: {name}")
            return f"{name} has {value} [{objective}]"
        return "OK"


def timing_harness(tmp_path, monkeypatch, elapsed=2):
    import noob_agent.redstone.grading as module

    monkeypatch.setattr(module, "SERVER_DIRECTORY", tmp_path / "server")
    server = TimingServer(elapsed)
    actions = Actions(TrialManifest(tmp_path / "runs"), server, server)
    return GraderControl(actions, validate_declaration(declaration(), actions.contract)), actions


def test_step_uses_server_schedule_and_retains_actual_tick_evidence(tmp_path, monkeypatch):
    grader, actions = timing_harness(tmp_path, monkeypatch)
    result = grader.pulse_step()
    assert result == {"start": 100, "end": 102, "elapsed_server_ticks": 2, "on": 1, "off": 1}
    generated = list(actions.manifest.path.parent.rglob("t0.mcfunction"))[0].read_text()
    assert f"schedule function {grader.namespace}:t2 2t replace" in generated
    assert "time query gametime" in generated
    assert "minecraft:lever[face=floor,facing=north,powered=true]" in generated
    pulse_end = list(actions.manifest.path.parent.rglob("t2.mcfunction"))[0].read_text()
    assert "minecraft:lever[face=floor,facing=north,powered=false]" in pulse_end
    assert grader.ticks == 2
    assert actions.used == 1


def test_wrong_server_tick_delta_stops_without_claiming_exactness(tmp_path, monkeypatch):
    grader, actions = timing_harness(tmp_path, monkeypatch, 3)
    with pytest.raises(RuntimeError, match="interval mismatch"):
        grader.pulse_step()
    assert actions.stopped
    assert actions.manifest.data["events"][0]["outcome"] == "unknown"
    assert any("103" in str(e["result"]) for e in actions.manifest.data["events"])


def test_tick_budget_rejects_before_install_or_schedule(tmp_path, monkeypatch):
    grader, actions = timing_harness(tmp_path, monkeypatch)
    grader.max_ticks = 1
    with pytest.raises(ActionLimit, match="budget exhausted"):
        grader.pulse_step()
    assert not list(tmp_path.rglob("*.mcfunction"))
    assert actions.used == 1


def test_nonlever_control_rejected_without_world_mutation(tmp_path):
    grader, actions, server = harness(tmp_path)
    with pytest.raises(ValueError, match="declared lever missing or mismatched"):
        grader.set_control("reset", True)
    assert all(command.startswith("execute if block") for command in server.commands)
    assert actions.used == 1


def test_actual_dedicated_server_predicate_reply(tmp_path):
    grader, actions, server = harness(tmp_path)
    server.command = lambda command: "Test passed"
    assert grader.probe("a", 0)["properties"]["power"] == 0


def test_new_grader_cannot_replenish_manifest_tick_budget(tmp_path, monkeypatch):
    grader, actions = timing_harness(tmp_path, monkeypatch)
    grader.pulse_step()
    second = GraderControl(actions, grader.declaration)
    assert second.ticks == 2


def test_mutated_declaration_is_revalidated_before_server_access(tmp_path):
    grader, actions, server = harness(tmp_path)
    grader.declaration.probes["a"][0].position[0] = 96
    with pytest.raises(ValueError):
        grader.probe("a", 0)
    assert not server.commands


def test_runtime_guard_blocks_grader_before_any_command(tmp_path):
    grader, actions, server = harness(tmp_path)

    def deny(charge):
        raise ActionLimit("Intention expired")

    actions.guard = deny
    with pytest.raises(ActionLimit):
        grader.set_control("reset", True)
    assert server.commands == []


def test_command_budget_is_finite_and_stops_before_delivery(tmp_path):
    grader, actions, server = harness(tmp_path)
    actions.manifest.data["grading_budget"]["commands"] = 4096
    with pytest.raises(ActionLimit, match="command budget"):
        grader.probe("a", 0)
    assert not server.commands
    assert actions.stopped


def test_unknown_predicate_reply_never_becomes_false_signal(tmp_path):
    grader, actions, server = harness(tmp_path)
    server.command = lambda command: "That position is not loaded"
    with pytest.raises(RuntimeError, match="predicate response"):
        grader.probe("a", 0)
    assert actions.manifest.data["events"][0]["outcome"] == "unknown"


def test_budget_denied_attempt_is_retained(tmp_path):
    grader, actions, server = harness(tmp_path, 1)
    grader.probe("a", 0)
    with pytest.raises(ActionLimit):
        grader.probe("a", 1)
    event = actions.manifest.data["events"][-1]
    assert event["kind"] == "grader_rejected"
    assert event["request"] == {"op": "probe", "role": "a", "index": 1}


def test_timeline_rejects_untrusted_recipes_before_server_access(tmp_path):
    grader, actions, server = harness(tmp_path)
    invalid = [
        [{"control": "missing", "level": True}],
        [{"control": "step", "level": True}],
        [{"control": "reset", "level": 1}],
        [{"wait": True}],
        [{"wait": 201}],
        [{"command": "say injected"}],
        [{"wait": 1, "control": "reset", "level": False}],
    ]
    for recipe in invalid:
        with pytest.raises(ValueError):
            grader.timeline(recipe, cycles=1)
    assert not server.commands
    assert actions.used == len(invalid)


def test_timeline_samples_observed_bits_and_chains_exact_cycles(tmp_path, monkeypatch):
    grader, actions = timing_harness(tmp_path, monkeypatch)
    captured = {}

    def execute(functions, duration, scores):
        captured.update(functions=functions, duration=duration, scores=scores)
        return {name: (15 if name.startswith("b") else 0) for name in scores}

    monkeypatch.setattr(grader, "_execute_timeline", execute)
    result = grader.timeline([{"wait": 4}], cycles=2)
    assert captured["duration"] == 404
    assert grader.ticks == 404
    assert actions.used == 1
    assert len(result["snapshots"]) == 4
    assert result["snapshots"][0]["signals"]["a"] == [15, 15, 15, 15]
    functions = captured["functions"]
    assert "198t replace" in functions["t6"]
    boundary = functions["t204"]
    assert boundary.index("run time query gametime") < boundary.index("powered=true")
    assert "minecraft:redstone_wire[power=15]" in boundary
    assert "expected" not in "".join(functions.values())


def test_timeline_budget_is_aggregate_across_calls(tmp_path, monkeypatch):
    grader, actions = timing_harness(tmp_path, monkeypatch)
    monkeypatch.setattr(grader, "_execute_timeline", lambda f, d, s: dict.fromkeys(s, 0))
    grader.timeline([], cycles=8)
    second = GraderControl(actions, grader.declaration)
    with pytest.raises(ActionLimit):
        second.timeline([], cycles=1)
    assert second.ticks == 1600


def test_timeline_requires_explicit_bounded_cycle_count(tmp_path):
    grader, _, server = harness(tmp_path)
    for count in (True, 0, 9, 1.0):
        with pytest.raises(ValueError):
            grader.timeline([], cycles=count)
    assert server.commands == []


@pytest.mark.parametrize("fault", [None, "timing", "probe", "control"])
def test_timeline_execution_checks_evidence_and_cleans_on_failure(tmp_path, monkeypatch, fault):
    grader, actions = timing_harness(tmp_path, monkeypatch)
    server = actions.transport
    original = server.command

    def command(text):
        if text.startswith("scoreboard players get"):
            _, _, _, name, objective = text.split()
            value = 1
            if name.startswith("s"):
                value = 100 + int(name[1:]) + (1 if fault == "timing" and name == "s200" else 0)
            if name.startswith("b"):
                value = -1 if fault == "probe" else 0
            if name.startswith("x"):
                value = 0 if fault == "control" else 1
            return f"{name} has {value} [{objective}]"
        return original(text)

    server.command = command
    if fault:
        with pytest.raises((ValueError, RuntimeError)):
            grader.timeline([], cycles=1)
        assert actions.stopped is (fault != "probe")
    else:
        result = grader.timeline([], cycles=1)
        assert result["timestamps"] == {"s0": 100, "s2": 102, "s200": 300}
    assert actions.manifest.data["timeline_resources"]["state"] == "clean"
    assert not (tmp_path / "server" / "redstone-trials/datapacks" / grader.namespace).exists()
    assert any(c.startswith("schedule clear") for c in server.commands)
    assert any(c.startswith("scoreboard objectives remove") for c in server.commands)
    assert list(actions.manifest.path.parent.rglob("t200.mcfunction"))


def test_timeline_generated_commands_consume_shared_budget(tmp_path, monkeypatch):
    grader, actions = timing_harness(tmp_path, monkeypatch)
    actions.manifest.data["grading_budget"]["commands"] = 4000
    with pytest.raises(ActionLimit, match="command budget"):
        grader.timeline([], cycles=1)
    assert not list(tmp_path.rglob("t0.mcfunction"))


def test_pending_timeline_requires_recovery_before_new_grader(tmp_path):
    grader, actions, _ = harness(tmp_path)
    actions.manifest.data["timeline_resources"] = {"state": "recovery_required"}
    with pytest.raises(ValueError, match="explicit recovery"):
        GraderControl(actions, grader.declaration)


def test_interrupted_cleanup_can_be_recovered_without_replenishing_budget(tmp_path, monkeypatch):
    from noob_agent.redstone.grading import cleanup_timeline

    grader, actions = timing_harness(tmp_path, monkeypatch)
    resource = {
        "namespace": grader.namespace,
        "functions": ["t0", "t200", "abort"],
        "state": "pending",
        "sprint_state": "starting",
    }
    actions.manifest.data["timeline_resources"] = resource
    before = dict(actions.manifest.data["grading_budget"])
    original = actions.transport.command
    actions.transport.command = lambda command: (_ for _ in ()).throw(TimeoutError())
    with pytest.raises(TimeoutError):
        cleanup_timeline(actions.manifest, actions.transport)
    assert resource["state"] == "recovery_required"
    actions.transport.command = original
    cleanup_timeline(actions.manifest, actions.transport)
    assert resource["state"] == "clean"
    assert actions.manifest.data["grading_budget"] == before
    assert "tick sprint stop" in actions.transport.commands
    assert f"function {grader.namespace}:abort" in actions.transport.commands
    count = len(actions.transport.commands)
    cleanup_timeline(actions.manifest, actions.transport)
    assert len(actions.transport.commands) == count


def test_module_allowance_does_not_extend_program_deadline(tmp_path, monkeypatch):
    grader, actions = timing_harness(tmp_path, monkeypatch)
    del actions.manifest.data["grading_budget"]
    grader = GraderControl(actions, grader.declaration, max_ticks=96000, max_commands=1000000)
    monkeypatch.setattr(grader, "_execute_timeline", lambda f, d, s: dict.fromkeys(s, 0))
    grader.timeline([{"wait": 200}], cycles=8, program_id="first")
    assert grader.ticks == 1800
    assert actions.manifest.data["grading_programs"]["first"]["ticks"] == 1600
    second = GraderControl(actions, grader.declaration, max_ticks=96000, max_commands=1000000)
    with pytest.raises(ActionLimit, match="Program deadline"):
        second.timeline([], program_id="first")
    assert second.ticks == 1800
    assert actions.stopped


def test_sample_only_hold_has_no_step_and_uses_same_aggregate(tmp_path, monkeypatch):
    grader, actions = timing_harness(tmp_path, monkeypatch)
    captured = {}

    def execute(functions, duration, scores):
        captured.update(functions=functions, duration=duration)
        return dict.fromkeys(scores, 0)

    monkeypatch.setattr(grader, "_execute_timeline", execute)
    result = grader.timeline([{"wait": 200}, {"wait": 200}], sample_only=True)
    assert len(result["snapshots"]) == 1
    assert result["snapshots"][0]["offset"] == 400
    assert "powered=true" not in "".join(captured["functions"].values())
    assert grader.ticks == 400
    second = GraderControl(actions, grader.declaration)
    assert second.ticks == 400


def test_sample_only_hold_and_reset_share_timeline_with_two_snapshots(tmp_path, monkeypatch):
    grader, actions = timing_harness(tmp_path, monkeypatch)
    captured = {}

    def execute(functions, duration, scores):
        captured.update(functions=functions, duration=duration)
        return {name: 1 if name.startswith("x") else 0 for name in scores}

    monkeypatch.setattr(grader, "_execute_timeline", execute)
    result = grader.timeline(
        [],
        cycles=2,
        sample_only=True,
        cycle_recipes=[
            [{"wait": 200}, {"wait": 200}],
            [
                {"control": "reset", "level": True},
                {"wait": 200},
                {"control": "reset", "level": False},
                {"wait": 200},
            ],
        ],
    )
    assert [sample["offset"] for sample in result["snapshots"]] == [400, 800]
    assert captured["duration"] == 800
    assert "powered=true" in captured["functions"]["t400"]
    assert "powered=false" in captured["functions"]["t600"]
    assert "powered=true" not in captured["functions"].get("t0", "")
    assert grader.ticks == 800
    assert actions.used == 1


def test_mixed_timeline_keeps_clock_and_sample_checkpoints_distinct(tmp_path, monkeypatch):
    grader, actions = timing_harness(tmp_path, monkeypatch)
    captured = {}

    def execute(functions, duration, scores):
        captured.update(functions=functions, duration=duration)
        return {name: 1 if name.startswith("x") else 0 for name in scores}

    monkeypatch.setattr(grader, "_execute_timeline", execute)
    result = grader.timeline(
        [],
        cycles=3,
        cycle_recipes=[
            [],
            [{"wait": 200}, {"wait": 200}],
            [
                {"control": "reset", "level": True},
                {"wait": 200},
                {"control": "reset", "level": False},
                {"wait": 200},
            ],
        ],
        sample_cycles=[1, 2],
    )
    assert result["cycles"] == 1
    assert result["duration"] == captured["duration"] == 1000
    assert [item["offset"] for item in result["snapshots"] if item["phase"] == "settled"] == [
        200,
        600,
        1000,
    ]
    assert [item["offset"] for item in result["snapshots"] if item["phase"] == "pulse_end"] == [2]
    assert "powered=true" in captured["functions"]["t600"]
    assert "powered=false" in captured["functions"]["t800"]
    assert grader.ticks == 1000
    assert actions.used == 1


@pytest.mark.parametrize("indexes", [[0, 0], [3], [True], [-1]])
def test_mixed_timeline_rejects_invalid_sample_indexes(tmp_path, monkeypatch, indexes):
    grader, actions = timing_harness(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="Invalid mixed sample cycles"):
        grader.timeline([], cycles=3, cycle_recipes=[[], [], []], sample_cycles=indexes)
    assert actions.used == 1
    assert not list(tmp_path.rglob("*.mcfunction"))


def test_expired_program_rejected_before_world_access(tmp_path, monkeypatch):
    grader, actions, server = harness(tmp_path)
    actions.manifest.data["grading_programs"] = {
        "expired": {"steps": 1, "ticks": 200, "started": 0}
    }
    with pytest.raises(ActionLimit, match="Program deadline"):
        grader.timeline([], program_id="expired")
    assert not server.commands
