"""Wire-level fixtures: fragmentation, deadlines and uncertain mutations."""

import socket
import struct
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from noob_agent.redstone import rcon
from noob_agent.redstone.rcon import RconClient, RconError, UnknownOutcome


def packet(request_id: int, kind: int, body: str) -> bytes:
    payload = struct.pack("<ii", request_id, kind) + body.encode() + b"\0\0"
    return struct.pack("<i", len(payload)) + payload


def read_packet(sock: socket.socket) -> tuple[int, int, str]:
    def exact(count: int) -> bytes:
        data = b""
        while len(data) < count:
            chunk = sock.recv(count - len(data))
            if not chunk:
                raise EOFError
            data += chunk
        return data

    size = struct.unpack("<i", exact(4))[0]
    body = exact(size)
    request_id, kind = struct.unpack("<ii", body[:8])
    return request_id, kind, body[8:-2].decode()


def fixture_server(
    handler: Callable[[socket.socket], None],
) -> tuple[socket.socket, threading.Thread]:
    client, server = socket.socketpair()

    def run() -> None:
        with server:
            auth_id, kind, password = read_packet(server)
            assert (kind, password) == (3, "fixture-secret")
            server.sendall(packet(auth_id, 2, ""))
            handler(server)

    thread = threading.Thread(target=run)
    thread.start()
    return client, thread


def test_fragmented_multipart_response_is_complete_and_next_command_aligned() -> None:
    def serve(sock: socket.socket) -> None:
        for command in ("list", "seed"):
            request_id, kind, body = read_packet(sock)
            assert (kind, body) == (2, command)
            marker_id, marker_kind, marker = read_packet(sock)
            assert (marker_kind, marker) == (2, "")
            wire = packet(request_id, 0, "first ") + packet(request_id, 0, "second")
            wire += packet(marker_id, 0, "Unknown command")
            for byte in wire:
                sock.sendall(bytes([byte]))

    sock, thread = fixture_server(serve)
    with RconClient("fixture-secret", connect=lambda: sock) as client:
        assert client.command("list") == "first second"
        assert client.command("seed") == "first second"
    thread.join(timeout=1)
    assert not thread.is_alive()


def test_disconnect_after_mutation_is_unknown_and_never_retried() -> None:
    commands = []

    def serve(sock: socket.socket) -> None:
        commands.append(read_packet(sock)[2])

    sock, thread = fixture_server(serve)
    with RconClient("fixture-secret", connect=lambda: sock) as client:
        with pytest.raises(UnknownOutcome, match="unknown"):
            client.command("setblock 1 64 1 minecraft:stone")
        with pytest.raises(RconError, match="closed"):
            client.command("list")
    thread.join(timeout=1)
    assert commands == ["setblock 1 64 1 minecraft:stone"]


@pytest.mark.parametrize("wire", [struct.pack("<i", -1), packet(9876, 0, "wrong id")])
def test_invalid_response_fails_closed(wire: bytes) -> None:
    def serve(sock: socket.socket) -> None:
        read_packet(sock)
        read_packet(sock)
        sock.sendall(wire)

    sock, thread = fixture_server(serve)
    with RconClient("fixture-secret", connect=lambda: sock) as client:
        with pytest.raises(UnknownOutcome):
            client.command("list")
    thread.join(timeout=1)


def test_authentication_failure_does_not_disclose_password() -> None:
    client, server = socket.socketpair()

    def serve() -> None:
        with server:
            read_packet(server)
            server.sendall(packet(-1, 2, "fixture-secret"))

    thread = threading.Thread(target=serve)
    thread.start()
    with pytest.raises(RconError) as error:
        RconClient("fixture-secret", connect=lambda: client)
    assert "fixture-secret" not in str(error.value)
    assert "fixture-secret" not in repr(error.value)
    thread.join(timeout=1)


def test_command_has_total_deadline_even_when_peer_keeps_sending() -> None:
    def serve(sock: socket.socket) -> None:
        request_id, _, _ = read_packet(sock)
        read_packet(sock)
        try:
            for _ in range(100):
                sock.sendall(packet(request_id, 0, "progress"))
                time.sleep(0.01)
        except BrokenPipeError:
            pass

    sock, thread = fixture_server(serve)
    with RconClient("fixture-secret", timeout=0.05, connect=lambda: sock) as client:
        started = time.monotonic()
        with pytest.raises(UnknownOutcome):
            client.command("list")
        assert time.monotonic() - started < 0.5
    thread.join(timeout=1)
    assert not thread.is_alive()


@pytest.mark.parametrize("command", ["", "list\nstop", "list\0stop", "x" * 4097])
def test_invalid_commands_are_never_sent(command: str) -> None:
    received = []

    def serve(sock: socket.socket) -> None:
        received.append(sock.recv(1))

    sock, thread = fixture_server(serve)
    with RconClient("fixture-secret", connect=lambda: sock) as client:
        with pytest.raises(ValueError):
            client.command(command)
    thread.join(timeout=1)
    assert received == [b""]


@pytest.mark.parametrize(
    "override",
    ["server-port=25565", "rcon.port=25575", "enable-rcon=false", "server-ip=example.org"],
)
def test_password_loader_rejects_other_server_endpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, override: str
) -> None:
    (tmp_path / "server.properties").write_text(
        "server-port=25567\nrcon.port=25577\nenable-rcon=true\nserver-ip=127.0.0.1\n"
        f"rcon.password=fixture-secret\n{override}\n"
    )
    monkeypatch.setattr(rcon, "SERVER_DIRECTORY", tmp_path)
    with pytest.raises(RconError) as error:
        rcon.local_password()
    assert "fixture-secret" not in str(error.value)
