"""Submission reporting must never promote a partial trial to a complete machine."""

from noob_agent.redstone.readiness import trial_readiness


def accepted_trial():
    events = [
        {
            "sequence": index,
            "kind": "module_inspection",
            "outcome": "observed",
            "result": {
                "module": module,
                "scope": "public_module_behavior",
                "complete": True,
                "behavioral_passed": True,
                "checks": [{"passed": True}],
            },
        }
        for index, module in enumerate(("register", "arithmetic", "storage", "output"))
    ]
    events.append(
        {
            "sequence": 4,
            "kind": "full_machine_grade",
            "outcome": "observed",
            "result": {
                "scope": "observed_stored_program_behavior",
                "passed": True,
                "failures": [],
                "programs": [
                    {"words": [3, 32, 21, 32, 48, 48, 48, 48], "outputs": [3, 8]},
                    {"words": [14, 32, 21, 32, 48, 48, 48, 48], "outputs": [14, 3]},
                ],
            },
        }
    )
    return {
        "run_id": "fixture",
        "milestone_4": {
            "modules": {
                module: {"grader_event": index}
                for index, module in enumerate(("register", "arithmetic", "storage", "output"))
            }
        },
        "events": events,
        "full_machine_verification": events[-1]["result"],
    }


def test_register_only_is_incomplete_even_with_optimistic_loop_status():
    trial = accepted_trial()
    trial["milestone_4"]["modules"] = {"register": {"grader_event": 0}}
    trial["loop"] = {"status": "full_machine_behavior_passed"}
    report = trial_readiness(trial)
    assert report["hardware_ready"] is False
    assert report["missing_modules"] == ["arithmetic", "storage", "output"]


def test_requires_observed_grader_evidence_and_two_changed_programs():
    trial = accepted_trial()
    assert trial_readiness(trial)["hardware_ready"] is True
    trial["events"][-1]["outcome"] = "unknown"
    assert trial_readiness(trial)["hardware_ready"] is False


def test_copying_a_pass_flag_without_the_grader_record_is_rejected():
    trial = accepted_trial()
    trial["events"] = []
    assert trial_readiness(trial)["hardware_ready"] is False


def test_unknown_world_action_blocks_readiness_provider_unknown_is_disclosed():
    trial = accepted_trial()
    trial["events"].append({"sequence": 5, "kind": "planner_call", "outcome": "unknown"})
    report = trial_readiness(trial)
    assert report["hardware_ready"] is True
    assert report["unknown_provider_events"] == [5]
    trial["events"].append({"sequence": 6, "kind": "bounded_command", "outcome": "unknown"})
    assert trial_readiness(trial)["hardware_ready"] is False


def test_duplicate_program_and_incomplete_grade_are_not_accepted():
    trial = accepted_trial()
    trial["events"][-1]["result"]["programs"][1] = trial["events"][-1]["result"]["programs"][0]
    assert trial_readiness(trial)["hardware_ready"] is False


def test_construction_after_full_grade_requires_reverification():
    trial = accepted_trial()
    trial["events"].append({"sequence": 5, "kind": "bounded_action", "outcome": "observed"})
    report = trial_readiness(trial)
    assert report["hardware_ready"] is False
    assert report["construction_after_full_grade"] == [5]


def test_no_grade_does_not_report_entire_construction_history_as_stale():
    trial = {"events": [{"sequence": 0, "kind": "bounded_action", "outcome": "observed"}]}
    assert trial_readiness(trial)["construction_after_full_grade"] == []
    trial = accepted_trial()
    trial["events"][0]["result"]["complete"] = False
    assert trial_readiness(trial)["hardware_ready"] is False
