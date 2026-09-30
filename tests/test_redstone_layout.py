import json

from noob_agent.redstone.layout import VerifiedLayout, validate_declaration_layout


def test_verified_place_adds_cell_and_verified_break_removes_it():
    layout = VerifiedLayout()
    placed = {
        "effect_verified": True,
        "after": {"name": "minecraft:lever", "properties": {"powered": False}},
    }

    layout.record_action("place", [20, 65, 10], placed)
    assert layout.to_jsonable() == [
        {
            "position": [20, 65, 10],
            "name": "minecraft:lever",
            "properties": {"powered": False},
        }
    ]

    layout.record_action(
        "break",
        [20, 65, 10],
        {"effect_verified": True, "after": {"name": "minecraft:air", "properties": {}}},
    )
    assert layout.to_jsonable() == []


def test_failed_or_unknown_actions_do_not_change_layout():
    layout = VerifiedLayout()
    layout.record_action(
        "place",
        [1, 64, 1],
        {"effect_verified": False, "after": {"name": "minecraft:stone", "properties": {}}},
    )
    layout.record_action("place", [2, 64, 2], {"after": {"name": "minecraft:lever"}})
    layout.record_action("place", [3, 64, 3], None)
    assert layout.to_jsonable() == []


def test_authoritative_readback_reconciles_and_removes_stale_cells():
    layout = VerifiedLayout()
    layout.record_action(
        "place",
        [1, 64, 1],
        {"effect_verified": True, "after": {"name": "minecraft:stone", "properties": {}}},
    )
    layout.reconcile([1, 64, 1], {"name": "minecraft:air", "properties": {}})
    layout.reconcile([2, 64, 2], {"name": "minecraft:redstone_wire", "properties": {"power": 0}})
    assert layout.to_jsonable() == [
        {
            "position": [2, 64, 2],
            "name": "minecraft:redstone_wire",
            "properties": {"power": 0},
        }
    ]


def test_layout_dump_and_fingerprint_are_deterministic_and_json_friendly():
    first, second = VerifiedLayout(), VerifiedLayout()
    for layout, order in ((first, ((4, 64, 2), (1, 64, 1))), (second, ((1, 64, 1), (4, 64, 2)))):
        for x, y, z in order:
            layout.reconcile([x, y, z], {"name": "minecraft:stone", "properties": {}})
    assert first.to_jsonable() == second.to_jsonable()
    assert first.fingerprint() == second.fingerprint()
    json.dumps({"cells": first.to_jsonable(), "fingerprint": first.fingerprint()})


def test_layout_fingerprint_ignores_transient_signal_state():
    off, on = VerifiedLayout(), VerifiedLayout()
    off.reconcile(
        [1, 64, 1],
        {"name": "minecraft:lever", "properties": {"powered": False, "facing": "north"}},
    )
    on.reconcile(
        [1, 64, 1],
        {"name": "minecraft:lever", "properties": {"powered": True, "facing": "north"}},
    )

    assert off.fingerprint() == on.fingerprint()


def test_declaration_validation_reports_probe_and_control_block_mismatches():
    layout = VerifiedLayout()
    layout.reconcile([1, 64, 1], {"name": "minecraft:stone", "properties": {}})
    layout.reconcile([2, 64, 2], {"name": "minecraft:lever", "properties": {"powered": False}})
    declaration = {
        "probes": {"a": [{"position": [1, 64, 1], "block": "minecraft:redstone_wire"}]},
        "controls": [{"id": "load0", "position": [2, 64, 2], "role": "programming"}],
    }

    mismatches = validate_declaration_layout(layout, declaration)

    assert mismatches == [
        {
            "target": "probe a[0]",
            "position": [1, 64, 1],
            "expected": "minecraft:redstone_wire",
            "actual": "minecraft:stone",
        }
    ]


def test_declaration_validation_flags_air_claims():
    mismatches = validate_declaration_layout(
        VerifiedLayout(),
        {
            "probes": {"a": [{"position": [10, 70, 10], "block": "minecraft:redstone_lamp"}]},
            "controls": [{"id": "reset", "position": [20, 65, 10], "role": "reset"}],
        },
    )
    assert [item["actual"] for item in mismatches] == ["minecraft:air", "minecraft:air"]
