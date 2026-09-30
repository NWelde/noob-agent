"""Dedicated redstone infrastructure entry point; run from the repository root."""

import argparse
from pathlib import Path

from noob_agent.redstone.behavior import run_behavior_negative_proof
from noob_agent.redstone.grading import recover_timeline, run_control_proof, run_timeline_proof
from noob_agent.redstone.modules import run_module_inspection
from noob_agent.redstone.reset import run_resets, run_smoke
from noob_agent.redstone.sidecar import run_observation, stop_persistent_bot
from noob_agent.redstone.trial import (
    RUN_DIRECTORY,
    TrialConfiguration,
    run_preflight,
    run_trial,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument(
        "--preflight",
        action="store_true",
        help="Read-only probes; always incomplete",
    )
    modes.add_argument("--observe", action="store_true", help="Attach bot and save actual state")
    modes.add_argument("--reset-check", action="store_true", help="Restore and fully verify twice")
    modes.add_argument(
        "--smoke", action="store_true", help="Bounded lever off/on/off and two resets"
    )
    modes.add_argument(
        "--trial",
        choices=("fixture", "provider"),
        help="Explicit connected mode; fixture never calls providers",
    )
    modes.add_argument(
        "--stop-agent",
        action="store_true",
        help="Gracefully stop the persistent demo bot and disconnect it",
    )
    modes.add_argument(
        "--inspect-module", type=Path, help="Read-only declared interface inspection"
    )
    modes.add_argument(
        "--control-proof", action="store_true", help="Dedicated trusted control/timing proof"
    )
    modes.add_argument(
        "--timeline-proof", action="store_true", help="Exact server timeline fixture"
    )
    modes.add_argument(
        "--behavior-negative-proof",
        action="store_true",
        help="Live powered non-register must fail behavioral reset",
    )
    modes.add_argument("--recover-timeline", type=Path, help="Cleanup a stopped timeline manifest")
    parser.add_argument("--planner-model", help="Explicit W&B model ID for provider mode")
    parser.add_argument("--planner-project", help="Optional W&B inference project")
    parser.add_argument(
        "--session-action-limit",
        type=int,
        help="Use a smaller primitive-action cap per session (useful for restart checks)",
    )
    parser.add_argument(
        "--resume", type=Path, help="Continue a stopped trial from its saved manifest"
    )
    parser.add_argument(
        "--keep-agent-connected",
        action="store_true",
        help=(
            "Keep the Minecraft bot and current world connected after this demo (provider default)"
        ),
    )
    parser.add_argument(
        "--disconnect-after-trial",
        action="store_true",
        help="Opt out of persistent connection for a provider demo",
    )
    parser.add_argument(
        "--task",
        choices=("computer", "lamp-repair"),
        default="computer",
        help="Provider target; lamp-repair is a seeded, separately graded vertical slice",
    )
    parser.add_argument(
        "--stop-after-module",
        choices=("register",),
        help="Preserve the build after independently verified register behavior",
    )
    parser.add_argument(
        "--register-workshop",
        action="store_true",
        help="Fresh provider trial with a marked bare pad and optional one-bit diagnostics",
    )
    args = parser.parse_args(argv)
    if args.register_workshop and (
        args.trial != "provider" or args.task != "computer" or args.resume is not None
    ):
        parser.error("--register-workshop requires a fresh provider computer trial")
    if args.stop_after_module and (
        args.trial != "provider" or args.task != "computer" or args.disconnect_after_trial
    ):
        parser.error(
            "--stop-after-module requires a provider computer trial with the agent kept connected"
        )
    if args.stop_agent:
        if (
            args.stop_after_module
            or args.register_workshop
            or args.keep_agent_connected
            or args.planner_model
            or args.planner_project
            or args.task != "computer"
            or args.disconnect_after_trial
        ):
            parser.error("--stop-agent cannot be combined with trial options")
        print("stopped" if stop_persistent_bot() else "no persistent agent was running")
        return 0
    if args.trial:
        try:
            config = TrialConfiguration(
                mode=args.trial,
                task=args.task.replace("-", "_"),
                stop_after_module=args.stop_after_module,
                register_workshop=args.register_workshop,
                keep_agent_connected=(
                    args.keep_agent_connected
                    or (args.trial == "provider" and not args.disconnect_after_trial)
                ),
                session_action_limit=args.session_action_limit,
                planner_model=args.planner_model,
                planner_project=args.planner_project,
            )
        except ValueError:
            parser.error(
                "Provider mode requires --planner-model; fixture rejects provider options "
                "and lamp-repair"
            )
        manifest = (
            run_trial(config, resume=args.resume) if args.resume is not None else run_trial(config)
        )
        print(manifest.path)
        return 2
    if (
        args.stop_after_module
        or args.register_workshop
        or args.planner_model
        or args.planner_project
        or args.task != "computer"
        or args.keep_agent_connected
        or args.disconnect_after_trial
        or args.resume
        or args.session_action_limit is not None
    ):
        parser.error(
            "--planner-model, --planner-project, --task, --resume and connection options "
            "require --trial"
        )
    manifest = (
        run_behavior_negative_proof()
        if args.behavior_negative_proof
        else recover_timeline(args.recover_timeline)
        if args.recover_timeline
        else run_timeline_proof()
        if args.timeline_proof
        else run_control_proof()
        if args.control_proof
        else run_module_inspection(args.inspect_module)
        if args.inspect_module
        else run_preflight()
        if args.preflight
        else run_smoke()
        if args.smoke
        else run_resets()
        if args.reset_check
        else run_observation(RUN_DIRECTORY)
    )
    print(manifest.path)
    return 2  # Deliberately not a readiness or milestone-success exit code.


if __name__ == "__main__":
    raise SystemExit(main())
