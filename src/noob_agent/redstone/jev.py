"""Bounded subprocess adapter for the shared Jev handler; never executes actions."""

from __future__ import annotations

import json
import math
import os
import selectors
import signal
import subprocess
import time
from typing import Any

MAX_BYTES = 262144


class JevError(RuntimeError):
    """Sanitized failure; callers must charge the attempt before dispatch."""


def action_request(state: dict[str, Any], criteria: dict[str, str]) -> dict[str, Any]:
    if not criteria or any(not k or not isinstance(v, str) or not v for k, v in criteria.items()):
        raise ValueError("Empty action criteria")
    return {
        "state": state,
        "questions": {
            "action": {
                "type": "choice",
                "instructions": "Choose exactly one offered bounded action.",
                "criteria": dict(criteria),
            }
        },
    }


def selected_action(response: dict[str, Any], request: dict[str, Any]) -> str:
    try:
        choice = response["answers"]["action"]["choice"]
        if type(choice) is not str or choice not in request["questions"]["action"]["criteria"]:
            raise ValueError
        return choice
    except (KeyError, TypeError, ValueError):
        raise JevError("Jev did not select an offered action ID") from None


class JevSubprocess:
    """One physical attempt, bounded stdin/stdout, no stderr capture or retry.

    Command injection is only for trusted harness fixtures. Production uses the
    repository's shared handler, with an outer deadline no longer than 30 seconds.
    The process group is killed and reaped on every failure, including interruption.
    """

    def __init__(self, *, command: list[str] | None = None) -> None:
        self.command = command or [
            "node",
            "--env-file-if-exists=.env",
            "scripts/jev_handler.mjs",
        ]

    def evaluate(self, request: dict[str, Any], *, timeout: float = 30) -> dict[str, Any]:
        if not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ValueError("Invalid Jev timeout")
        payload = json.dumps(request, allow_nan=False).encode() + b"\n"
        if len(payload) > MAX_BYTES:
            raise JevError("Jev request exceeds size limit")
        deadline = time.monotonic() + timeout
        process = None
        try:
            process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            assert process.stdin is not None and process.stdout is not None
            output = bytearray()
            offset = 0
            with selectors.DefaultSelector() as selector:
                os.set_blocking(process.stdin.fileno(), False)
                os.set_blocking(process.stdout.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE)
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError
                    for key, _ in selector.select(remaining):
                        if key.fileobj is process.stdin:
                            offset += os.write(
                                process.stdin.fileno(), payload[offset : offset + 4096]
                            )
                            if offset == len(payload):
                                selector.unregister(process.stdin)
                                process.stdin.close()
                        else:
                            chunk = os.read(process.stdout.fileno(), 4096)
                            if not chunk:
                                selector.unregister(process.stdout)
                            output.extend(chunk)
                            if len(output) > MAX_BYTES:
                                raise ValueError
            if process.wait(timeout=max(0.001, deadline - time.monotonic())) != 0:
                raise ValueError
            result = json.loads(output)
            if not isinstance(result, dict):
                raise ValueError
            selected_action(result, request)
            if not isinstance(result.get("usage"), dict):
                raise ValueError
            identity = result.get("response", {}).get("modelId")
            if not isinstance(identity, str) or not identity:
                raise ValueError
            # Shared CLI already strips raw headers/body. Keep only its public fields.
            return {
                "answers": result["answers"],
                "usage": result["usage"],
                "response": {"modelId": identity, "timestamp": result["response"].get("timestamp")},
            }
        except Exception:
            raise JevError("Jev subprocess failed or returned an invalid response") from None
        finally:
            if process is not None:
                # Kill descendants too, even if the direct child already exited.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                for stream in (process.stdin, process.stdout):
                    if stream is not None:
                        stream.close()
