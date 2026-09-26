"""Transport behavioral checks with local processes, no Minecraft needed."""

import json
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
print('{{"ready":true,"version":"1.21.1"}}')
for line in sys.stdin:
    journal=json.load(open({str(manifest.path)!r}))
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
