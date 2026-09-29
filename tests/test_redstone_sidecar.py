"""Transport behavioral checks with local processes, no Minecraft needed."""

import json
import socket
import sys
from pathlib import Path

import pytest

from noob_agent.redstone.sidecar import Sidecar, SidecarError
from noob_agent.redstone.trial import TrialManifest


def fixture_process(body: str) -> list[str]:
    return [sys.executable, "-u", "-c", body]


def test_journals_before_delivery_and_preserves_actual_result(tmp_path: Path) -> None:
    manifest = TrialManifest(tmp_path)
    body = f"""
import json,sys
from pathlib import Path
from noob_agent.redstone.trial import TrialManifest
print('{{"ready":true,"version":"1.21.1"}}')
for line in sys.stdin:
    journal=TrialManifest.load_data(Path({str(manifest.path)!r}))
    assert journal['events'][-1]['outcome']=='unknown'
    print(json.dumps({{'ok':True,'result':{{'gameMode':'survival'}}}}))
"""
    with Sidecar(manifest, command=fixture_process(body)) as sidecar:
        assert sidecar.request({"op": "player"}) == {"gameMode": "survival"}
    assert manifest.data["events"][-1]["outcome"] == "observed"


def test_completed_registry_rejection_keeps_sidecar_available(tmp_path: Path) -> None:
    manifest = TrialManifest(tmp_path)
    body = """
import sys
print('{"ready":true,"version":"1.21.1"}')
for line in sys.stdin:
    if '"validate"' in line:
        print('{"ok":false,"error":"request_failed"}')
    else:
        print('{"ok":true,"result":{"gameMode":"creative"}}')
"""
    with Sidecar(manifest, command=fixture_process(body)) as sidecar:
        assert sidecar.request(
            {"op": "validate", "name": "minecraft:lever", "properties": {"facing": "up"}}
        ) == {"valid": False}
        assert sidecar.request({"op": "player"}) == {"gameMode": "creative"}
    assert all(event["outcome"] == "observed" for event in manifest.data["events"])


@pytest.mark.parametrize(
    "response", ["", "not json", '{"ok":true}', '{"ok":false,"error":"secret"}']
)
def test_failure_is_durable_sanitized_and_prevents_retry(tmp_path: Path, response: str) -> None:
    manifest = TrialManifest(tmp_path)
    body = f"""
import sys
print('{{"ready":true,"version":"1.21.1"}}')
sys.stdin.readline()
print({response!r})
"""
    with Sidecar(manifest, command=fixture_process(body)) as sidecar:
        with pytest.raises(SidecarError):
            sidecar.request({"op": "interact", "position": [1, 64, 1]})
        with pytest.raises(SidecarError):
            sidecar.request({"op": "player"})
    saved = json.loads(manifest.path.read_text())
    assert saved["events"][-1]["outcome"] == "unknown"
    assert saved["errors"][-1]["stage"] == "sidecar_exchange"
    assert "secret" not in manifest.path.read_text()


def test_exchange_deadline_stops_hung_process(tmp_path: Path) -> None:
    manifest = TrialManifest(tmp_path)
    body = 'import time; print(\'{"ready":true,"version":"1.21.1"}\'); time.sleep(10)'
    with Sidecar(manifest, command=fixture_process(body), timeout=0.1) as sidecar:
        with pytest.raises(SidecarError):
            sidecar.request({"op": "player"})
    assert manifest.data["events"][-1]["outcome"] == "unknown"


@pytest.mark.parametrize("ready", ['{"ready":true,"version":"1.20.1"}', "garbage"])
def test_startup_failure_is_sanitized_and_durable(tmp_path: Path, ready: str) -> None:
    manifest = TrialManifest(tmp_path)
    with pytest.raises(SidecarError, match="startup failed"):
        Sidecar(manifest, command=fixture_process(f"print({ready!r})"))
    assert manifest.data["errors"][0]["stage"] == "sidecar_startup"
    assert manifest.data["completion"]["status"] == "incomplete"


def test_raw_command_is_not_sent_or_recorded(tmp_path: Path) -> None:
    manifest = TrialManifest(tmp_path)
    body = 'import time; print(\'{"ready":true,"version":"1.21.1"}\'); time.sleep(10)'
    with Sidecar(manifest, command=fixture_process(body)) as sidecar:
        with pytest.raises(ValueError):
            sidecar.request({"op": "command", "command": "stop"})
    assert manifest.data["events"] == []


def test_persistent_connection_supports_requests_and_close_only_detaches_controller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = TrialManifest(tmp_path)
    controller, daemon = socket.socketpair()

    def attach(self: Sidecar) -> dict[str, object]:
        self.connection = controller
        return {"ready": True, "version": "1.21.1"}

    monkeypatch.setattr(Sidecar, "_connect_persistent", attach)

    with Sidecar(manifest, keep_connected=True) as sidecar:
        daemon.sendall(b'{"ok":true,"result":{"gameMode":"creative"}}\n')
        assert sidecar.request({"op": "player"}) == {"gameMode": "creative"}

    assert daemon.recv(100) == b'{"op": "player"}\n'
    assert daemon.fileno() >= 0
    assert daemon.recv(100) == b""
    daemon.close()


@pytest.mark.parametrize("changed", [True, False])
def test_unknown_persistent_interaction_reconciles_before_single_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: bool
) -> None:
    manifest = TrialManifest(tmp_path)
    controller, daemon = socket.socketpair()
    charged: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        Sidecar, "_connect_persistent", lambda self: {"ready": True, "version": "1.21.1"}
    )
    sidecar = Sidecar(
        manifest,
        keep_connected=True,
        recovery_charge=lambda kind, request: charged.append((kind, request.copy())),
    )
    sidecar.connection = controller
    before = {
        "position": [1, 64, 1],
        "name": "minecraft:lever",
        "properties": {"powered": False, "face": "floor"},
    }
    manifest.observed(manifest.attempt("sidecar", {"op": "block", "position": [1, 64, 1]}), before)

    def lost_reply(_timeout: float) -> dict[str, object]:
        raise SidecarError("lost")

    monkeypatch.setattr(sidecar, "_read_connection", lost_reply)
    recovered = {**before, "properties": {**before["properties"], "powered": changed}}
    calls: list[str] = []

    def recovery(request: dict[str, object]) -> dict[str, object]:
        calls.append(str(request["op"]))
        if request["op"] == "settle":
            return {"settled": True}
        if request["op"] == "block":
            return recovered if changed else before
        calls.append("retry")
        return {"before": before, "after": recovered}

    monkeypatch.setattr(sidecar, "_reconnect_persistent", lambda: None)
    monkeypatch.setattr(sidecar, "_recovery_request", recovery)
    try:
        result = sidecar.request({"op": "interact", "position": [1, 64, 1]})
    finally:
        sidecar.close()
        daemon.close()
    assert calls == (["settle", "block"] if changed else ["settle", "block", "interact", "retry"])
    assert result["after"] == recovered
    assert manifest.data["events"][1]["outcome"] == "observed"
    assert charged == (
        [
            ("charged_observation", {"op": "settle"}),
            ("charged_observation", {"op": "block", "position": [1, 64, 1]}),
        ]
        if changed
        else [
            ("charged_observation", {"op": "settle"}),
            ("charged_observation", {"op": "block", "position": [1, 64, 1]}),
            ("bounded_action", {"op": "interact", "position": [1, 64, 1]}),
        ]
    )


def test_unknown_interaction_stays_stopped_when_reconciliation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = TrialManifest(tmp_path)
    controller, daemon = socket.socketpair()
    monkeypatch.setattr(
        Sidecar, "_connect_persistent", lambda self: {"ready": True, "version": "1.21.1"}
    )
    sidecar = Sidecar(manifest, keep_connected=True)
    sidecar.connection = controller
    before = {"position": [1, 64, 1], "name": "minecraft:lever", "properties": {"powered": False}}
    manifest.observed(manifest.attempt("sidecar", {"op": "block", "position": [1, 64, 1]}), before)
    monkeypatch.setattr(
        sidecar, "_read_connection", lambda _timeout: (_ for _ in ()).throw(SidecarError())
    )
    monkeypatch.setattr(sidecar, "_reconnect_persistent", lambda: None)
    calls: list[str] = []

    def fail(request: dict[str, object]) -> dict[str, object]:
        calls.append(str(request["op"]))
        if request["op"] == "settle":
            return {"settled": True}
        raise SidecarError("unavailable")

    monkeypatch.setattr(sidecar, "_recovery_request", fail)
    try:
        with pytest.raises(SidecarError):
            sidecar.request({"op": "interact", "position": [1, 64, 1]})
    finally:
        daemon.close()
    assert calls == ["settle", "block"]
    assert manifest.data["events"][1]["outcome"] == "unknown"
    assert manifest.data["errors"][-1]["stage"] == "sidecar_reconcile"


def test_recovery_charge_refusal_prevents_reconciliation_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = TrialManifest(tmp_path)
    controller, daemon = socket.socketpair()
    monkeypatch.setattr(
        Sidecar, "_connect_persistent", lambda self: {"ready": True, "version": "1.21.1"}
    )
    charged: list[tuple[str, dict[str, object]]] = []

    def refuse(kind: str, request: dict[str, object]) -> None:
        charged.append((kind, request.copy()))
        raise RuntimeError("budget exhausted")

    sidecar = Sidecar(manifest, keep_connected=True, recovery_charge=refuse)
    sidecar.connection = controller
    before = {"position": [1, 64, 1], "name": "minecraft:lever", "properties": {"powered": False}}
    manifest.observed(manifest.attempt("sidecar", {"op": "block", "position": [1, 64, 1]}), before)
    monkeypatch.setattr(
        sidecar, "_read_connection", lambda _timeout: (_ for _ in ()).throw(SidecarError())
    )
    monkeypatch.setattr(sidecar, "_reconnect_persistent", lambda: None)
    dispatched: list[dict[str, object]] = []
    monkeypatch.setattr(sidecar, "_recovery_request", lambda request: dispatched.append(request))
    try:
        with pytest.raises(SidecarError):
            sidecar.request({"op": "interact", "position": [1, 64, 1]})
    finally:
        daemon.close()
    assert charged == [("charged_observation", {"op": "settle"})]
    assert dispatched == []
    assert manifest.data["events"][1]["outcome"] == "unknown"
