"""Controlled world-reader fixtures: software evidence, never circuit success."""

import pytest
from test_redstone_modules import declaration

from noob_agent.redstone.actions import Actions
from noob_agent.redstone.modules import validate_declaration
from noob_agent.redstone.trial import TrialManifest


def declared(module):
    value = declaration(module)
    value["controls"] += [
        {"id": f"i{i}", "role": "test_input", "position": [i + 3, 64, 1]} for i in range(9)
    ]

    def recipe(code):
        return [{"control": f"i{i}", "level": bool(code & (1 << i))} for i in range(9)]

    recipes = {}
    if module != "storage":
        recipes.update({f"load:{n}": recipe(n) for n in range(16)})
    if module in ("arithmetic", "output"):
        recipes.update({f"add:{n}": recipe(16 + n) for n in range(16)})
        recipes["out"] = recipe(32)
    if module == "storage":
        from noob_agent.redstone.behavior import STORAGE_PATTERNS

        recipes.update({f"address:{a}": recipe(64 + a) for a in range(8)})
        for words in STORAGE_PATTERNS:
            for a, word in enumerate(words):
                recipes[f"write:{a}:{word}"] = [
                    {"control": "program", "level": True},
                    *recipe(a * 64 + word),
                    {"wait": 1},
                    {"control": "program", "level": False},
                ]
    value["recipes"] = recipes
    return value


class WorldReader:
    """A controlled sequential device, driven by control levels, not expected results."""

    def __init__(self, actions, declaration, broken=False):
        self.actions, self.declaration = actions, declaration
        self.levels = {}
        self.a = self.o = self.strobe = self.address = 0
        self.words = [0] * 8
        self.broken = broken
        self.calls = []

    def timeline(self, recipe, *, cycles=1, sample_only=False):
        self.calls.append((recipe, cycles, sample_only))
        for op in recipe:
            if "control" in op:
                self.levels[op["control"]] = op["level"]
                code = sum(1 << i for i in range(9) if self.levels.get(f"i{i}"))
                if op["control"] == "program" and not op["level"]:
                    self.words[code // 64] = code % 64
                if self.levels.get("reset"):
                    self.a = self.o = self.strobe = 0
        code = sum(1 << i for i in range(9) if self.levels.get(f"i{i}"))
        if self.declaration.module == "storage":
            self.address = code % 8
        for _ in range(0 if sample_only else cycles):
            if code < 16:
                self.a = code
            elif code < 32:
                self.a = (self.a + code - 16) % 16
            elif code == 32:
                self.o = self.a
                self.strobe ^= 1

        def bits(n, width):
            return [15 if n & (1 << i) else 0 for i in reversed(range(width))]

        signals = {
            "a": bits(self.a, 4),
            "o": [int(bool(b)) for b in bits(self.o, 4)],
            "strobe": bits(self.strobe, 1),
            "address": bits(self.address, 3),
            "words": [b for w in self.words for b in bits(w, 6)],
            "readout": bits(self.words[self.address], 6),
        }
        signals = {k: v for k, v in signals.items() if k in self.declaration.probes}
        if self.broken:
            role = next(iter(signals))
            signals[role][0] = 15 if signals[role][0] == 0 else 0
        return {"snapshots": [{"phase": "settled", "signals": signals}], "duration": 200}


@pytest.mark.parametrize("module", ["register", "arithmetic", "storage", "output"])
@pytest.mark.parametrize("broken", [False, True])
def test_four_graders_compare_world_snapshots(tmp_path, module, broken):
    from noob_agent.redstone.behavior import grade_module

    actions = Actions(TrialManifest(tmp_path), None, None)
    decl = validate_declaration(declared(module), actions.contract)
    reader = WorldReader(actions, decl, broken)
    result = grade_module(actions, decl, control=reader)
    assert result["behavioral_passed"] is (not broken)
    assert bool(result["failed_checks"]) is broken
    assert result["scope"] == "public_module_behavior"
    assert result["checks"]
    assert actions.manifest.data["checks"][-1] == result
    if module == "register":
        assert len(result["checks"]) == 49
        assert sum(sample for _, _, sample in reader.calls) == 33


def test_signal_check_failure_reason_is_only_present_when_check_fails(tmp_path):
    from noob_agent.redstone.behavior import grade_module

    actions = Actions(TrialManifest(tmp_path), None, None)
    decl = validate_declaration(declared("register"), actions.contract)
    passing = grade_module(actions, decl, control=WorldReader(actions, decl))
    assert all("failure_reason" not in item for item in passing["checks"])

    broken_reader = WorldReader(actions, decl, broken=True)
    failing = grade_module(actions, decl, control=broken_reader)
    assert all(item["failure_reason"] == "signal_mismatch" for item in failing["failed_checks"])


def test_missing_signal_in_snapshot_has_probe_failure_reason(tmp_path):
    from noob_agent.redstone.behavior import grade_module

    actions = Actions(TrialManifest(tmp_path), None, None)
    decl = validate_declaration(declared("register"), actions.contract)
    reader = WorldReader(actions, decl)
    original = reader.timeline

    def missing(*args, **kwargs):
        snapshot = original(*args, **kwargs)
        snapshot["snapshots"][0]["signals"].pop("a")
        return snapshot

    reader.timeline = missing
    result = grade_module(actions, decl, control=reader, fail_fast=True)
    assert result["failed_checks"][0]["failure_reason"] == "missing_or_mismatched_probe"


def test_register_batch_preserves_ticks_and_reduces_timeline_count():
    from noob_agent.redstone.behavior import estimate_module_schedule
    from noob_agent.redstone.contract import MachineContract

    declaration = validate_declaration(compact_declared("register"), MachineContract())
    schedule = estimate_module_schedule(declaration)
    assert schedule["scheduled_ticks"] == 16_400
    assert schedule["timeline_count"] == 17
    schedules = [
        estimate_module_schedule(validate_declaration(compact_declared(module), MachineContract()))
        for module in ("register", "arithmetic", "storage", "output")
    ]
    assert sum(int(item["scheduled_ticks"]) for item in schedules) == 55_616
    assert sum(int(item["timeline_count"]) for item in schedules) == 63


def test_recipes_reject_commands_step_and_missing_cases(tmp_path):
    actions = Actions(TrialManifest(tmp_path), None, None)
    for bad in (
        [{"command": "say unsafe"}],
        [{"control": "step", "level": True}],
        [{"wait": True}],
        [{"control": "i0", "level": 1}],
    ):
        value = declared("register")
        value["recipes"]["load:0"] = bad
        with pytest.raises(ValueError):
            validate_declaration(value, actions.contract)
    from noob_agent.redstone.behavior import grade_module

    value = declared("register")
    del value["recipes"]["load:15"]
    decl = validate_declaration(value, actions.contract)
    reader = WorldReader(actions, decl)
    with pytest.raises(ValueError, match="Missing recipe"):
        grade_module(actions, decl, control=reader)
    assert not reader.calls


def test_behavior_grade_stops_when_declared_probe_is_not_present(tmp_path, monkeypatch):
    import noob_agent.redstone.behavior as behavior

    actions = Actions(TrialManifest(tmp_path), None, None)
    decl = validate_declaration(declared("register"), actions.contract)

    class MissingProbeGrader:
        max_ticks = 100_000
        ticks = 0

        def __init__(self, *args, **kwargs):
            self.probes_checked = []

        def probe(self, role, index):
            self.probes_checked.append((role, index))
            if role == "a" and index == 2:
                raise ValueError("Declared probe missing or mismatched")
            return {"position": decl.probes[role][index].position}

    monkeypatch.setattr(behavior, "GraderControl", MissingProbeGrader)
    result = behavior.grade_module(actions, decl)

    assert not result["behavioral_passed"]
    assert result["complete"]
    assert result["failed_checks"] == [{
        "reason": "declared_probe_missing_or_mismatched",
        "role": "a",
        "index": 2,
        "detail": "Declared probe missing or mismatched",
    }]
    assert len(result["checks"]) == 0


@pytest.mark.parametrize(
    "module,role",
    [("arithmetic", "o"), ("storage", "readout"), ("storage", "address"), ("output", "strobe")],
)
def test_independent_secondary_observations_cannot_be_hidden(tmp_path, module, role):
    from noob_agent.redstone.behavior import grade_module

    actions = Actions(TrialManifest(tmp_path), None, None)
    decl = validate_declaration(declared(module), actions.contract)
    reader = WorldReader(actions, decl)
    original = reader.timeline

    def corrupt(*args, **kwargs):
        snapshot = original(*args, **kwargs)
        bits = snapshot["snapshots"][0]["signals"][role]
        bits[-1] = 0 if bits[-1] else 1
        return snapshot

    reader.timeline = corrupt
    result = grade_module(actions, decl, control=reader)
    assert not result["behavioral_passed"]
    assert any(role in item["expected"] for item in result["failed_checks"])


def compact_declared(module):
    value = declared(module)
    value["recipes"] = {k: v for k, v in value["recipes"].items() if k == "out"}

    def mapped(base, fields):
        ops = [{"control": f"i{i}", "level": bool(base & (1 << i))} for i in range(9)]
        for start, width, parameter in fields:
            for bit in range(width):
                ops[start + bit]["level"] = {"parameter": parameter, "bit": bit}
        return ops

    templates = {}
    if module != "storage":
        templates["load"] = mapped(0, [(0, 4, "value")])
    if module in ("arithmetic", "output"):
        templates["add"] = mapped(16, [(0, 4, "value")])
    if module == "storage":
        templates["address"] = mapped(64, [(0, 3, "address")])
        templates["write"] = [
            {"control": "program", "level": True},
            *mapped(0, [(0, 6, "word"), (6, 3, "address")]),
            {"wait": 1},
            {"control": "program", "level": False},
        ]
    value["recipe_templates"] = templates
    return value


@pytest.mark.parametrize("module", ["register", "arithmetic", "storage", "output"])
def test_compact_declarations_drive_same_world_operations(tmp_path, module):
    from noob_agent.redstone.behavior import grade_module

    actions = Actions(TrialManifest(tmp_path), None, None)
    original = validate_declaration(declared(module), actions.contract)
    compact = validate_declaration(compact_declared(module), actions.contract)
    readers = [WorldReader(actions, decl) for decl in (original, compact)]
    for decl, reader in zip((original, compact), readers, strict=True):
        assert grade_module(actions, decl, control=reader)["behavioral_passed"]
    assert readers[0].calls == readers[1].calls


@pytest.mark.parametrize(
    "bad",
    [
        {"parameter": "value", "bit": True},
        {"parameter": "value", "bit": 4},
        {"parameter": "word", "bit": 0},
        {"parameter": "value", "bit": 0, "invert": 1},
        {"parameter": "value", "bit": 0, "expression": "exec()"},
        {"parameter": "value", "bit": -1},
    ],
)
def test_compact_mappings_fail_closed(bad):
    from noob_agent.redstone.contract import MachineContract

    value = compact_declared("register")
    value["recipe_templates"]["load"][0]["level"] = bad
    with pytest.raises(ValueError):
        validate_declaration(value, MachineContract())


@pytest.mark.parametrize(
    "mutation", ["reset", "step", "unknown", "overlap", "length", "wait", "family"]
)
def test_template_targets_bounds_and_ambiguity_rejected(mutation):
    from noob_agent.redstone.contract import MachineContract

    value = compact_declared("register")
    template = value["recipe_templates"]["load"]
    if mutation in ("reset", "step", "unknown"):
        template[0]["control"] = mutation
    elif mutation == "overlap":
        value["recipes"]["load:0"] = []
    elif mutation == "length":
        template.extend([{"wait": 1}] * 129)
    elif mutation == "wait":
        template.append({"wait": True})
    else:
        value["recipe_templates"]["write"] = template
    with pytest.raises(ValueError):
        validate_declaration(value, MachineContract())


def test_parameter_polarity_and_full_storage_domain():
    from noob_agent.redstone.contract import MachineContract
    from noob_agent.redstone.modules import resolve_recipe

    value = compact_declared("storage")
    decl = validate_declaration(value, MachineContract())
    for address in range(8):
        for word in range(64):
            recipe = resolve_recipe(decl, f"write:{address}:{word}")
            code = sum(1 << i for i, op in enumerate(recipe[1:10]) if op["level"])
            assert code == address * 64 + word
    for key in ("write:8:0", "write:0:64", "write:-1:0", "write:0:01", "write:0", "load:0"):
        with pytest.raises(ValueError):
            resolve_recipe(decl, key)
    value = compact_declared("register")
    value["recipe_templates"]["load"][0]["level"]["invert"] = True
    decl = validate_declaration(value, MachineContract())
    assert resolve_recipe(decl, "load:0")[0]["level"] is True
    assert resolve_recipe(decl, "load:1")[0]["level"] is False
