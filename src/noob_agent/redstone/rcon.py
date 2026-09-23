"""Dedicated-server RCON transport. Never expose this command surface to a planner."""

from __future__ import annotations

import math
import socket
import struct
import time
from collections.abc import Callable
from pathlib import Path
from types import TracebackType

SERVER_DIRECTORY = Path(".noob-agent/redstone-server")
RCON_ADDRESS = ("127.0.0.1", 25577)


class RconError(RuntimeError):
    """Sanitized transport or configuration failure."""


class UnknownOutcome(RconError):
    """A command may have executed; do not retry or infer unchanged world state."""


def local_password() -> str:
    """Read only the dedicated server's local secret; never return it in evidence."""
    try:
        properties = {}
        for line in (SERVER_DIRECTORY / "server.properties").read_text().splitlines():
            if line and not line.startswith(("#", "!")) and "=" in line:
                key, value = line.split("=", 1)
                properties[key.strip()] = value.strip()
        expected = {"server-port": "25567", "rcon.port": "25577", "enable-rcon": "true"}
        if any(properties.get(key) != value for key, value in expected.items()):
            raise RconError("Dedicated server endpoint configuration does not match")
        if properties.get("server-ip") not in ("", "127.0.0.1"):
            raise RconError("Dedicated server must bind locally")
        password = properties.get("rcon.password", "")
        # Java properties escapes need a proper decoder before supporting them.
        # Fail closed rather than accidentally authenticate with a different secret.
        if not password or "\\" in password or "\0" in password:
            raise RconError("Dedicated RCON password missing or unsupported encoding")
        return password
    except OSError:
        raise RconError("Dedicated server properties unavailable") from None


class RconClient:
    """Sequential, finite RCON exchanges with a command-response barrier.

    Minecraft returns long results as multiple packets. An empty command with a
    separate ID provides an ordered response barrier without modifying the world.
    Any uncertainty invalidates the connection; commands are never retried.
    ``connect`` exists for local wire fixtures, not runtime endpoint selection.
    """

    def __init__(
        self,
        password: str,
        *,
        timeout: float = 5.0,
        connect: Callable[[], socket.socket] | None = None,
    ) -> None:
        if not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ValueError("RCON timeout must be finite and in (0, 30]")
        self.timeout = timeout
        self._socket: socket.socket | None = None
        self._sequence = 0
        try:
            self._socket = (
                connect() if connect else socket.create_connection(RCON_ADDRESS, timeout=timeout)
            )
            deadline = time.monotonic() + timeout
            self._send(1, 3, password, deadline)
            # Some servers send an empty RESPONSE_VALUE before AUTH_RESPONSE.
            for _ in range(2):
                request_id, kind, _ = self._receive(deadline)
                if request_id == -1:
                    raise RconError("RCON authentication rejected")
                if request_id != 1:
                    raise RconError("Invalid authentication response")
                if kind == 2:
                    self._sequence = 1
                    return
                if kind != 0:
                    break
            raise RconError("Missing authentication response")
        except (OSError, UnicodeError, RconError, ValueError):
            self.close()
            raise RconError("RCON connection or authentication failed") from None

    @classmethod
    def dedicated(cls) -> RconClient:
        return cls(local_password())

    def __enter__(self) -> RconClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    def _remaining(self, deadline: float) -> socket.socket:
        if self._socket is None:
            raise RconError("RCON connection closed")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        self._socket.settimeout(remaining)
        return self._socket

    def _send(self, request_id: int, kind: int, body: str, deadline: float) -> None:
        payload = struct.pack("<ii", request_id, kind) + body.encode("utf-8") + b"\0\0"
        self._remaining(deadline).sendall(struct.pack("<i", len(payload)) + payload)

    def _exact(self, size: int, deadline: float) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            chunk = self._remaining(deadline).recv(size - len(chunks))
            if not chunk:
                raise RconError("RCON disconnected")
            chunks.extend(chunk)
        return bytes(chunks)

    def _receive(self, deadline: float) -> tuple[int, int, bytes]:
        size = struct.unpack("<i", self._exact(4, deadline))[0]
        if not 10 <= size <= 4110:
            raise RconError("Invalid RCON packet length")
        payload = self._exact(size, deadline)
        if payload[-2:] != b"\0\0":
            raise RconError("Invalid RCON packet terminator")
        request_id, kind = struct.unpack("<ii", payload[:8])
        return request_id, kind, payload[8:-2]

    def command(self, command: str) -> str:
        """Trusted commands only. Replies are transport evidence, not action success."""
        if self._socket is None:
            raise RconError("RCON connection closed")
        if (
            not command
            or any(character in command for character in "\0\n\r")
            or len(command.encode("utf-8")) > 4096
        ):
            raise ValueError("Invalid RCON command")
        self._sequence += 2
        command_id, barrier_id = self._sequence - 1, self._sequence
        deadline = time.monotonic() + self.timeout
        try:
            self._send(command_id, 2, command, deadline)
            self._send(barrier_id, 2, "", deadline)
            result = bytearray()
            received = False
            while True:
                request_id, kind, body = self._receive(deadline)
                if kind != 0:
                    raise RconError("Unexpected RCON packet type")
                if request_id == barrier_id:
                    if not received:
                        raise RconError("Missing command response")
                    return result.decode("utf-8")
                if request_id != command_id:
                    raise RconError("Unexpected RCON response ID")
                received = True
                result.extend(body)
                if len(result) > 1024 * 1024:
                    raise RconError("RCON response exceeds limit")
        except (OSError, UnicodeError, RconError):
            self.close()
            raise UnknownOutcome("RCON command outcome unknown; connection closed") from None
