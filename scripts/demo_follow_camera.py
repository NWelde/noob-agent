"""Observer-only fixed-angle follow camera; never move the building bot."""

import json
import math
import time
from datetime import UTC, datetime
from pathlib import Path

from noob_agent.redstone.rcon import RconClient, UnknownOutcome

path = Path("demo/camera-follow-events.jsonl")
stop = Path("demo/stop-follow-camera")
config = Path("demo/camera-mode.json")
source = None
section = []
last_action = 0.0
last_shot = None
last_reframe = 0.0


def section_command():
    global source, section, last_action, last_shot, last_reframe
    settings = json.loads(config.read_text())
    proof = settings.get("proof_pose")
    if proof is not None:
        # Stable user/production-configured final proof shot; observer only.
        x, y, z, yaw, pitch = proof
        return f"tp nathanbeyene {x} {y} {z} {yaw} {pitch}"
    if source is None:
        journal_file = Path(settings["trial_manifest"]).with_name("events.jsonl")
        source = journal_file.open("rb")
        size = journal_file.stat().st_size
        if size > 2_000_000:
            source.seek(size - 2_000_000)
            source.readline()
    while True:
        offset = source.tell()
        line = source.readline()
        if not line:
            break
        if not line.endswith(b"\n"):
            source.seek(offset)
            break
        event = json.loads(line)["event"]
        if event["kind"] == "jev_call":
            plan = event.get("request", {}).get("state", {}).get("intention", {})
            points = [
                a["position"]
                for a in plan.get("actions", [])
                if a.get("action") in {"place", "break", "interact"}
            ]
            if points:
                section = points
        if event["kind"] == "bounded_action":
            last_action = datetime.fromisoformat(event["at"]).timestamp()
            if not section:
                section = [event["request"]["position"]]
    now = time.time()
    points = section
    if points:
        lower = [min(p[i] for p in points) for i in range(3)]
        upper = [max(p[i] for p in points) for i in range(3)]
        center = [(a + b) / 2 + 0.5 for a, b in zip(lower, upper)]
        span = max(upper[0] - lower[0], upper[2] - lower[2])
        distance = max(12.0, span * 1.15 + 8)
        if now - last_action >= 25:
            distance = max(18.0, distance + 4)
    else:
        # Wide arithmetic overview; later modules derive from the last section.
        p = section[-1] if section else [64, 64, 17]
        if p[2] >= 74:
            center, distance = [20, 66, 80], 34
        elif p[2] >= 38:
            center, distance = [46, 67, 52], 48
        elif p[0] >= 38:
            center, distance = [62, 66, 17], 38
        else:
            center, distance = [20, 66, 17], 34
    desired = (center[0], center[1], center[2], distance)
    changed = (
        last_shot is None
        or math.dist(desired[:3], last_shot[:3]) > 3
        or abs(distance - last_shot[3]) > 6
    )
    if changed and (last_shot is None or time.monotonic() - last_reframe >= 12):
        last_shot, last_reframe = desired, time.monotonic()
    cx, cy, cz, distance = last_shot
    height = distance * 0.55
    return (
        f"tp nathanbeyene {cx + distance * 0.6:.2f} {cy + height:.2f} "
        f"{cz + distance * 0.8:.2f} 143.13 28.81"
    )


with path.open("a", encoding="utf-8") as journal:
    while not stop.exists():
        command = (
            section_command()
            if config.exists()
            else "execute at noobagentbot run tp nathanbeyene ~3 ~3 ~4 143 23"
        )
        try:
            # The observer-only absolute-relative pose command is idempotent.
            # Short-lived connections avoid silently losing the shot on socket closure.
            with RconClient.dedicated() as camera:
                response = camera.command(command)
        except (UnknownOutcome, OSError) as error:
            journal.write(
                json.dumps(
                    {
                        "at": datetime.now(UTC).isoformat(),
                        "kind": "observer_connection_failure",
                        "error": type(error).__name__,
                    }
                )
                + "\n"
            )
            journal.flush()
            time.sleep(1.0)
            continue
        journal.write(
            json.dumps(
                {
                    "at": datetime.now(UTC).isoformat(),
                    "kind": "observer_only_section" if config.exists() else "observer_only_follow",
                    "command": command,
                    "response": response,
                }
            )
            + "\n"
        )
        journal.flush()
        time.sleep(1.0)
