"""Metadata verification must fail closed and never imply footage authenticity."""

import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/check_hackathon_readiness.py"
SPEC = importlib.util.spec_from_file_location("hackathon_readiness_cli", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
CLI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLI)


def metadata():
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "60/1",
            },
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"duration": "117", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
    }


def test_metadata_pass_is_explicitly_limited_to_format(tmp_path, monkeypatch):
    video = tmp_path / "proof.mp4"
    video.touch()
    monkeypatch.setattr(CLI.shutil, "which", lambda name: "/usr/bin/ffprobe")
    monkeypatch.setattr(
        CLI.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps(metadata())),
    )
    report = CLI.video_readiness(video)
    assert report["format_ready"] is True
    assert "metadata_only" in report["scope"]


@pytest.mark.parametrize("defect", ["fps", "resolution", "audio", "duration", "malformed"])
def test_rejects_wrong_format_or_unparseable_metadata(tmp_path, monkeypatch, defect):
    video = tmp_path / "proof.mp4"
    video.touch()
    data = metadata()
    if defect == "fps":
        data["streams"][0]["avg_frame_rate"] = "30/1"
    elif defect == "resolution":
        data["streams"][0]["width"] = 1280
    elif defect == "audio":
        data["streams"].pop()
    elif defect == "duration":
        data["format"]["duration"] = "10"
    elif defect == "malformed":
        data = {}
    monkeypatch.setattr(CLI.shutil, "which", lambda name: "/usr/bin/ffprobe")
    monkeypatch.setattr(
        CLI.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps(data))
    )
    assert CLI.video_readiness(video)["format_ready"] is False


def test_timeout_is_sanitized(tmp_path, monkeypatch):
    video = tmp_path / "proof.mp4"
    video.touch()
    monkeypatch.setattr(CLI.shutil, "which", lambda name: "/usr/bin/ffprobe")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("private command", 30, stderr="private data")

    monkeypatch.setattr(CLI.subprocess, "run", timeout)
    report = CLI.video_readiness(video)
    assert report["format_ready"] is False
    assert "private" not in json.dumps(report)
