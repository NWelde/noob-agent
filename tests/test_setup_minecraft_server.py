"""The judge-facing Minecraft setup writes a runnable local server without network in tests."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


def _load_setup():
    path = Path("scripts/setup_minecraft_server.py")
    spec = importlib.util.spec_from_file_location("setup_minecraft_server", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_download(module, jar_bytes: bytes):
    calls: list[str] = []

    def download(url: str, destination: Path) -> None:
        calls.append(url)
        destination.write_bytes(jar_bytes)

    return calls, download


def test_offline_uuid_matches_the_server_generated_uuid_for_the_bot() -> None:
    module = _load_setup()

    assert module.offline_uuid("noobagentbot") == "4e859362-be49-3f44-8e12-e3707dd8898c"


def test_setup_refuses_without_eula_acceptance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_setup()

    code = module.main(["--dir", str(tmp_path / "server"), "--skip-npm"])

    assert code == 2
    assert "eula" in capsys.readouterr().err.lower()
    assert not (tmp_path / "server").exists()


def test_setup_writes_server_files_bot_permissions_and_every_scenario_pack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_setup()
    jar = b"fake server jar"
    monkeypatch.setattr(module, "SERVER_JAR_SHA1", hashlib.sha1(jar).hexdigest())
    calls, download = _fake_download(module, jar)
    monkeypatch.setattr(module, "download", download)
    server = tmp_path / "server"

    code = module.main(["--dir", str(server), "--accept-eula", "--player", "judge", "--skip-npm"])

    assert code == 0
    assert calls == [module.SERVER_JAR_URL]
    assert (server / "server.jar").read_bytes() == jar
    assert "eula=true" in (server / "eula.txt").read_text()
    properties = (server / "server.properties").read_text()
    assert "server-port=25566" in properties
    assert "online-mode=false" in properties
    assert "white-list=true" in properties
    assert "server-ip=127.0.0.1" in properties
    assert "level-name=noob-agent-training" in properties
    ops = json.loads((server / "ops.json").read_text())
    assert ops == [
        {
            "uuid": "4e859362-be49-3f44-8e12-e3707dd8898c",
            "name": "noobagentbot",
            "level": 4,
            "bypassesPlayerLimit": False,
        }
    ]
    allowed = {entry["name"] for entry in json.loads((server / "whitelist.json").read_text())}
    assert allowed == {"noobagentbot", "judge"}
    datapacks = server / "noob-agent-training" / "datapacks"
    for pack in Path("scenarios/minecraft").iterdir():
        assert (datapacks / pack.name / "pack.mcmeta").is_file()


def test_setup_reuses_a_verified_jar_and_rejects_a_corrupt_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_setup()
    jar = b"good jar"
    monkeypatch.setattr(module, "SERVER_JAR_SHA1", hashlib.sha1(jar).hexdigest())
    server = tmp_path / "server"
    server.mkdir()
    (server / "server.jar").write_bytes(jar)
    calls, download = _fake_download(module, b"unused")
    monkeypatch.setattr(module, "download", download)

    assert module.main(["--dir", str(server), "--accept-eula", "--skip-npm"]) == 0
    assert calls == []

    (server / "server.jar").write_bytes(b"tampered")
    _, corrupt = _fake_download(module, b"still wrong")
    monkeypatch.setattr(module, "download", corrupt)

    assert module.main(["--dir", str(server), "--accept-eula", "--skip-npm"]) == 1
    assert "sha-1" in capsys.readouterr().err.lower()
