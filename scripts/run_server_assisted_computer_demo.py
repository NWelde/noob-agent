"""Live agent design with disclosed server-assisted construction and readbacks."""

import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import noob_agent.redstone.actions as actions_module
import noob_agent.redstone.loop as loop_module
import noob_agent.redstone.trial as trial_module
from noob_agent.redstone.circuit_plan import CircuitPlan
from noob_agent.redstone.demo import ActionPing
from noob_agent.redstone.trial import TrialConfiguration, run_trial

resume_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
saved = trial_module.TrialManifest.load_data(resume_path) if resume_path else None
if saved is not None and saved.get("production_profile"):
    production_contract = Path(saved["contract"]["path"])
    trial_module.CONTRACT_PATH = production_contract
    actions_module.CONTRACT_PATH = production_contract
    loop_module.CONTRACT_PATH = production_contract
    import json

    from noob_agent.redstone.contract import MachineContract

    original_load_contract = trial_module.load_contract
    original_contract = original_load_contract(
        Path("scenarios/minecraft/redstone-computer-v1/contract.json")
    )
    profile_limits = saved["production_profile"]["production_limits"]

    def load_assisted_contract(path):
        if Path(path) != production_contract:
            return original_load_contract(path)
        raw = json.loads(production_contract.read_text())
        expected = original_contract.model_dump(mode="json")
        expected["budgets"] = profile_limits
        if raw != expected:
            raise ValueError("Assisted production may change only its frozen disclosed budgets")
        return MachineContract.model_validate_json(production_contract.read_text())

    trial_module.load_contract = load_assisted_contract
    actions_module.load_contract = load_assisted_contract
if saved is not None:
    assert saved["loop"]["status"] == "stopped"
    assert all(
        e["kind"] in {"planner_call", "jev_call"}
        for e in saved["events"]
        if e["outcome"] == "unknown"
    )
    assert saved.get("timeline_resources", {}).get("state", "clean") == "clean"
    original_eligibility = trial_module.can_resume_model_failure
    trial_module.can_resume_model_failure = lambda data: (
        (
            data.get("run_id") == saved["run_id"]
            and data.get("loop", {}).get("reason")
            in {
                "planner_validation_exhausted",
                "KeyboardInterrupt",
                "CancelledError",
                "operator_reconciled_visual_boundary",
                "UnboundLocalError",
                "LoopLimit",
                "operator_geometry_copy_completed",
                "operator_geometry_copy_provider_error",
            }
        )
        or original_eligibility(data)
    )

# Restore from durable primitive charge events before continuation verification.
# The normal verification itself charges reads and overwrites action_budget.
OriginalActions = actions_module.Actions
CHARGE_KINDS = {
    "charged_observation",
    "bounded_action",
    "bounded_movement",
    "bounded_command",
    "grader_operation",
}


class CumulativeActions(OriginalActions):
    def __init__(self, manifest, *args, **kwargs):
        prior = max(
            sum(e["kind"] in CHARGE_KINDS for e in manifest.data["events"]),
            manifest.data.get("action_budget", {}).get("used", 0),
        )
        manifest.compact_snapshots = True
        super().__init__(manifest, *args, **kwargs)
        if resume_path is not None:
            self.used = prior
            elapsed = (
                datetime.now(UTC) - datetime.fromisoformat(manifest.data["started_at"])
            ).total_seconds()
            self.started -= elapsed
            manifest.data["action_budget"] = {
                "used": prior,
                "limit": self.maximum,
                "stopped": False,
            }
            ev = manifest.attempt(
                "sol_budget_reconciliation",
                {
                    "method": "conservative maximum of durable primitive cha"
                    "rges and retained counter",
                    "used": prior,
                    "kinds": sorted(CHARGE_KINDS),
                },
            )
            manifest.observed(ev, {"world_actions": 0, "original_wall_preserved": True})


actions_module.Actions = CumulativeActions
original_validate = loop_module.validate_intention


def isolated_register_intention(payload, contract):
    intention = original_validate(payload, contract)
    for action in intention.actions:
        x, y, z = action.position
        if (
            action.action == "place"
            and action.block == "minecraft:redstone_wire"
            and y == 64
            and z == 11
            and any(abs(x - lane) <= 1 for lane in (10, 16, 22, 28))
        ):
            raise ValueError(
                "Assisted register isolation: ground control d"
                "ust beside Q probes "
                "is forbidden; use elevated buses"
            )
    return intention


loop_module.validate_intention = isolated_register_intention

os.environ["NOOB_PHYSICAL_ACTIONS"] = "0"
os.environ["NOOB_SERVER_VISUALS"] = "1"
# Demo-only bounded request override; the shared trial wall/action limits remain.
loop_module.PLANNER_TIMEOUT_SECONDS = 180
trial_module.PLANNER_TIMEOUT_SECONDS = 180
seed = {
    "module": "register",
    "notes": [
        "ASSISTED DEMO: Sol architecture guidance; age"
        "nt-directed construction with "
        "server-assisted placement and movement. This "
        "is not ordinary play"
        "er placement "
        "or an unaided benchmark. Independent world re"
        "ads determine success.",
        "Build the frozen stored-program computer: A4,"
        " separately latched O4, PC3, "
        "eight 6-bit instructions, LOAD, ADD modulo16,"
        " OUT and HALT. No software "
        "simulation, scripted outputs or scoreboard co"
        "mputation.",
        "First build and independently grade one real "
        "four-bit register. A repeater's "
        "facing points toward its INPUT (opposite sign"
        "al travel). Parallel storage "
        "repeaters retain power when locked by powered"
        " perpendicular repeaters. "
        "The lock repeater output must point into the "
        "storage repeater side. "
        "Provide data controls, separate STEP and RESE"
        "T, and physical reset logic. "
        "Check a single lane before duplicating four l"
        "anes.",
        "Build supports before attached components; re"
        "move attachments bef"
        "ore supports. "
        "Wall torches attach at the same y opposite th"
        "eir facing. Floor components "
        "require solid support below. Keep control bus"
        "es short and prevent crosstalk.",
        "Action dependency IDs reference this response"
        " only. Circuit plan IDs are "
        "separate from action IDs. Use declared contro"
        "ls and actual observed signals "
        "for tests. Do not call a stage complete until"
        " independent checks pass.",
        "Final physical program LOAD3, OUT, ADD5, OUT,"
        " HALT must emit3 then8; "
        "a changed program LOAD14, OUT, ADD5, OUT, HAL"
        "T must emit14 then3. "
        "These are expectations, not evidence of prese"
        "nt hardware.",
    ],
}


class FreshConnectionActionPing(ActionPing):
    def show(self, action, block):
        from noob_agent.redstone.rcon import RconClient

        with RconClient.dedicated() as fresh:
            ActionPing(self.manifest, fresh).show(action, block)


original = loop_module.TrialLoop


class AssistedLoop(original):
    def __init__(self, manifest, actions, planner, jev, **kwargs):
        resuming = kwargs.get("resume_state") is not None
        prior_calls = dict(manifest.data.get("loop_budget", {}))
        prior_actions = manifest.data.get("action_budget", {}).get("used", 0)
        prior_assistance = manifest.data.get("assistance")
        # TrialWandbClient's SDK default is captured at class definition time.
        # Match the disclosed outer limit, with SDK retries still disabled.
        planner.timeout = 180
        kwargs["announce"] = None
        kwargs["batch_construction"] = True
        # User explicitly authorized ending per-block filming. Continuation
        # pose verification already ran with the retained server visual mode.
        actions.server_visual = False
        kwargs["full_machine_verification"] = True
        kwargs["strict_module_gate"] = True
        super().__init__(manifest, actions, planner, jev, **kwargs)
        # A targeted independent reset readback can improve while static hardware
        # is unchanged (real source transitions initialize scheduled components).
        # Admit this evidence once, without setting module acceptance.
        failed_grades = [
            e["sequence"]
            for e in manifest.data["events"]
            if e["kind"] == "module_inspection"
            and e.get("result", {}).get("scope") == "public_module_behavior"
            and e.get("result", {}).get("behavioral_passed") is False
        ]
        admitted = {
            e["request"]["diagnostic_sequence"]
            for e in manifest.data["events"]
            if e["kind"] == "operator_diagnostic_regrade_admission"
        }
        diagnostics = [
            e
            for e in manifest.data["events"]
            if e["kind"] == "operator_register_reset_diagnostic"
            and e.get("outcome") == "observed"
            and e["sequence"] > max(failed_grades, default=-1)
            and e["sequence"] not in admitted
        ]
        for diagnostic in diagnostics:
            result = diagnostic["result"]
            cells = {tuple(c["position"]): c for c in result.get("readbacks", [])}
            improved = (
                result.get("independent_zero_predicate") == "Test passed"
                and cells.get((4, 67, 4), {}).get("properties", {}).get("powered") is True
                and cells.get((10, 64, 9), {}).get("properties", {}).get("powered") is False
            )
            if improved:
                self.repair_guard.record_module_diagnostic_improvement(
                    "register", verified_improvement=True
                )
                ev = manifest.attempt(
                    "operator_diagnostic_regrade_admission",
                    {"diagnostic_sequence": diagnostic["sequence"]},
                )
                manifest.observed(
                    ev,
                    {
                        "world_actions": 0,
                        "module_acceptance": False,
                        "reason": "fresh independent lane RESET improvement; ful"
                        "l register suite required",
                    },
                )
        if not resuming:
            self.context.circuit_plan = CircuitPlan.model_validate(seed)
        else:
            self.used.update(prior_calls)
            elapsed = (
                datetime.now(UTC) - datetime.fromisoformat(manifest.data["started_at"])
            ).total_seconds()
            self.deadline = min(
                self.deadline, time.monotonic() + self.contract.budgets.wall_seconds - elapsed
            )
        design_path = Path("demo/sol-initial-design-20260929.md")
        design_text = design_path.read_text(encoding="utf-8")
        original_request = self.context.request

        def persistent_guidance(observation):
            nonlocal design_text
            latest_design = design_path.read_text(encoding="utf-8")
            if latest_design != design_text:
                design_text = latest_design
                intervention = manifest.attempt(
                    "sol_design_assistance_update", {"path": str(design_path), "text": design_text}
                )
                manifest.observed(intervention, {"disclosed": True, "world_actions": 0})
            request = original_request(observation)
            import copy

            batch_schema = copy.deepcopy(request.response_schema)
            if batch_schema and "actions" in batch_schema.get("properties", {}):
                batch_schema["properties"]["actions"]["maxItems"] = 32
            batch_system = request.system.replace(
                "Offer at most eight supported construction actions",
                "Offer up to32 supported construction actions",
            )
            batch_system = batch_system.replace(
                "Supports must be built in a previous batch be"
                "fore offering wire o"
                "r attached components; Jev can choose offers "
                "in any order.",
                "Supports may be placed earlier in this same s"
                "ection with explicit"
                " depends_on; the dependency queue verifies su"
                "pports before attach"
                "ments.",
            )
            request = request.model_copy(
                update={"system": batch_system, "response_schema": batch_schema}
            )
            passed = set(manifest.data.get("milestone_4", {}).get("modules", {}))
            current = next(
                (m for m in ("register", "arithmetic", "storage", "output") if m not in passed),
                "machine",
            )
            guidance = (
                "\nASSISTED DESIGN GUIDANCE (persistent): "
                + " ".join(seed["notes"])
                + "\nRecorded initial GPT-6.1 Sol design:\n"
                + design_text.split("## Recorded repair assistance")[0]
                + (
                    design_text.split("## Four-lane shared controls for the full register grade")[
                        -1
                    ]
                    if current == "register"
                    else ""
                )
                + f"\nIndependent passed modules: {sorted(passed)}. Current module: {current}. "
                "Do not move to another module until this modu"
                "le's behavioral grade passes. "
                "Use four parallel lanes separated by at least"
                " four blocks; do not place "
                "four data levers or storage repeaters along t"
                "he same wire bus. Keep "
                "individual data signals electrically independ"
                "ent. Build and diagnose "
                "one lane with data, clock-lock and reset befo"
                "re copying it. "
                "A reset lever powering data is not a reset: i"
                "t must force the held bit "
                "to zero, then allow the lock to close again. "
                "Data must be captured only "
                "during the STEP pulse. Direct data-to-lamp wi"
                "ring is not memory."
            )
            priority = (
                "User-authorized batched production: plan a wh"
                "ole functional circu"
                "it section per response, up to 32 primitives "
                "fitting the frozen i"
                "ntention limits. Include support-before-attac"
                "hment dependencies. "
                "No per-block labels, equip, swing or movement"
                " during this authori"
                "zed faster construction. One Jev selection be"
                "gins each section; r"
                "emaining primitives execute in dependency-rea"
                "dy planner order wit"
                "h charged actual readbacks. Copy verified lan"
                "e GEOMETRY through i"
                "ndividual bounded placements; never use clone"
                "/fill. Avoid repeate"
                "d unsupported stone scaffolding. "
                + f"Current independently open module is {current}. "
                "Build and grade it before moving on. Omit max_actions and max_seconds "
                "to use default charged limits. "
            )
            if current == "register":
                import json

                targets = json.loads(Path("demo/register-design-targets.json").read_text())["cells"]
                layout = {tuple(c["position"]): c for c in self.layout.to_jsonable()}
                missing = [
                    t
                    for t in targets
                    if (
                        layout.get(tuple(t["position"]), {}).get("name") != t["block"]
                        or any(
                            layout.get(tuple(t["position"]), {}).get("properties", {}).get(k) != v
                            for k, v in (
                                t["properties"] | t.get("expected_connections", {})
                            ).items()
                        )
                    )
                ]
                protected = {tuple(t["position"]) for t in targets}
                obsolete = [
                    c["position"]
                    for c in self.layout.to_jsonable()
                    if c["name"]
                    in {
                        "minecraft:redstone_wire",
                        "minecraft:repeater",
                        "minecraft:lever",
                        "minecraft:stone",
                    }
                    and 64 <= c["position"][1] <= 95
                    and 4 <= c["position"][0] <= 35
                    and 4 <= c["position"][2] <= 31
                    and tuple(c["position"]) not in protected
                ]
                obsolete.sort(key=lambda p: -p[1])
                next_work = {
                    "disclosed_sol_geometry_checklist": True,
                    "obsolete_ground_control_cells_to_remove": obsolete,
                    "missing_or_mismatched_cells": missing[:8],
                    "remaining_cell_count": len(missing),
                    "behavioral_pass": False,
                }
                priority += (
                    "Use this checklist based on verified layout. "
                    "Remove obsolete cell"
                    "s first, highest attachments before their sup"
                    "ports. Use the lates"
                    "t delayed RESET-data design: staircase x-4 an"
                    "d three west-facing "
                    "delay4 repeaters at x-3/x-2/x-1,z7; remove th"
                    "e obsolete x-2 stair"
                    "case. expected_connections are read-only veri"
                    "fication expectation"
                    "s, never placement properties. If existing du"
                    "st has the wrong aut"
                    "omatic connection shape, break and replace it"
                    " with properties={} "
                    "after its staircase is built, then observe it"
                    "s actual shape. Do n"
                    "ot directly assign dust connections or power."
                    "  Then propose up to"
                    " eight missing cells in listed order, support"
                    " before attachment. "
                    "If a cell contains the wrong block, break the"
                    "n replace it. Do not"
                    " repeat verified placements or observe unrela"
                    "ted cells. Preserve "
                    "individual data and Q probes. The checklist i"
                    "s design assistance,"
                    " never evidence of behavior. " + json.dumps(next_work)
                )
                if not missing and not obsolete:
                    priority += (
                        "Final geometry is present. Toggle real RESET "
                        "on/off to initialize"
                        " source transitions; then request full regist"
                        "er grading with comm"
                        "on RESET4,67,4 and STEP4,67,17, four data lev"
                        "ers10/16/22/28,64,5 "
                        "and A probes28/22/16/10,64,10 in MSB-first or"
                        "der. Load template s"
                        "ets only four data input bit levels and waits"
                        "20 ticks; grader its"
                        "elf pulses STEP. "
                    )
            if current == "arithmetic" and Path("demo/arithmetic-xor-design-targets.json").exists():
                import json

                xor_design = json.loads(Path("demo/arithmetic-xor-design-targets.json").read_text())
                targets = xor_design["cells"]
                layout = {tuple(c["position"]): c for c in self.layout.to_jsonable()}
                missing = [
                    t
                    for t in targets
                    if layout.get(tuple(t["position"]), {}).get("name") != t["block"]
                    or any(
                        layout.get(tuple(t["position"]), {}).get("properties", {}).get(k) != v
                        for k, v in t["properties"].items()
                    )
                ]
                protected = {tuple(t["position"]) for t in targets}
                obsolete = [
                    c["position"]
                    for c in self.layout.to_jsonable()
                    if c["name"] != "minecraft:air"
                    and 40 <= c["position"][0] <= 49
                    and 4 <= c["position"][2] <= 16
                    and 64 <= c["position"][1] <= 70
                    and tuple(c["position"]) not in protected
                ]
                obsolete.sort(key=lambda p: -p[1])
                priority += (
                    "Exact disclosed lane-zero XOR design checklis"
                    "t supersedes older g"
                    "eometry. This is only a combinational subcirc"
                    "uit, never arithmeti"
                    "c acceptance. Remove obsolete attachments bef"
                    "ore supports, then b"
                    "uild missing cells in whole sections of up to"
                    "32 bounded primitive"
                    "s. If a cell contains a wrong block, break be"
                    "fore replacing it. I"
                    "mmediate main repeaters at z9 and side repeat"
                    "ers x41/45,z10 give "
                    "equal level15 input to comparators x40/44,z10"
                    ". Preserve TWO sourc"
                    "e controls40,64,5(n) and46,64,5(A diagnostic)"
                    "; they drive both co"
                    "pies through isolated paths. Elevated n cross"
                    "over uses the exact "
                    "staircase x40..44 then y68 bridge to48 and de"
                    "scending staircase t"
                    "o48,z8; no joining it to A wiring. No additio"
                    "nal supports or torc"
                    "hes beyond this checklist. Do not extend or c"
                    "opy until all four r"
                    "eal input combinations00,01,10,11 yield XOR0,"
                    "1,1,0 at probe42,64,"
                    "15. Read actual lever states before interacti"
                    "ng and settle/read p"
                    "robe after each pair. Record the real truth-t"
                    "able observations in"
                    " circuit_plan notes so we can proceed to actu"
                    "al fulladder/shadow/"
                    "A/O hardware after subcircuit verification. "
                    + json.dumps(
                        {
                            "obsolete_cells": obsolete,
                            "missing_cells": missing[:32],
                            "remaining_cells": len(missing),
                            "subcircuit_only": True,
                        }
                    )
                )
            if (
                current == "arithmetic"
                and Path("demo/arithmetic-carry-design-targets.json").exists()
            ):
                import json

                targets = json.loads(Path("demo/arithmetic-carry-design-targets.json").read_text())[
                    "cells"
                ]
                layout = {tuple(c["position"]): c for c in self.layout.to_jsonable()}
                missing = [
                    t
                    for t in targets
                    if layout.get(tuple(t["position"]), {}).get("name") != t["block"]
                    or any(
                        layout.get(tuple(t["position"]), {}).get("properties", {}).get(k) != v
                        for k, v in t["properties"].items()
                    )
                ]
                priority += (
                    "Build bit0 carry AND using this exact disclos"
                    "ed checklist, in up "
                    "to32 primitives with real supports before att"
                    "achments. Preserve v"
                    "erified XOR. A branch travels from actual A l"
                    "ever46,64,5 along y6"
                    "4,z4 to53 with a west-facing repeater52,z4 re"
                    "storing strength, th"
                    "en south to main repeater53,z13. N branch fro"
                    "m actual n-port48,64"
                    ",10 drives repeater49,z10 west into stone50,z"
                    "10; torch51,z10 east"
                    " inverts n, side wire51,z11..14 and west repe"
                    "ater52,z14 feeds com"
                    "parator53,z14 side. Main A and side NOTn are "
                    "normalized15, so sub"
                    "tract comparator gives A AND n. Carry output "
                    "repeater53,z15 north"
                    ", probe53,z16. This is actual hardware geomet"
                    "ry, not computed out"
                    "put. Once missing zero, exercise actual two i"
                    "nput levers through "
                    "all four pairs; read BOTH sum42,64,15 and car"
                    "ry53,64,16, expectin"
                    "g00->00,01->10,10->10,11->01. Read actual lev"
                    "er states and do not"
                    " trust action names. Save truth table in note"
                    "s; bit0 carries in0."
                    " Continue real shadow/A/O and remaining fulla"
                    "dder lanes once this"
                    " subcircuit passes, without notes-only empty "
                    "intentions. "
                    + json.dumps(
                        {
                            "missing": missing[:32],
                            "remaining": len(missing),
                            "subcircuit_only": True,
                        }
                    )
                )
            if (
                current == "arithmetic"
                and Path("demo/arithmetic-section-design-targets.json").exists()
            ):
                import json

                section = json.loads(
                    Path("demo/arithmetic-section-design-targets.json").read_text()
                )
                layout = {tuple(c["position"]): c for c in self.layout.to_jsonable()}
                missing = [
                    t
                    for t in section["cells"]
                    if layout.get(tuple(t["position"]), {}).get("name") != t["block"]
                    or any(
                        layout.get(tuple(t["position"]), {}).get("properties", {}).get(k) != v
                        for k, v in t["properties"].items()
                    )
                ]
                priority += (
                    "NEXT EXACT DISCLOSED SECTION: "
                    + section["scope"]
                    + ". "
                    + section["notes"]
                    + " All previous bit0 work is already verified; "
                    "stop rebuilding it o"
                    "r extending SUM0. Use next32 missing cells be"
                    "low with explicit su"
                    "pport dependencies; the floor at y71 is not n"
                    "aturally stone, buil"
                    "d each listed support. Do not claim construct"
                    "ion equals behavior."
                    " "
                    + json.dumps(
                        {
                            "missing": missing[:32],
                            "remaining": len(missing),
                            "inputs": section["source_inputs"],
                            "sum_probe": section["sum_probe"],
                            "carry_probe": section["carry_probe"],
                        }
                    )
                )
            return request.model_copy(
                update={
                    "system": priority + request.system + guidance + "\n" + priority,
                    "thinking": False,
                }
            )

        self.context.request = persistent_guidance
        if prior_assistance is not None:
            manifest.data.setdefault("assistance_history", []).append(prior_assistance)
        manifest.data["assistance"] = {
            "kind": "agent-directed construction with server-assisted placement",
            "physical_actions": False,
            "batch_construction": {
                "user_authorized": True,
                "jev_first_action_per_section": True,
                "remaining_order": "dependency-ready planner order",
                "per_block_filming": False,
                "primitive_accounting": "unchanged",
            },
            "server_visuals": True,
            "unaided_benchmark": False,
            "seed": seed,
            "initial_sol_design": {"path": str(design_path), "text": design_text},
            "cumulative_budget_preservation": {
                "resuming": resuming,
                "prior_calls": prior_calls,
                "prior_actions": prior_actions,
            },
            "provider_request_overrides": {
                "thinking": False,
                "reason": (
                    "Two prior requests consumed the reasoning tok"
                    "en cap without JSON; "
                    "use the persisted initial assisted design"
                ),
                "persistent_module_guidance": True,
                "timeout_seconds": 180,
            },
        }
        manifest.save()
        print("CAMERA_PLACEMENT_CHECKPOINT", flush=True)
        gate = Path("demo/allow-server-assisted-placement")
        started = time.monotonic()
        while not gate.exists():
            if time.monotonic() - started > 300:
                raise RuntimeError("Capture gate timed out before agent construction")
            time.sleep(0.5)


loop_module.TrialLoop = AssistedLoop
run_trial(
    TrialConfiguration(
        mode="provider",
        task="computer",
        keep_agent_connected=True,
        planner_model="deepseek-ai/DeepSeek-V4-Pro-0813",
        planner_project="nathanweldegiorgis731-minerva-university/Noob-agent",
    ),
    resume=resume_path,
)
