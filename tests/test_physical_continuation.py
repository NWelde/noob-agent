import copy

import pytest

from noob_agent.redstone.reset import BASELINE, ResetError
from noob_agent.redstone.trial import TrialManifest, verify_continuation


def player():
    return dict(
        username="noobagentbot",
        gameMode="creative",
        dimension="overworld",
        orientationUnits="radians",
        position=[20.7, 64, 10.6],
        yaw=4.7,
        pitch=0,
        inventory=copy.deepcopy(BASELINE["inventory"]),
    )


def check(tmp_path, monkeypatch, current, assisted=True, enabled=True):
    monkeypatch.setenv("NOOB_PHYSICAL_ACTIONS", "1" if enabled else "0")
    manifest = TrialManifest(tmp_path)
    manifest.data["continuation"] = {"state": {}}
    manifest.data["assistance"] = {"physical_actions": assisted}

    class Reader:
        def read(self, request):
            return current

    return verify_continuation(manifest, "computer", actions=Reader())


def test_physical_continuation_accepts_actual_walked_pose(tmp_path, monkeypatch):
    assert check(tmp_path, monkeypatch, player())["resume_check"]["blocks_checked"] == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("username", "other"),
        ("gameMode", "survival"),
        ("dimension", "nether"),
        ("inventory", []),
        ("position", [96, 64, 10]),
        ("position", [20, float("nan"), 10]),
        ("yaw", float("inf")),
    ],
)
def test_physical_continuation_rejects_invalid_player(tmp_path, monkeypatch, field, value):
    with pytest.raises(ResetError):
        check(tmp_path, monkeypatch, dict(player(), **{field: value}))


@pytest.mark.parametrize("assisted,enabled", [(False, True), (True, False)])
def test_nonphysical_continuation_retains_exact_spawn_check(
    tmp_path, monkeypatch, assisted, enabled
):
    with pytest.raises(ResetError):
        check(tmp_path, monkeypatch, player(), assisted, enabled)


@pytest.mark.parametrize("disclosed,enabled", [(True, True), (False, True), (True, False)])
def test_server_visual_continuation_requires_disclosure_and_enabled_adapter(
    tmp_path, monkeypatch, disclosed, enabled
):
    monkeypatch.setenv("NOOB_PHYSICAL_ACTIONS", "0")
    monkeypatch.setenv("NOOB_SERVER_VISUALS", "1" if enabled else "0")
    manifest = TrialManifest(tmp_path)
    manifest.data["continuation"] = {"state": {}}
    manifest.data["assistance"] = {"physical_actions": False, "server_visuals": disclosed}

    class Reader:
        def read(self, request):
            return player()

    if disclosed and enabled:
        assert (
            verify_continuation(manifest, "computer", actions=Reader())["resume_check"][
                "blocks_checked"
            ]
            == 0
        )
    else:
        with pytest.raises(ResetError):
            verify_continuation(manifest, "computer", actions=Reader())
