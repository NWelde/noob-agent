"""Full reset evidence must reject even a single mismatched layer or inventory slot."""

import copy

import pytest

from noob_agent.redstone.reset import (
    BASELINE,
    ResetError,
    baseline_hash,
    verify_player,
    verify_scan,
)


def test_baseline_hash_is_stable_and_covers_inventory_and_world() -> None:
    assert baseline_hash() == baseline_hash()
    changed = copy.deepcopy(BASELINE)
    changed["inventory"][0]["count"] = 1
    assert baseline_hash(changed) != baseline_hash()
    changed = copy.deepcopy(BASELINE)
    changed["layers"][20]["sha256"] = "wrong"
    assert baseline_hash(changed) != baseline_hash()


def test_complete_scan_rejects_missing_or_changed_layer() -> None:
    scan = {"layers": copy.deepcopy(BASELINE["layers"]), "blocks": 96 * 112 * 36}
    verify_scan(scan)
    for bad in [dict(scan, blocks=1), dict(scan, layers=scan["layers"][:-1])]:
        with pytest.raises(ResetError):
            verify_scan(bad)
    scan["layers"][18]["sha256"] = "corrupt"
    with pytest.raises(ResetError):
        verify_scan(scan)


def test_exact_player_inventory_pose_and_radian_conversion() -> None:
    player = dict(
        username="noobagentbot",
        position=[48.5, 64, 98.5],
        yaw=0,
        pitch=0,
        orientationUnits="radians",
        gameMode="creative",
        dimension="overworld",
        inventory=copy.deepcopy(BASELINE["inventory"]),
    )
    verify_player(player)
    for field, value in [
        ("yaw", 3.141592653589793),
        ("position", [48.5, 64, 98.6]),
        ("inventory", []),
        ("gameMode", "survival"),
    ]:
        with pytest.raises(ResetError):
            verify_player(dict(player, **{field: value}))
    player["inventory"].append({"slot": 5, "name": "stone", "count": 1})
    with pytest.raises(ResetError):
        verify_player(player)


def test_failed_full_scan_invalidates_prior_readiness_before_settings(tmp_path) -> None:
    from noob_agent.redstone.reset import TrustedReset
    from noob_agent.redstone.trial import TrialManifest

    class Commands:
        def __init__(self):
            self.sent = []

        def command(self, command):
            self.sent.append(command)
            return "response is not proof"

    class Scan:
        def request(self, request):
            if request["op"] == "settle":
                return {"settled": True}
            result = {"layers": copy.deepcopy(BASELINE["layers"]), "blocks": 96 * 112 * 36}
            result["layers"][30]["sha256"] = "one wrong cell anywhere changes layer hash"
            return result

    manifest = TrialManifest(tmp_path)
    manifest.data["initial_conditions"] = {"verified": True}
    transport = Commands()
    with pytest.raises(ResetError):
        TrustedReset(manifest, transport, Scan()).restore()
    assert manifest.data["resets"][-1]["verified"] is False
    assert manifest.data["template"]["verified"] is False
    assert manifest.data["initial_conditions"]["verified"] is False
    assert "seed" not in transport.sent
    assert len([c for c in transport.sent if c.startswith("fill ")]) == 36
