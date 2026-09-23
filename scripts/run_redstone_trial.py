"""Dedicated redstone infrastructure entry point; run from the repository root."""

import argparse
from pathlib import Path

from noob_agent.redstone.behavior import run_behavior_negative_proof
from noob_agent.redstone.grading import recover_timeline, run_control_proof, run_timeline_proof
from noob_agent.redstone.modules import run_module_inspection
from noob_agent.redstone.reset import run_resets, run_smoke
from noob_agent.redstone.sidecar import run_observation
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
    args = parser.parse_args(argv)
    if args.trial:
        try:
            config = TrialConfiguration(
                mode=args.trial,
                planner_model=args.planner_model,
                planner_project=args.planner_project,
            )
        except ValueError:
            parser.error("Provider mode requires --planner-model; fixture rejects provider options")
        manifest = run_trial(config)
        print(manifest.path)
        return 2
    if args.planner_model or args.planner_project:
        parser.error("Planner options require --trial provider")
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
