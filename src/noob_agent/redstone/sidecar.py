"""Sequential dedicated Mineflayer transport; callers must enforce trial budgets."""

from __future__ import annotations

import json
import math
import os
import selectors
import subprocess
import time
from pathlib import Path
from types import TracebackType
from typing import Any

from noob_agent.redstone.trial import TrialManifest

SIDECAR = Path(__file__).resolve().parents[1] / "connectors/minecraft_sidecar/redstone.js"


class SidecarError(RuntimeError):
    """Sanitized failure. A delivered action may have executed; never auto-retry."""


class Sidecar:
    def __init__(
        self,
        manifest: TrialManifest,
        *,
        command: list[str] | None = None,
        timeout: float = 12,
    ) -> None:
        if not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ValueError("Invalid sidecar deadline")
        self.manifest = manifest
        self.timeout = timeout
        self.process: subprocess.Popen[bytes] | None = None
        self.buffer = bytearray()
        try:
            self.process = subprocess.Popen(
                command or ["node", str(SIDECAR)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            ready = self._read(25)
            if ready != {"ready": True, "version": "1.21.1"}:
                raise SidecarError("Invalid sidecar readiness")
        except BaseException as error:
            self._failure("sidecar_startup", error)
            if not isinstance(error, Exception):
                raise
            raise SidecarError("Sidecar startup failed") from None

    def _failure(self, stage: str, error: BaseException) -> None:
        self.close()
        self.manifest.data["errors"].append({"stage": stage, "type": type(error).__name__})
        self.manifest.save()

    def _read(self, timeout: float) -> dict[str, Any]:
        assert self.process is not None and self.process.stdout is not None
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            while b"\n" not in self.buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise SidecarError("Sidecar deadline exceeded")
                part = os.read(self.process.stdout.fileno(), 65536)
                if not part:
                    raise SidecarError("Sidecar disconnected")
                self.buffer.extend(part)
                if len(self.buffer) > 1024 * 1024:
                    raise SidecarError("Sidecar response too large")
            line, _, rest = self.buffer.partition(b"\n")
            self.buffer = bytearray(rest)
        result = json.loads(line)
        if not isinstance(result, dict):
            raise SidecarError("Invalid sidecar response")
        return result

    def request(self, request: dict[str, Any]) -> dict[str, Any]:
        if self.process is None:
            raise SidecarError("Sidecar closed")
        # Fixed small protocol; no credentials, chat or arbitrary command input.
        allowed = {
            "baseline": {"op"},
            "settle": {"op"},
            "player": {"op"},
            "block": {"op", "position"},
            "interact": {"op", "position"},
            "validate": {"op", "name", "properties"},
        }
        operation = request.get("op")
        if not isinstance(operation, str) or operation not in allowed:
            raise ValueError("Invalid sidecar operation")
        if set(request) - allowed[operation]:
            raise ValueError("Invalid sidecar request fields")
        payload = json.dumps(request, allow_nan=False).encode() + b"\n"
        if len(payload) > 4096:
            raise ValueError("Sidecar request too large")
        sequence = self.manifest.attempt("sidecar", request)
        try:
            assert self.process.stdin is not None
            self.process.stdin.write(payload)
            self.process.stdin.flush()
            reply = self._read(self.timeout)
            if reply.get("ok") is not True or not isinstance(reply.get("result"), dict):
                raise SidecarError("Sidecar request failed; outcome unknown")
            result: dict[str, Any] = reply["result"]
            self.manifest.observed(sequence, result)
            return result
        except BaseException as error:
            self._failure("sidecar_exchange", error)
            if not isinstance(error, Exception):
                raise
            raise SidecarError("Sidecar exchange failed; outcome unknown") from None

    def close(self) -> None:
        process, self.process = self.process, None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                stream.close()

    def __enter__(self) -> Sidecar:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def run_observation(root: Path) -> TrialManifest:
    """Live attachment evidence only: does not establish reset or trial readiness."""
    manifest = TrialManifest(root)
    manifest.data["kind"] = "infrastructure_sidecar_observation"
    manifest.save()
    try:
        with Sidecar(manifest) as sidecar:
            sidecar.request({"op": "player"})
            sidecar.request({"op": "block", "position": [48, 64, 95]})
    except SidecarError:
        pass  # The transport has already saved the sanitized cause and pending request.
    finally:
        manifest.finish_incomplete(
            [
                "Attachment/readback evidence only; no verified reset or construction trial",
                "Hashed baseline, player verification, action budgets and off/on/off smoke pending",
                "Trial recording missing",
            ]
        )
    return manifest
