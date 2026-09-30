import pytest
from test_redstone_actions import Reader, Transport

from noob_agent.redstone.actions import Actions, EffectMismatch
from noob_agent.redstone.trial import TrialManifest


def test_visual_server_placement_sync_precedes_command_and_requires_readback(tmp_path, monkeypatch):
    monkeypatch.setenv("NOOB_SERVER_VISUALS", "1")
    monkeypatch.setenv("NOOB_PHYSICAL_ACTIONS", "0")

    class VisualReader(Reader):
        def request(self, request):
            if request["op"] == "player":
                return {"position": [1.5, 64, 2.5]}
            if request["op"] == "animate":
                return {"visual_only": True, "held": "stone"}
            return super().request(request)

    manifest = TrialManifest(tmp_path)
    transport = Transport()
    actions = Actions(manifest, transport, VisualReader())
    with pytest.raises(EffectMismatch):
        actions.apply("place", [1, 64, 1], "minecraft:stone")
    placement = next(i for i, c in enumerate(transport.commands) if c.startswith("setblock "))
    assert any(c.startswith("tp noobagentbot ") for c in transport.commands[:placement])
    visual = next(e for e in manifest.data["events"] if e["request"].get("op") == "animate")
    assert visual["outcome"] == "observed"
    assert manifest.data["events"][0]["result"]["effect_verified"] is False
