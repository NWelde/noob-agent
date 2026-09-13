"""Render the Doom token-demo transcript from Weave calls, never SQLite."""

# ruff: noqa: E501

from __future__ import annotations

import argparse
import importlib
import time
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, cast

DEFAULT_OUTPUT = Path(".noob-agent/doom-demo-log.md")


def _calls(project: str) -> Iterable[Any]:
    weave = importlib.import_module("weave")
    weave.init(project)
    client = weave.get_client()
    return cast(Iterable[Any], client.get_calls(limit=10_000))


def _value(call: Any, name: str, default: Any = None) -> Any:
    return getattr(call, name, default)


def render(run_id: str, calls: Iterable[Any]) -> str:
    """Return a readable transcript for calls whose recorded experiment matches run_id."""
    rows = []
    for call in calls:
        inputs = _value(call, "inputs", {}) or {}
        experiment = inputs.get("experiment_id", "") if isinstance(inputs, dict) else ""
        if isinstance(experiment, str) and experiment.startswith(f"{run_id}-s"):
            rows.append(call)
    lines = [
        f"# Doom token-budget demo: `{run_id}`",
        "",
        "Source: Weave trace only. Registry validation verdicts are not traced.",
        "",
    ]
    for call in rows:
        inputs = _value(call, "inputs", {}) or {}
        op = _value(call, "op_name", _value(call, "op", "unknown"))
        lines.extend(
            [
                f"## {op}",
                "",
                f"- experiment: `{inputs.get('experiment_id', '-')}`",
                f"- purpose: `{inputs.get('purpose', '-')}`",
                f"- finish reason: `{inputs.get('finish_reason', '-')}`",
                f"- tokens: input `{inputs.get('input_tokens', '-')}`, output `{inputs.get('output_tokens', '-')}`",
                "",
            ]
        )
        reply = inputs.get("response_text") if isinstance(inputs, dict) else None
        reasoning = inputs.get("reasoning") if isinstance(inputs, dict) else None
        if reply:
            lines.extend(["### Reply", "", "```text", str(reply), "```", ""])
        if reasoning:
            lines.extend(
                ["<details><summary>Reasoning</summary>", "", str(reasoning), "", "</details>", ""]
            )
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--follow", action="store_true")
    args = parser.parse_args(argv)
    while True:
        try:
            text = render(args.run_id, _calls(args.project))
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text, encoding="utf-8")
        except Exception as error:
            print(f"Weave read failed; preserving the last good log: {error}")
        if not args.follow:
            return 0
        time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
