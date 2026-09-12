#!/usr/bin/env python3
"""Validate the deterministic fake-connector transcript against the public contract.

Non-production scaffolding. This script is standalone: it imports only the
standard library and never imports, calls, or configures ``noob_agent``. It
reads the JSON transcript beside it and reports every contract violation it
finds, exiting non-zero when the transcript is not usable as a fixture.

Run:
    uv run python scaffolding/fake_connector/validate.py

An optional argument points the validator at a different transcript directory,
which is how the scaffold's own negative checks are exercised:

    uv run python scaffolding/fake_connector/validate.py /tmp/mutated-transcript
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

DEFAULT_TRANSCRIPT_DIR = Path(__file__).resolve().parent / "transcript"
MANIFEST_NAME = "manifest.json"

# Key sets transcribed from connector_contract.md. Production code owns the
# authoritative models; this scaffold restates the public shape on purpose, so
# that drift between the two shows up here instead of passing silently.
MANIFEST_KEYS = {
    "schema_version",
    "connector_version",
    "game_id",
    "observation_mode",
    "timing_model",
    "tools",
}
TOOL_DEFINITION_KEYS = {
    "name",
    "description",
    "argument_schema",
    "preconditions",
    "max_duration",
    "state_changing",
}
OBSERVATION_KEYS = {
    "episode_id",
    "sequence",
    "game_id",
    "scenario_id",
    "public_goal",
    "status",
    "player",
    "visible_objects",
    "messages",
    "last_action_id",
    "terminal",
    "terminal_reason",
    "logical_time",
}
PLAYER_KEYS = {"position", "orientation", "properties"}
VISIBLE_OBJECT_KEYS = {"object_id", "label", "position", "distance", "properties"}
MESSAGE_KEYS = {"kind", "text"}
TOOL_REQUEST_KEYS = {"action_id", "tool_name", "arguments"}
STEP_RESULT_KEYS = {
    "action_id",
    "sequence",
    "status",
    "code",
    "message",
    "observation",
    "state_changed",
    "primitive_actions_charged",
    "logical_duration",
    "wall_time_ms",
}

STATUSES = {"succeeded", "rejected", "failed", "unknown"}
# connector_contract.md enumerates failure codes only; "OK" is this scaffold's
# documented convention for a result that completed normally.
CODES = {
    "OK",
    "INVALID_TOOL",
    "INVALID_ARGUMENT",
    "PRECONDITION_FAILED",
    "UNREACHABLE",
    "NO_VISIBLE_TARGET",
    "GAME_REJECTED",
    "TIMEOUT_CONFIRMED",
    "TIMEOUT_UNKNOWN",
    "BUDGET_EXHAUSTED",
    "CONNECTOR_LOST",
}

# The connector must never expose private grader state, scenario answers,
# clean/faulty labels, held-out configuration, or the meaning of an
# unfamiliar mechanic.
BANNED_KEY_TOKENS = (
    "faulty",
    "grader",
    "ground_truth",
    "held_out",
    "heldout",
    "hidden",
    "is_clean",
    "clean_label",
    "private",
    "secret",
    "solution",
    "defect",
    "variant",
    "answer",
)
BANNED_VALUE_TOKENS = ("faulty", "grader", "ground_truth", "held_out", "heldout")


class Report:
    """Collect every violation instead of stopping at the first one."""

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.checks = 0

    def check(self, condition: bool, message: str) -> bool:
        self.checks += 1
        if not condition:
            self.failures.append(message)
        return bool(condition)


def walk(node: Any, path: str) -> Iterator[tuple[str, str | None, Any]]:
    """Yield every (path, key, value) in a decoded JSON document."""
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}"
            yield child, key, value
            yield from walk(value, child)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            child = f"{path}[{index}]"
            yield child, None, value
            yield from walk(value, child)


def check_keys(report: Report, where: str, node: Any, expected: set[str]) -> bool:
    """Require a mapping whose keys are exactly the contract's field set."""
    if not report.check(isinstance(node, dict), f"{where}: expected a JSON object"):
        return False
    actual = set(node)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    ok = report.check(not missing, f"{where}: missing field(s) {missing}")
    ok = report.check(not extra, f"{where}: unexpected field(s) {extra}") and ok
    return ok


def check_privacy(report: Report, where: str, document: Any) -> None:
    """Fail on any key or string value that could leak private state."""
    for path, key, value in walk(document, where):
        if key is not None:
            lowered = key.lower()
            for token in BANNED_KEY_TOKENS:
                report.check(
                    token not in lowered,
                    f"{path}: private field name contains {token!r}",
                )
        if isinstance(value, str):
            lowered = value.lower()
            for token in BANNED_VALUE_TOKENS:
                report.check(
                    token not in lowered,
                    f"{path}: value leaks private state token {token!r}",
                )


def load_entry(report: Report, path: Path) -> Any:
    """Parse one transcript file and require canonical, deterministic bytes."""
    raw = path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        report.check(False, f"{path.name}: invalid JSON ({error})")
        return None
    canonical = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    report.check(
        raw == canonical,
        f"{path.name}: not canonically ordered; rewrite with sorted keys and 2-space indent",
    )
    return data


def check_observation(report: Report, where: str, observation: Any, expected_sequence: int) -> None:
    """Validate one public snapshot, including its nested public collections."""
    if not check_keys(report, where, observation, OBSERVATION_KEYS):
        return
    report.check(
        observation["sequence"] == expected_sequence,
        f"{where}.sequence: expected {expected_sequence}, found {observation['sequence']!r}",
    )
    report.check(
        isinstance(observation["episode_id"], str) and observation["episode_id"] != "",
        f"{where}.episode_id: expected a non-empty string",
    )
    scenario_id = observation["scenario_id"]
    report.check(
        isinstance(scenario_id, str) and scenario_id != "",
        f"{where}.scenario_id: expected a non-empty string",
    )
    if isinstance(scenario_id, str):
        for token in ("clean", "faulty"):
            report.check(
                token not in scenario_id.lower(),
                f"{where}.scenario_id: public family ID must not encode {token!r}",
            )
    report.check(
        isinstance(observation["logical_time"], int) and observation["logical_time"] >= 0,
        f"{where}.logical_time: expected a non-negative integer",
    )
    report.check(
        isinstance(observation["terminal"], bool),
        f"{where}.terminal: expected a boolean",
    )
    if observation["terminal"] is True:
        report.check(
            isinstance(observation["terminal_reason"], str)
            and observation["terminal_reason"] != "",
            f"{where}.terminal_reason: required when terminal is true",
        )
    else:
        report.check(
            observation["terminal_reason"] is None,
            f"{where}.terminal_reason: must be null when terminal is false",
        )

    check_keys(report, f"{where}.player", observation["player"], PLAYER_KEYS)

    objects = observation["visible_objects"]
    if report.check(isinstance(objects, list), f"{where}.visible_objects: expected a list"):
        seen: set[str] = set()
        for index, item in enumerate(objects):
            item_where = f"{where}.visible_objects[{index}]"
            if not check_keys(report, item_where, item, VISIBLE_OBJECT_KEYS):
                continue
            object_id = item["object_id"]
            report.check(
                isinstance(object_id, str) and object_id != "",
                f"{item_where}.object_id: expected a non-empty string",
            )
            report.check(
                object_id not in seen,
                f"{item_where}.object_id: duplicate episode-local ID {object_id!r}",
            )
            seen.add(object_id)
            distance = item["distance"]
            report.check(
                distance is None or (isinstance(distance, (int, float)) and distance >= 0),
                f"{item_where}.distance: expected null or a non-negative number",
            )

    messages = observation["messages"]
    if report.check(isinstance(messages, list), f"{where}.messages: expected a list"):
        for index, item in enumerate(messages):
            check_keys(report, f"{where}.messages[{index}]", item, MESSAGE_KEYS)


def check_manifest(report: Report, manifest: Any) -> tuple[set[str], dict[str, bool]]:
    """Validate the frozen manifest and return its declared tool surface."""
    tool_names: set[str] = set()
    state_changing: dict[str, bool] = {}
    if manifest is None or not check_keys(report, MANIFEST_NAME, manifest, MANIFEST_KEYS):
        return tool_names, state_changing
    check_privacy(report, MANIFEST_NAME, manifest)
    tools = manifest["tools"]
    if not report.check(
        isinstance(tools, list) and len(tools) > 0,
        f"{MANIFEST_NAME}.tools: expected a non-empty list",
    ):
        return tool_names, state_changing
    for index, tool in enumerate(tools):
        tool_where = f"{MANIFEST_NAME}.tools[{index}]"
        if not check_keys(report, tool_where, tool, TOOL_DEFINITION_KEYS):
            continue
        name = tool["name"]
        report.check(name not in tool_names, f"{tool_where}.name: duplicate tool name {name!r}")
        report.check(
            isinstance(tool["max_duration"], int) and tool["max_duration"] > 0,
            f"{tool_where}.max_duration: expected a positive integer",
        )
        report.check(
            isinstance(tool["state_changing"], bool),
            f"{tool_where}.state_changing: expected a boolean",
        )
        report.check(
            isinstance(tool["preconditions"], list),
            f"{tool_where}.preconditions: expected a list",
        )
        tool_names.add(name)
        state_changing[name] = bool(tool["state_changing"])
    return tool_names, state_changing


def main(argv: list[str]) -> int:
    report = Report()
    transcript_dir = Path(argv[0]).resolve() if argv else DEFAULT_TRANSCRIPT_DIR

    if not transcript_dir.is_dir():
        print(f"FAIL: transcript directory not found: {transcript_dir}")
        return 1

    manifest_path = transcript_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        print(f"FAIL: manifest not found: {manifest_path}")
        return 1

    entry_paths = sorted(p for p in transcript_dir.glob("*.json") if p.name != MANIFEST_NAME)
    if not entry_paths:
        print(f"FAIL: no transcript entries found in {transcript_dir}")
        return 1

    tool_names, state_changing = check_manifest(report, load_entry(report, manifest_path))

    expected_sequence = 0
    seen_action_ids: set[str] = set()
    last_executed_action_id: str | None = None
    terminal_seen_in: str | None = None
    statuses_seen: set[str] = set()
    tools_used: set[str] = set()
    saw_reset = False

    for position, path in enumerate(entry_paths):
        name = path.name
        entry = load_entry(report, path)
        if entry is None:
            continue
        check_privacy(report, name, entry)
        if not report.check(isinstance(entry, dict), f"{name}: expected a JSON object"):
            continue

        report.check(
            terminal_seen_in is None,
            f"{name}: entry recorded after the terminal observation in {terminal_seen_in}",
        )

        kind = entry.get("kind")
        if kind == "reset":
            report.check(not saw_reset, f"{name}: a second reset entry is not allowed")
            report.check(position == 0, f"{name}: reset must be the first entry")
            saw_reset = True
            if check_keys(report, name, entry, {"kind", "observation"}):
                observation = entry["observation"]
                check_observation(report, f"{name}.observation", observation, 0)
                if isinstance(observation, dict):
                    report.check(
                        observation.get("last_action_id") is None,
                        f"{name}.observation.last_action_id: must be null after reset",
                    )
                    if observation.get("terminal") is True:
                        terminal_seen_in = name
            expected_sequence = 1
            continue

        if not report.check(kind == "step", f"{name}: unknown entry kind {kind!r}"):
            continue
        report.check(saw_reset, f"{name}: a step precedes the reset entry")
        if not check_keys(report, name, entry, {"kind", "request", "result"}):
            continue

        request = entry["request"]
        result = entry["result"]
        if not check_keys(report, f"{name}.request", request, TOOL_REQUEST_KEYS):
            continue
        if not check_keys(report, f"{name}.result", result, STEP_RESULT_KEYS):
            continue

        # Action-ID matching: every request receives one durable result carrying
        # the same action_id, and no action_id is reused within an episode.
        action_id = request["action_id"]
        report.check(
            isinstance(action_id, str) and action_id != "",
            f"{name}.request.action_id: expected a non-empty string",
        )
        report.check(
            result["action_id"] == action_id,
            f"{name}.result.action_id: {result['action_id']!r} does not match the request's "
            f"{action_id!r}",
        )
        report.check(
            action_id not in seen_action_ids,
            f"{name}.request.action_id: duplicate action_id {action_id!r}",
        )
        seen_action_ids.add(action_id)
        report.check(
            isinstance(request["arguments"], dict),
            f"{name}.request.arguments: expected a JSON object",
        )

        # Result statuses, codes, and budget accounting.
        status = result["status"]
        code = result["code"]
        report.check(status in STATUSES, f"{name}.result.status: unknown status {status!r}")
        report.check(code in CODES, f"{name}.result.code: unknown result code {code!r}")
        statuses_seen.add(status if status in STATUSES else "invalid")
        charged = result["primitive_actions_charged"]
        report.check(
            isinstance(charged, int) and charged >= 0,
            f"{name}.result.primitive_actions_charged: expected a non-negative integer",
        )
        for field in ("logical_duration", "wall_time_ms"):
            report.check(
                isinstance(result[field], int) and result[field] >= 0,
                f"{name}.result.{field}: expected a non-negative integer",
            )
        report.check(
            result["state_changed"] is None or isinstance(result["state_changed"], bool),
            f"{name}.result.state_changed: expected a boolean or null",
        )
        report.check(
            isinstance(result["message"], str),
            f"{name}.result.message: expected a string",
        )

        tool_name = request["tool_name"]
        tools_used.add(tool_name)
        if status == "rejected":
            # Rejected means the request was never sent to the game.
            report.check(
                code != "OK",
                f"{name}.result.code: a rejected request needs a failure code",
            )
            report.check(
                charged == 0,
                f"{name}.result.primitive_actions_charged: a rejected request consumes one "
                f"agent decision but no primitive call, found {charged!r}",
            )
            report.check(
                result["state_changed"] is False,
                f"{name}.result.state_changed: a rejected request never reached the game",
            )
            report.check(
                result["logical_duration"] == 0,
                f"{name}.result.logical_duration: a rejected request advances no game time",
            )
            if code == "INVALID_TOOL":
                report.check(
                    tool_name not in tool_names,
                    f"{name}.request.tool_name: {tool_name!r} is declared in the manifest, "
                    f"so INVALID_TOOL is the wrong code",
                )
        else:
            report.check(
                tool_name in tool_names,
                f"{name}.request.tool_name: {tool_name!r} is not declared in the manifest",
            )
            report.check(
                charged >= 1,
                f"{name}.result.primitive_actions_charged: an executed primitive is charged "
                f"at least once, found {charged!r}",
            )
            if not state_changing.get(tool_name, True):
                report.check(
                    result["state_changed"] is not True,
                    f"{name}.result.state_changed: {tool_name!r} is declared "
                    f"state_changing=false in the manifest",
                )

        # Sequence progression: 0 after reset, then one increment per recorded
        # step, with the embedded observation carrying the same sequence.
        report.check(
            result["sequence"] == expected_sequence,
            f"{name}.result.sequence: expected {expected_sequence}, found {result['sequence']!r}",
        )
        observation = result["observation"]
        check_observation(report, f"{name}.result.observation", observation, expected_sequence)
        expected_sequence += 1

        if isinstance(observation, dict):
            last_action_id = observation.get("last_action_id")
            if status == "rejected":
                report.check(
                    last_action_id == last_executed_action_id,
                    f"{name}.result.observation.last_action_id: a rejected request never reached "
                    f"the game, so this must still be {last_executed_action_id!r}, "
                    f"found {last_action_id!r}",
                )
            else:
                report.check(
                    last_action_id == action_id,
                    f"{name}.result.observation.last_action_id: expected {action_id!r}, "
                    f"found {last_action_id!r}",
                )
                last_executed_action_id = action_id
            if observation.get("terminal") is True:
                terminal_seen_in = name

    # Transcript-level coverage required by the scaffold's brief.
    report.check(saw_reset, "transcript: no reset entry found")
    report.check(terminal_seen_in is not None, "transcript: no terminal observation found")
    report.check("observe" in tools_used, "transcript: no observe action recorded")
    report.check(
        "succeeded" in statuses_seen, "transcript: no successful primitive action recorded"
    )
    report.check("rejected" in statuses_seen, "transcript: no rejected action recorded")

    # Hash the newline-normalized text, not the raw bytes, so the fingerprint is
    # identical whether the checkout stored LF or CRLF.
    digest = hashlib.sha256()
    for path in [manifest_path, *entry_paths]:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_text(encoding="utf-8").encode("utf-8"))

    if report.failures:
        print(f"FAIL: {len(report.failures)} of {report.checks} checks failed")
        for failure in report.failures:
            print(f"  - {failure}")
        return 1

    print(f"PASS: {report.checks} checks over {len(entry_paths) + 1} files")
    print(f"transcript sha256: {digest.hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
