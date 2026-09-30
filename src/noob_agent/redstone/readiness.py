"""Read-only submission evidence report; this never runs or accepts a grader."""

from typing import Any

from noob_agent.redstone.contract import MachineContract

MODULES = ("register", "arithmetic", "storage", "output")
PROVIDER_EVENTS = {"planner_call", "jev_call"}


def trial_readiness(data: dict[str, Any]) -> dict[str, Any]:
    """Require durable observed grades, rather than UI status or copied pass flags.

    This reports retained evidence, not a fresh live hardware verification. Any
    unknown non-provider operation or construction after the full grade prevents
    a ready result. Historical provider uncertainties remain visible.
    """
    events = data.get("events", [])
    if not isinstance(events, list) or any(not isinstance(event, dict) for event in events):
        raise ValueError("Invalid trial events")
    by_sequence = {
        event["sequence"]: event for event in events if type(event.get("sequence")) is int
    }
    accepted = data.get("milestone_4", {}).get("modules", {})
    verified_modules = []
    for module in MODULES:
        acceptance = accepted.get(module, {})
        event = by_sequence.get(acceptance.get("grader_event"), {})
        result = event.get("result", {})
        if (
            event.get("outcome") == "observed"
            and event.get("kind") in {"module_inspection", "module_integration_recheck"}
            and result.get("module") == module
            and result.get("scope") == "public_module_behavior"
            and result.get("complete") is True
            and result.get("behavioral_passed") is True
        ):
            verified_modules.append(module)

    full = data.get("full_machine_verification", {})
    full_events = [
        event
        for event in events
        if event.get("kind") == "full_machine_grade"
        and event.get("outcome") == "observed"
        and event.get("result") == full
    ]
    programs = full.get("programs", [])
    expected = MachineContract().programs
    public_programs_passed = (
        isinstance(programs, list)
        and len(programs) >= 2
        and all(
            programs[index].get("words") == list(program.words) + [48] * (8 - len(program.words))
            and programs[index].get("outputs") == list(program.expected_outputs)
            for index, program in enumerate(expected)
        )
    )
    full_passed = (
        bool(full_events)
        and full.get("scope") == "observed_stored_program_behavior"
        and full.get("passed") is True
        and full.get("failures") == []
        and public_programs_passed
    )
    unknown = [event for event in events if event.get("outcome") == "unknown"]
    provider_unknown = [
        event.get("sequence") for event in unknown if event.get("kind") in PROVIDER_EVENTS
    ]
    other_unknown = [
        event.get("sequence") for event in unknown if event.get("kind") not in PROVIDER_EVENTS
    ]
    last_grade = max((event.get("sequence", -1) for event in full_events), default=-1)
    later_construction = [
        event.get("sequence")
        for event in events
        if last_grade >= 0
        and event.get("kind") in {"bounded_action", "bounded_command"}
        and event.get("sequence", -1) > last_grade
    ]
    missing = [module for module in MODULES if module not in verified_modules]
    return {
        "run_id": data.get("run_id"),
        "scope": "retained_trial_evidence_not_fresh_live_verification",
        "hardware_ready": full_passed
        and not missing
        and not other_unknown
        and not later_construction,
        "verified_modules": verified_modules,
        "missing_modules": missing,
        "full_machine_passed": full_passed,
        "unknown_provider_events": provider_unknown,
        "unknown_other_events": other_unknown,
        "construction_after_full_grade": later_construction,
        "assisted_production": bool(data.get("production_profile")),
    }
