"""Sequential dedicated Mineflayer transport; callers must enforce trial budgets."""

from __future__ import annotations

import json
import math
import os
import selectors
import socket
import subprocess
import time
from pathlib import Path
from types import TracebackType
from typing import Any

from noob_agent.redstone.trial import TrialManifest

SIDECAR = Path(__file__).resolve().parents[1] / "connectors/minecraft_sidecar/redstone.js"
REPO_ROOT = Path(__file__).resolve().parents[3]
PERSISTENT_SOCKET = REPO_ROOT / ".noob-agent" / "redstone-sidecar.sock"
PERSISTENT_LOG = REPO_ROOT / ".noob-agent" / "redstone-sidecar.log"
PERSISTENT_PID = REPO_ROOT / ".noob-agent" / "redstone-sidecar.pid"


class SidecarError(RuntimeError):
    """Sanitized failure. A delivered action may have executed; never auto-retry."""


class Sidecar:
    def __init__(
        self,
        manifest: TrialManifest,
        *,
        command: list[str] | None = None,
        timeout: float = 12,
        keep_connected: bool = False,
    ) -> None:
        if not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ValueError("Invalid sidecar deadline")
        self.manifest = manifest
        self.timeout = timeout
        self.process: subprocess.Popen[bytes] | None = None
        self.buffer = bytearray()
        self.keep_connected = keep_connected
        self.connection: socket.socket | None = None
        self.connection_buffer = bytearray()
        try:
            if keep_connected:
                if command is not None:
                    raise ValueError("Persistent sidecar does not accept a fixture command")
                ready = self._connect_persistent()
            else:
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

    def _connect_persistent(self) -> dict[str, Any]:
        PERSISTENT_SOCKET.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + 35
        started: subprocess.Popen[bytes] | None = None
        while True:
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.settimeout(1)
            try:
                connection.connect(str(PERSISTENT_SOCKET))
                self.connection = connection
                return self._read_connection(25)
            except OSError:
                connection.close()
            if started is None:
                log = PERSISTENT_LOG.open("ab", buffering=0)
                try:
                    started = subprocess.Popen(
                        ["node", str(SIDECAR), "--persistent", str(PERSISTENT_SOCKET)],
                        cwd=REPO_ROOT,
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                    temporary = PERSISTENT_PID.with_suffix(".tmp")
                    temporary.write_text(f"{started.pid}\n", encoding="ascii")
                    temporary.replace(PERSISTENT_PID)
                finally:
                    log.close()
            if started.poll() is not None:
                raise SidecarError("Persistent Minecraft agent failed to start")
            if time.monotonic() >= deadline:
                raise SidecarError("Persistent Minecraft agent startup deadline exceeded")
            time.sleep(0.2)

    def _read_connection(self, timeout: float) -> dict[str, Any]:
        if self.connection is None:
            raise SidecarError("Persistent Minecraft agent is disconnected")
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(self.connection, selectors.EVENT_READ)
            while b"\n" not in self.connection_buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise SidecarError("Persistent Minecraft agent deadline exceeded")
                part = self.connection.recv(65536)
                if not part:
                    raise SidecarError("Persistent Minecraft agent disconnected")
                self.connection_buffer.extend(part)
                if len(self.connection_buffer) > 1024 * 1024:
                    raise SidecarError("Persistent Minecraft agent response too large")
            line, _, rest = self.connection_buffer.partition(b"\n")
            self.connection_buffer = bytearray(rest)
        result = json.loads(line)
        if not isinstance(result, dict):
            raise SidecarError("Invalid persistent Minecraft agent response")
        return result

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
        if self.keep_connected:
            if self.connection is None:
                raise SidecarError("Persistent Minecraft agent is disconnected")
        elif self.process is None:
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
            if self.keep_connected:
                if self.connection is None:
                    raise SidecarError("Persistent Minecraft agent is disconnected")
                self.connection.sendall(payload)
                reply = self._read_connection(self.timeout)
            else:
                assert self.process is not None and self.process.stdin is not None
                self.process.stdin.write(payload)
                self.process.stdin.flush()
                reply = self._read(self.timeout)
            if operation == "validate" and reply == {"ok": False, "error": "request_failed"}:
                # This read-only registry query has a completed negative result.
                # It cannot leave a world mutation uncertain or require bot teardown.
                invalid_result = {"valid": False}
                self.manifest.observed(sequence, invalid_result)
                return invalid_result
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
        if self.keep_connected:
            connection, self.connection = self.connection, None
            if connection is not None:
                connection.close()
            return
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


def stop_persistent_bot() -> bool:
    """Gracefully stop the demo bot daemon; false means no daemon is listening."""
    if not PERSISTENT_SOCKET.exists():
        return False
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(3)
    buffer = bytearray()
    try:
        connection.connect(str(PERSISTENT_SOCKET))
        with selectors.DefaultSelector() as selector:
            selector.register(connection, selectors.EVENT_READ)
            if not selector.select(3):
                return False
            buffer.extend(connection.recv(4096))
        if json.loads(buffer.partition(b"\n")[0]) != {"ready": True, "version": "1.21.1"}:
            return False
        connection.sendall(b'{"control":"shutdown"}\n')
        with selectors.DefaultSelector() as selector:
            selector.register(connection, selectors.EVENT_READ)
            if not selector.select(3):
                return False
            reply = json.loads(connection.recv(4096).partition(b"\n")[0])
        if reply != {"ok": True, "stopping": True}:
            return False
        pid = int(PERSISTENT_PID.read_text(encoding="ascii").strip())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                PERSISTENT_PID.unlink(missing_ok=True)
                return True
            time.sleep(0.1)
        return False
    except (OSError, json.JSONDecodeError):
        return False
    finally:
        connection.close()


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
