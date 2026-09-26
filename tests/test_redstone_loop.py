"""Connected-loop behavioral checks, with no network or provider access."""

import json

import pytest

from noob_agent.models.client import ModelResponse
from noob_agent.redstone.actions import Actions
from noob_agent.redstone.jev import JevError
from noob_agent.redstone.loop import TrialLoop
from noob_agent.redstone.trial import TrialManifest


class World:
    def command(self, command):
        raise AssertionError("Observation fixture must not mutate")

    def request(self, request):
        return {"name": "minecraft:air", "properties": {}, "position": request["position"]}


def intention(count=2):
    return {
        "summary": "Inspect actual cells",
        "actions": [
            {
                "id": f"cell-{i}",
                "criteria": "Inspect unobserved cell",
                "action": "observe",
                "position": [i, 64, 0],
            }
            for i in range(count)
        ],
    }


class Planner:
    def __init__(self, manifest, value=None, error=None):
        self.manifest, self.value, self.error = manifest, value or intention(), error
        self.requests = []

    async def complete(self, request):
        disk = json.loads(self.manifest.path.read_text())
        assert disk["loop_budget"]["planner_calls"] == len(self.requests) + 1
        assert disk["events"][-1]["outcome"] == "unknown"
        self.requests.append(request)
        if self.error:
            raise self.error
        return ModelResponse(
            text=json.dumps(self.value), input_tokens=3, output_tokens=4, model_id="fixture/planner"
        )


class Jev:
    def __init__(self, manifest, choice=None, error=None):
        self.manifest, self.choice, self.error = manifest, choice, error
        self.requests = []

    def evaluate(self, request, *, timeout):
        disk = json.loads(self.manifest.path.read_text())
        assert disk["loop_budget"]["jev_calls"] == len(self.requests) + 1
        assert disk["events"][-1]["outcome"] == "unknown"
        assert 0 < timeout <= 30
        self.requests.append(request)
        if self.error:
            raise self.error
        return {
            "answers": {
                "action": {
                    "choice": self.choice or next(iter(request["questions"]["action"]["criteria"]))
                }
            },
            "usage": {"totalTokens": 5},
            "response": {"modelId": "fixture/jev"},
        }


def setup(tmp_path, **kwargs):
    manifest = TrialManifest(tmp_path)
    world = World()
    actions = Actions(manifest, world, world)
    planner, jev = Planner(manifest), Jev(manifest)
    loop = TrialLoop(manifest, actions, planner, jev, **kwargs)
    return manifest, actions, planner, jev, loop


async def test_sequence_observations_and_feedback_drive_same_trial_repair(tmp_path):
    checks = iter([{"passed": False, "actual": "missing link"}, {"passed": True, "complete": True}])
    manifest, actions, planner, jev, loop = setup(tmp_path, check=lambda _: next(checks))
    await loop.run({})
    assert len(planner.requests) == 2
    assert len(jev.requests) == 4
    assert actions.used == 4
    assert jev.requests[1]["state"]["results"][0]["result"]["name"] == "minecraft:air"
    assert "missing link" in planner.requests[1].prompt
    assert manifest.data["loop_budget"]["repair_rounds"] == 1
    assert manifest.data["loop"]["status"] == "checkpoint_complete"
    assert manifest.data["final_grade"]["model_success"] is False
    other = setup(tmp_path / "fresh", check=lambda _: {"passed": True, "complete": True})
    await other[-1].run({})
    assert "missing link" not in other[2].requests[0].prompt


async def test_jev_calls_are_paced_without_charging_a_wait(tmp_path, monkeypatch):
    now = [0.0]
    delays = []

    async def advance(delay):
        delays.append(delay)
        now[0] += delay

    monkeypatch.setattr("noob_agent.redstone.loop.asyncio.sleep", advance)
    manifest, actions, _, jev, loop = setup(
        tmp_path,
        clock=lambda: now[0],
        jev_min_interval_seconds=4,
        check=lambda _: {"passed": True, "complete": True},
    )
    await loop.run({})
    assert delays == [4]
    assert len(jev.requests) == 2
    assert actions.used == 2
    assert manifest.data["loop_budget"]["jev_calls"] == 2


async def test_provider_build_only_intention_dispatches_without_premature_grade(
    tmp_path, monkeypatch
):
    manifest, actions, planner, jev, loop = setup(tmp_path, require_module_grading=True)
    build = {
        "summary": "Place one control for register",
        "actions": [
            {
                "id": "control",
                "criteria": "Place control",
                "action": "place",
                "position": [80, 64, 80],
                "block": "minecraft:lever",
            }
        ],
    }
    calls = 0

    async def complete(request):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("end after build-only offer")
        return ModelResponse(
            text=json.dumps(build), input_tokens=3, output_tokens=4, model_id="fixture"
        )

    planner.complete = complete
    monkeypatch.setattr(
        actions,
        "apply",
        lambda action, position, block, properties: {"effect_verified": True},
    )
    await loop.run({})
    assert len(jev.requests) == 1
    assert manifest.data["milestone_4"] == {
        "status": "pending",
        "construction_epoch": 1,
        "modules": {},
    }
    assert not manifest.data["checks"]
    assert not any(event["kind"] == "planner_validation" for event in manifest.data["events"])


async def test_provider_rejects_stone_only_building_before_circuit_progress(tmp_path):
    manifest, actions, planner, jev, loop = setup(tmp_path, require_module_grading=True)
    planner.value = {
        "summary": "Extend foundation",
        "actions": [
            {
                "id": "more_stone",
                "criteria": "Extend foundation",
                "action": "place",
                "position": [30, 64, 30],
                "block": "minecraft:stone",
            }
        ],
    }
    await loop.run({})
    assert len(planner.requests) == 3
    assert not jev.requests
    assert actions.used == 0
    rejected = [event for event in manifest.data["events"] if event["kind"] == "planner_validation"]
    assert len(rejected) == 3
    assert "No signal or control block exists yet" in rejected[0]["request"]["reason"]
    assert "No signal or control block exists yet" in planner.requests[1].prompt


async def test_provider_static_power_does_not_bypass_signal_progress_guard(tmp_path):
    manifest, actions, planner, jev, loop = setup(tmp_path, require_module_grading=True)
    planner.value = {
        "summary": "Place static power base",
        "actions": [
            {
                "id": "power",
                "criteria": "Place power source",
                "action": "place",
                "position": [30, 64, 30],
                "block": "minecraft:redstone_block",
            }
        ],
    }
    await loop.run({})
    assert not jev.requests
    assert actions.used == 0
    assert sum(event["kind"] == "planner_validation" for event in manifest.data["events"]) == 3


async def test_provider_rejects_action_cap_below_offer_readback_cost(tmp_path):
    manifest, actions, planner, jev, loop = setup(tmp_path, require_module_grading=True)
    planner.value = {
        "summary": "Place two register controls",
        "max_actions": 5,
        "actions": [
            {
                "id": f"control_{index}",
                "criteria": "Place control",
                "action": "place",
                "position": [30 + index, 64, 30],
                "block": "minecraft:lever",
            }
            for index in range(2)
        ],
    }
    await loop.run({})
    assert not jev.requests
    assert actions.used == 0
    rejected = [event for event in manifest.data["events"] if event["kind"] == "planner_validation"]
    assert len(rejected) == 3
    assert "max_actions cannot cover" in rejected[0]["request"]["reason"]


async def test_provider_rejects_replacing_verified_identical_cell(tmp_path, monkeypatch):
    manifest, actions, planner, jev, loop = setup(tmp_path, require_module_grading=True)
    planner.value = {
        "summary": "Place register input wire",
        "actions": [
            {
                "id": "input_wire",
                "criteria": "Connect input",
                "action": "place",
                "position": [30, 64, 30],
                "block": "minecraft:redstone_wire",
            }
        ],
    }
    monkeypatch.setattr(actions, "apply", lambda *args: {"effect_verified": True})
    await loop.run({})
    assert len(jev.requests) == 1
    assert manifest.data["milestone_4"]["construction_epoch"] == 1
    rejected = [event for event in manifest.data["events"] if event["kind"] == "planner_validation"]
    assert len(rejected) == 3
    assert "Block already verified at x 30 y 64 z 30" in rejected[0]["request"]["reason"]


async def test_unfinished_grading_handoff_returns_to_compact_build_schema(tmp_path, monkeypatch):
    manifest, actions, planner, _, loop = setup(tmp_path, require_module_grading=True)
    replies = [
        {"summary": "Request register declaration schema", "actions": [], "request_grading": True},
        {
            "summary": "Add a register control first",
            "actions": [
                {
                    "id": "control",
                    "criteria": "Place control",
                    "action": "place",
                    "position": [30, 64, 30],
                    "block": "minecraft:lever",
                }
            ],
        },
    ]
    requests = []

    async def complete(request):
        requests.append(request)
        if len(requests) > len(replies):
            raise RuntimeError("stop after schema handoff")
        return ModelResponse(
            text=json.dumps(replies[len(requests) - 1]),
            input_tokens=3,
            output_tokens=4,
            model_id="fixture",
        )

    planner.complete = complete
    monkeypatch.setattr(actions, "apply", lambda *args: {"effect_verified": True})
    await loop.run({})
    assert len(requests) == 3
    assert "module_inspection" not in requests[0].response_schema["properties"]
    assert "module_inspection" in requests[1].response_schema["properties"]
    assert "module_inspection" not in requests[2].response_schema["properties"]


async def test_provider_feedback_reports_verified_construction_progress(tmp_path, monkeypatch):
    manifest, actions, planner, _, loop = setup(tmp_path, require_module_grading=True)
    planner.value = {
        "summary": "Place register control",
        "actions": [
            {
                "id": "lever",
                "criteria": "Place control",
                "action": "place",
                "position": [30, 64, 30],
                "block": "minecraft:lever",
            }
        ],
    }
    monkeypatch.setattr(actions, "apply", lambda *args: {"effect_verified": True})
    original_complete = planner.complete

    async def complete_once(request):
        if planner.requests:
            planner.requests.append(request)
            raise RuntimeError("stop after progress feedback")
        return await original_complete(request)

    planner.complete = complete_once
    await loop.run({})
    assert loop.signal_control_placements > 0
    assert '"signal_control_placements": 1' in planner.requests[1].prompt


async def test_provider_grading_handoff_requests_full_schema_only_when_ready(tmp_path, monkeypatch):
    from test_redstone_behavior import compact_declared

    manifest, actions, planner, jev, loop = setup(tmp_path, require_module_grading=True)
    replies = [
        {
            "summary": "Place control",
            "actions": [
                {
                    "id": "control",
                    "criteria": "Place control",
                    "action": "place",
                    "position": [80, 64, 80],
                    "block": "minecraft:lever",
                }
            ],
        },
        {"summary": "Ready for public register grade", "actions": [], "request_grading": True},
        {
            "summary": "Grade register",
            "actions": [],
            "module_inspection": compact_declared("register"),
        },
    ]
    requests = []

    async def complete(request):
        requests.append(request)
        if len(requests) > len(replies):
            raise RuntimeError("stop after grade")
        return ModelResponse(
            text=json.dumps(replies[len(requests) - 1]),
            input_tokens=3,
            output_tokens=4,
            model_id="fixture",
        )

    planner.complete = complete
    monkeypatch.setattr(actions, "apply", lambda *args: {"effect_verified": True})
    monkeypatch.setattr(
        "noob_agent.redstone.loop.grade_module",
        lambda runtime, declaration, *, fail_fast: {
            "module": declaration.module,
            "scope": "public_module_behavior",
            "complete": True,
            "behavioral_passed": True,
            "checks": [{"passed": True}],
            "failed_checks": [],
        },
    )
    await loop.run({})
    assert len(jev.requests) == 1
    assert "module_inspection" not in requests[0].response_schema["properties"]
    assert "module_inspection" not in requests[1].response_schema["properties"]
    assert "module_inspection" in requests[2].response_schema["properties"]
    assert manifest.data["milestone_4"]["modules"]["register"]["construction_epoch"] == 1


@pytest.mark.parametrize("provider", ["planner", "jev"])
async def test_failed_calls_remain_charged_and_stop(tmp_path, provider):
    manifest, actions, planner, jev, loop = setup(tmp_path)
    target = planner if provider == "planner" else jev
    target.error = RuntimeError("secret-fixture")
    await loop.run({})
    assert manifest.data["loop_budget"][provider + "_calls"] == 1
    assert manifest.data["loop"]["status"] == "stopped"
    assert actions.used == 0
    assert "secret-fixture" not in manifest.path.read_text()


async def test_invalid_choice_never_dispatches(tmp_path):
    manifest, actions, _, jev, loop = setup(tmp_path)
    jev.choice = "unoffered"
    await loop.run({})
    assert actions.used == 0
    assert manifest.data["loop"]["status"] == "stopped"


async def test_interruption_is_durable_and_not_retried(tmp_path):
    manifest, actions, _, jev, loop = setup(tmp_path)
    jev.error = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        await loop.run({})
    assert manifest.data["loop"]["status"] == "stopped"
    assert manifest.data["loop_budget"]["jev_calls"] == 1
    assert actions.stopped


@pytest.mark.parametrize("limit", ["planner_calls", "jev_calls", "repair_rounds"])
async def test_exhausted_limits_prevent_next_dispatch(tmp_path, limit):
    manifest, _, planner, jev, loop = setup(tmp_path, check=lambda _: {"passed": False})
    loop.used[limit] = getattr(loop.contract.budgets, limit)
    await loop.run({})
    assert manifest.data["loop"]["status"] == "stopped"
    if limit == "planner_calls":
        assert not planner.requests
    elif limit == "jev_calls":
        assert not jev.requests
    else:
        assert len(planner.requests) == 1


async def test_intention_action_cap_counts_actual_observations(tmp_path):
    manifest, actions, planner, _, loop = setup(tmp_path)
    planner.value = {**intention(), "max_actions": 1}
    await loop.run({})
    assert actions.used == 1
    assert manifest.data["loop"]["status"] == "stopped"


async def test_remaining_time_bounds_provider_and_blocks_late_action(tmp_path):
    now = [0.0]
    manifest, actions, _, jev, loop = setup(tmp_path, clock=lambda: now[0])
    original = jev.evaluate

    def late(request, *, timeout):
        assert timeout <= 30
        result = original(request, timeout=timeout)
        now[0] = 61
        return result

    jev.evaluate = late
    await loop.run({})
    assert actions.used == 0
    assert manifest.data["loop"]["status"] == "stopped"


async def test_early_finish_returns_to_planner_without_dispatch(tmp_path):
    manifest, actions, _, jev, loop = setup(
        tmp_path, check=lambda _: {"passed": True, "complete": True}
    )
    jev.choice = "__finish__"
    await loop.run({})
    assert actions.used == 0
    assert manifest.data["loop"]["status"] == "checkpoint_complete"


async def test_unknown_world_outcome_stops_before_another_model_call(tmp_path):
    from noob_agent.redstone.rcon import UnknownOutcome

    manifest, actions, planner, jev, loop = setup(tmp_path)

    def lost(request):
        raise UnknownOutcome("secret-fixture")

    actions.reader.request = lost
    await loop.run({})
    assert len(planner.requests) == len(jev.requests) == 1
    assert actions.stopped
    assert manifest.data["loop"]["reason"] == "UnknownOutcome"
    assert manifest.data["events"][-1]["outcome"] == "unknown"
    assert "secret-fixture" not in manifest.path.read_text()


async def test_global_action_limit_counts_reads(tmp_path):
    manifest, actions, _, jev, loop = setup(tmp_path)
    actions.maximum = 1
    await loop.run({})
    assert actions.used == 1
    assert len(jev.requests) == 1
    assert manifest.data["loop"]["reason"] == "LoopLimit"


async def test_provider_receives_short_remaining_intention_deadline(tmp_path):
    _, _, planner, jev, loop = setup(tmp_path, check=lambda _: {"passed": True, "complete": True})
    planner.value = {**intention(1), "max_seconds": 2}
    original = jev.evaluate

    def bounded(request, *, timeout):
        assert 0 < timeout <= 2
        return original(request, timeout=timeout)

    jev.evaluate = bounded
    await loop.run({})


async def test_expired_wall_budget_never_calls_provider(tmp_path):
    now = [0.0]
    manifest, _, planner, _, loop = setup(tmp_path, clock=lambda: now[0])
    now[0] = 3600
    await loop.run({})
    assert not planner.requests
    assert manifest.data["loop"]["reason"] == "LoopLimit"


async def test_invalid_intention_never_calls_jev(tmp_path):
    manifest, actions, planner, jev, loop = setup(tmp_path)
    planner.value = {**intention(), "max_seconds": 61}
    await loop.run({})
    assert not jev.requests
    assert actions.used == 0
    assert manifest.data["loop_budget"]["planner_calls"] == 3
    assert manifest.data["loop_budget"]["repair_rounds"] == 2
    validation_events = [
        event for event in manifest.data["events"] if event["kind"] == "planner_validation"
    ]
    assert [event["result"]["retry_scheduled"] for event in validation_events] == [
        True,
        True,
        False,
    ]


async def test_schema_error_feedback_names_field_without_echoing_input(tmp_path):
    manifest, actions, planner, jev, loop = setup(tmp_path)
    planner.value = intention(1)
    planner.value["actions"][0]["position"][0] = True
    await loop.run({})
    assert not jev.requests
    assert actions.used == 0
    reasons = [
        event["request"]["reason"]
        for event in manifest.data["events"]
        if event["kind"] == "planner_validation"
    ]
    assert reasons == ["Field actions.0.position.0 failed int_type."] * 3
    assert "Field actions.0.position.0" in planner.requests[1].prompt
    assert "True" not in reasons[0]


async def test_truncated_json_gets_compact_repair_guidance(tmp_path):
    manifest, actions, planner, jev, loop = setup(tmp_path)

    async def truncated(request):
        planner.requests.append(request)
        return ModelResponse(
            text='{"summary": "unfinished", "actions": [',
            input_tokens=100,
            output_tokens=16_384,
            model_id="fixture/planner",
            finish_reason="length",
        )

    planner.complete = truncated
    await loop.run({})
    assert not jev.requests
    assert actions.used == 0
    assert len(planner.requests) == 3
    assert "at most 8 actions" in planner.requests[1].prompt
    assert "output token cap" in planner.requests[1].prompt


async def test_provider_rejects_incomplete_module_recipes_before_jev(tmp_path):
    from test_redstone_behavior import compact_declared

    manifest, actions, planner, jev, loop = setup(tmp_path, require_module_grading=True)
    declaration = compact_declared("register")
    declaration["recipe_templates"].clear()
    planner.value = {
        "summary": "Place controls",
        "actions": intention(1)["actions"],
        "module_inspection": declaration,
    }
    await loop.run({})
    assert not jev.requests
    assert actions.used == 0
    failures = [
        event["request"]["reason"]
        for event in manifest.data["events"]
        if event["kind"] == "planner_validation"
    ]
    assert all("Missing register recipe load:0" in failure for failure in failures)
    assert "recipe_templates.load" in planner.requests[1].prompt


async def test_provider_checkpoint_requires_four_passes_after_last_build(tmp_path, monkeypatch):
    from test_redstone_behavior import compact_declared

    manifest = TrialManifest(tmp_path)
    world = World()
    actions = Actions(manifest, world, world)
    jev = Jev(manifest)
    modules = ["register", "arithmetic", "storage", "output", "register"]
    replies = [
        {
            "summary": f"Grade {module}",
            "actions": [
                {
                    "id": "build",
                    "criteria": "Place control",
                    "action": "place",
                    "position": [80, 64, 80],
                    "block": "minecraft:lever",
                }
            ]
            if index == 1
            else [],
            "module_inspection": compact_declared(module),
        }
        for index, module in enumerate(modules)
    ]

    class SequencePlanner:
        def __init__(self):
            self.requests = []

        async def complete(self, request):
            self.requests.append(request)
            return ModelResponse(
                text=json.dumps(replies[len(self.requests) - 1]),
                input_tokens=3,
                output_tokens=4,
                model_id="fixture/planner",
            )

    planner = SequencePlanner()
    monkeypatch.setattr(
        actions,
        "apply",
        lambda action, position, block, properties: {"effect_verified": True},
    )

    def passing_grade(runtime, declaration, *, fail_fast=False):
        result = {
            "module": declaration.module,
            "scope": "public_module_behavior",
            "complete": True,
            "behavioral_passed": True,
            "checks": [{"passed": True}],
            "failed_checks": [],
        }
        runtime.manifest.data["checks"].append(result)
        runtime.manifest.save()
        return result

    monkeypatch.setattr("noob_agent.redstone.loop.grade_module", passing_grade)
    loop = TrialLoop(manifest, actions, planner, jev, require_module_grading=True)
    await loop.run({})
    milestone = manifest.data["milestone_4"]
    assert len(planner.requests) == 5
    assert manifest.data["loop"]["status"] == "public_modules_passed"
    assert milestone["status"] == "checks_passed"
    assert milestone["construction_epoch"] == 1
    assert set(milestone["modules"]) == set(modules)
    assert all(item["construction_epoch"] == 1 for item in milestone["modules"].values())
    invalidation = [
        event for event in manifest.data["events"] if event["kind"] == "milestone_4_invalidation"
    ]
    assert invalidation[0]["request"]["modules"] == ["register"]
    progress = json.loads(planner.requests[4].prompt)["feedback"][-1]["milestone_4"]
    assert progress["passed_modules"] == ["arithmetic", "output", "storage"]
    assert progress["remaining_modules"] == ["register"]
    assert manifest.data["final_grade"]["model_success"] is False


async def test_rejected_block_state_returns_to_planner_without_stopping_world(tmp_path):
    class RejectingWorld(World):
        def request(self, request):
            if request["op"] == "validate":
                return {"valid": False}
            return super().request(request)

    manifest = TrialManifest(tmp_path)
    world = RejectingWorld()
    actions = Actions(manifest, world, world)
    planner = Planner(manifest)
    jev = Jev(manifest)
    loop = TrialLoop(
        manifest, actions, planner, jev, check=lambda _: {"passed": True, "complete": True}
    )

    async def complete(request):
        planner.requests.append(request)
        value = (
            {
                "summary": "Try lever",
                "actions": [
                    {
                        "id": "lever",
                        "criteria": "Place lever",
                        "action": "place",
                        "position": [48, 64, 90],
                        "block": "minecraft:lever",
                        "properties": {"facing": "up"},
                    }
                ],
            }
            if len(planner.requests) == 1
            else intention(1)
        )
        return ModelResponse(
            text=json.dumps(value), input_tokens=3, output_tokens=4, model_id="fixture/planner"
        )

    planner.complete = complete
    await loop.run({})
    assert len(planner.requests) == 2
    assert "invalid_block_state" in planner.requests[1].prompt
    assert manifest.data["loop"]["status"] == "checkpoint_complete"
    assert not [event for event in manifest.data["events"] if event["kind"] == "bounded_command"]


async def test_jev_http_503_retries_are_charged_before_any_action(tmp_path):
    manifest, actions, _, jev, loop = setup(
        tmp_path, check=lambda _: {"passed": True, "complete": True}
    )
    original = jev.evaluate
    attempts = [0]

    def temporarily_unavailable(request, *, timeout):
        attempts[0] += 1
        if attempts[0] <= 3:
            assert actions.used == 0
            jev.requests.append(request)
            raise JevError("gateway unavailable", diagnostic={"statusCode": 503})
        return original(request, timeout=timeout)

    jev.evaluate = temporarily_unavailable
    await loop.run({})
    assert manifest.data["loop"]["status"] == "checkpoint_complete"
    assert manifest.data["loop_budget"]["jev_calls"] == 5
    calls = [event for event in manifest.data["events"] if event["kind"] == "jev_call"]
    assert all(
        event["result"]
        == {
            "provider_error": {"statusCode": 503},
            "retry_scheduled": True,
        }
        for event in calls[:3]
    )
    assert all(event["outcome"] == "observed" for event in calls)


async def test_observed_effect_mismatch_is_returned_for_repair(tmp_path):
    manifest, actions, planner, _, loop = setup(tmp_path)
    planner.value = {
        "summary": "Place support",
        "actions": [
            {
                "id": "support",
                "criteria": "Place support",
                "action": "place",
                "position": [0, 64, 0],
                "block": "minecraft:stone",
            }
        ],
    }
    original_request = actions.reader.request

    def read(request):
        if request["op"] in ("settle", "validate"):
            return {}
        return original_request(request)

    actions.reader.request = read
    actions.transport.command = lambda _: "Changed the block"
    original_complete = planner.complete

    async def complete(request):
        if planner.requests:
            planner.error = RuntimeError("stop fixture after repair request")
        return await original_complete(request)

    planner.complete = complete
    await loop.run({})
    assert len(planner.requests) == 2
    feedback = json.loads(planner.requests[1].prompt)["feedback"][0]
    evidence = feedback["results"][0]["evidence"]
    assert evidence["effect_verified"] is False
    assert evidence["after"]["name"] == "minecraft:air"
    assert manifest.data["loop_budget"]["repair_rounds"] == 1


async def test_missing_dust_fixture_requires_observed_failure_before_repair():
    from noob_agent.redstone.contract import load_contract
    from noob_agent.redstone.fixtures import MissingDustPlanner
    from noob_agent.redstone.planner import PlannerContext
    from noob_agent.redstone.trial import CONTRACT_PATH

    planner = MissingDustPlanner()
    context = PlannerContext(load_contract(CONTRACT_PATH))
    check = {
        "name": "missing_dust_powered",
        "passed": False,
        "dust": {"name": "minecraft:air"},
        "lamp": {"properties": {"lit": False}},
        "lever": {"properties": {"powered": False}},
    }
    context.feedback({"check": check})
    with pytest.raises(ValueError):
        await planner.complete(context.request({}))
    check["lever"]["properties"]["powered"] = True
    context.feedback({"check": check})
    repair = json.loads((await planner.complete(context.request({}))).text)
    assert [action["id"] for action in repair["actions"]] == ["repair_missing_dust"]
    fresh = PlannerContext(context.contract)
    initial = json.loads((await planner.complete(fresh.request({}))).text)
    assert [action["id"] for action in initial["actions"]] == ["lever", "lamp"]


async def test_module_inspection_failure_then_repair_does_not_end_trial(tmp_path):
    from test_redstone_modules import World as ModuleWorld
    from test_redstone_modules import declaration

    value = declaration()
    world = ModuleWorld(value)
    world.cells[(0, 64, 0)]["name"] = "minecraft:air"
    manifest, actions, planner, jev, loop = setup(tmp_path)
    actions.reader = world
    planner.value = {
        "summary": "Inspect selected module interface",
        "actions": [],
        "module_inspection": value,
    }
    original = planner.complete

    async def complete(request):
        if len(planner.requests) == 1:
            world.cells[(0, 64, 0)]["name"] = "minecraft:redstone_wire"
        elif len(planner.requests) == 2:
            planner.error = RuntimeError("fixture ends after continued planning")
        return await original(request)

    planner.complete = complete
    await loop.run({})
    assert len(planner.requests) == 3
    assert not jev.requests
    assert "Missing, mismatched or ambiguous world evidence" in planner.requests[1].prompt
    checks = manifest.data["checks"]
    assert [check["valid"] for check in checks] == [False, True]
    assert all(check["behavioral_passed"] is False for check in checks)
    assert manifest.data["loop_budget"]["repair_rounds"] == 1
    assert manifest.data["final_grade"]["model_success"] is False


@pytest.mark.parametrize("compact", [False, True])
async def test_behavioral_failure_returns_raw_feedback_and_charges_repair(
    tmp_path, monkeypatch, compact
):
    from test_redstone_behavior import WorldReader, compact_declared, declared

    from noob_agent.redstone.behavior import grade_module

    manifest, actions, planner, jev, loop = setup(tmp_path)
    planner.value = {
        "summary": "Grade register",
        "actions": [],
        "module_inspection": compact_declared("register") if compact else declared("register"),
    }

    def grade(runtime, declaration, *, fail_fast=False):
        return grade_module(
            runtime,
            declaration,
            control=WorldReader(runtime, declaration, broken=True),
            fail_fast=fail_fast,
        )

    monkeypatch.setattr("noob_agent.redstone.loop.grade_module", grade)
    original = planner.complete

    async def complete(request):
        if planner.requests:
            planner.error = RuntimeError("end after actual repair feedback")
        return await original(request)

    planner.complete = complete
    await loop.run({})
    feedback = json.loads(planner.requests[1].prompt)["feedback"][0]["module_inspection"]
    assert feedback["behavioral_passed"] is False
    assert feedback["complete"] is False
    assert feedback["stopped_after_failure"] is True
    assert len(feedback["checks"]) == 1
    assert json.loads(manifest.path.read_text())["checks"][-1] == feedback
    assert feedback["failed_checks"][0]["actual"]["a"] == 8
    assert feedback["failed_checks"][0]["raw"]["snapshots"][0]["signals"]["a"] == [15, 0, 0, 0]
    assert manifest.data["loop_budget"]["repair_rounds"] == 1
