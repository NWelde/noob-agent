"""Resume a known non-dispatched validation boundary with cumulative budgets."""

import os
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import noob_agent.redstone.loop as loop_module
import noob_agent.redstone.trial as trial_module
from noob_agent.redstone.demo import ActionPing
from noob_agent.redstone.trial import TrialConfiguration, TrialManifest

os.environ["NOOB_PHYSICAL_ACTIONS"] = "1"
source = Path(sys.argv[1])
saved = TrialManifest.load_data(source)
assert saved["loop"]["reason"] in {"planner_validation_exhausted", "DependencyError"}
assert saved["assistance"]["physical_actions"] is True
assert not any(e["outcome"] == "unknown" for e in saved["events"])
last = saved["events"][-1]
assert (last["kind"] == "planner_validation" and last["result"]["world_actions"] == 0) or (
    saved["loop"]["reason"] == "DependencyError"
    and last["kind"] == "planner_call"
    and last["outcome"] == "observed"
)
assert isinstance(saved["continuation"]["state"], dict)
assert saved.get("timeline_resources", {}).get("state", "clean") == "clean"
count = len(saved.get("clean_validation_recoveries", [])) + 1
assert count <= 2
shutil.copy2(source, source.with_name(f"before-clean-validation-recovery-{count}.json"))
prior_actions = saved["action_budget"]["used"]
prior_calls = dict(saved["loop_budget"])
elapsed = (datetime.now(UTC) - datetime.fromisoformat(saved["started_at"])).total_seconds()
original_eligibility = trial_module.can_resume_model_failure
trial_module.can_resume_model_failure = lambda data: (
    (
        data.get("run_id") == saved["run_id"]
        and data.get("loop", {}).get("reason")
        in {"planner_validation_exhausted", "DependencyError"}
        and not any(e["outcome"] == "unknown" for e in data["events"])
    )
    or original_eligibility(data)
)
original_verification = trial_module.verify_continuation


def verify(*args, **kwargs):
    result = original_verification(*args, **kwargs)
    assert not result["resume_check"]["mismatches"], "Retained world changed; continuation refused"
    return result


trial_module.verify_continuation = verify
original_loop = loop_module.TrialLoop


class ResumedLoop(original_loop):
    def __init__(self, manifest, actions, planner, jev, **kwargs):
        kwargs["announce"] = ActionPing(manifest, actions.transport).show
        super().__init__(manifest, actions, planner, jev, **kwargs)
        self.used.update(prior_calls)
        actions.used += prior_actions
        actions.started -= elapsed
        self.deadline = min(
            self.deadline, time.monotonic() + self.contract.budgets.wall_seconds - elapsed
        )
        manifest.data.setdefault("clean_validation_recoveries", []).append(
            {
                "attempt": count,
                "prior_calls": prior_calls,
                "prior_actions": prior_actions,
                "elapsed_wall_seconds": elapsed,
                "known_world_actions_at_failure": 0,
            }
        )
        self.context.feedback(
            {
                "resume": "Retained world independently checked. Resume the register. "
                "depends_on may name only actions offered in this response; already built controls "
                "need no dependency. A lamp following a lever is not storage. "
                "Build load/hold/reset "
                "memory before requesting the register's public grading."
            }
        )
        original_request = self.context.request

        def request(observation):
            return original_request(observation).model_copy(
                update={
                    "thinking": True,
                    "system": (
                        "Assisted design guidance: Minecraft repeater facing names its INPUT "
                        "side, opposite signal travel. A lone repeater or torch does not store "
                        "a bit. Use real memory, such as four parallel storage repeaters locked "
                        "by perpendicular powered repeaters. STEP must allow one two-tick "
                        "update and then hold; RESET must clear stored bits. Data inputs, "
                        "STEP and RESET are distinct controls. Never remove a support while "
                        "its attached torch remains; remove the attachment first if redesigning. "
                        "Trace actual input, lock and output states before another redesign. "
                    )
                    + original_request(observation).system,
                }
            )

        self.context.request = request
        manifest.data["assistance"]["provider_request_overrides"] = {
            "thinking": True,
            "design_guidance": "repeater input direction and locked storage",
            "no_finished_layout": True,
        }
        manifest.save()


loop_module.TrialLoop = ResumedLoop
public = saved["configuration"]
configuration = TrialConfiguration(
    mode=public["mode"],
    task=public["task"],
    keep_agent_connected=public["keep_agent_connected"],
    planner_model=public["planner"]["model"],
    planner_project=public["planner"]["project"],
    session_action_limit=public.get("session_action_limit"),
    stop_after_module=public.get("stop_after_module"),
    register_workshop=public.get("register_workshop", False),
)
assert configuration.public() == public
result = trial_module.run_trial(configuration, resume=source)
print(result.path, result.data.get("loop"), flush=True)
