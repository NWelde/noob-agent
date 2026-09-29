"""Build or replay the physical redstone memory demo."""

import argparse

from noob_agent.redstone.memory_demo import run_memory_demo


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", action="store_true", help="Verify and replay existing hardware")
    parser.add_argument(
        "--upgrade-panel", action="store_true", help="Widen existing bit indicators"
    )
    parser.add_argument("--exhaustive", action="store_true", help="Test all 16 values on replay")
    parser.add_argument(
        "--pause", type=float, default=3, help="Seconds between visible demo phases"
    )
    args = parser.parse_args()
    if not 0 <= args.pause <= 30:
        parser.error("--pause must be between 0 and 30 seconds")
    if args.upgrade_panel and not args.replay:
        parser.error("--upgrade-panel requires --replay")
    run_memory_demo(
        replay=args.replay, pause=args.pause, upgrade=args.upgrade_panel, exhaustive=args.exhaustive
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
