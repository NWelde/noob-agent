"""Declaration/readback infrastructure only; fixtures are not working circuits."""

import copy

import pytest

from noob_agent.redstone.actions import ActionLimit, Actions
from noob_agent.redstone.contract import MachineContract
from noob_agent.redstone.modules import inspect_module, structural_identity, validate_declaration
from noob_agent.redstone.trial import TrialManifest


def declaration(module="register"):
    widths = {
        "register": {"a": 4},
        "arithmetic": {"a": 4, "o": 4},
        "storage": {"words": 48, "readout": 6, "address": 3},
        "output": {"o": 4, "strobe": 1},
    }[module]
    index = 0
    probes = {}
    for role, width in widths.items():
        probes[role] = []
        for _ in range(width):
            probes[role].append(
                {
                    "position": [index, 64, 0],
                    "block": "minecraft:redstone_lamp"
                    if role == "o"
                    else "minecraft:redstone_wire",
                }
            )
            index += 1
    return {
        "module": module,
        "probes": probes,
        "controls": [
            {"id": "reset", "role": "reset", "position": [0, 64, 1]},
            {"id": "step", "role": "step", "position": [1, 64, 1]},
            {"id": "program", "role": "programming", "position": [2, 64, 1]},
        ],
    }


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("probe", "Declared a probe 0 at x 0 y 63 z 0 outside build bounds"),
        ("control", "Declared control reset at x 0 y 64 z 98 outside build bounds"),
    ],
)
def test_out_of_bounds_declaration_identifies_probe_or_control(target, expected):
    value = declaration()
    if target == "probe":
        value["probes"]["a"][0]["position"] = [0, 63, 0]
    else:
        value["controls"][0]["position"] = [0, 64, 98]
    with pytest.raises(ValueError, match=expected):
        validate_declaration(value, MachineContract())


def test_aliased_declaration_names_both_roles_and_coordinate():
    value = declaration()
    value["controls"][0]["position"] = [0, 64, 0]
    with pytest.raises(ValueError, match="Aliased a probe 0 and control reset at x 0 y 64 z 0"):
        validate_declaration(value, MachineContract())


class World:
    def __init__(self, value):
        self.cells = {}
        for probes in value["probes"].values():
            for probe in probes:
                lamp = probe["block"] == "minecraft:redstone_lamp"
                self.cells[tuple(probe["position"])] = {
                    "name": probe["block"],
                    "properties": {"lit": True} if lamp else {"power": 15},
                }
        for control in value["controls"]:
            self.cells[tuple(control["position"])] = {
                "name": "minecraft:lever",
                "properties": {"face": "floor", "facing": "north", "powered": False},
            }

    def request(self, request):
        return {"position": request["position"], **self.cells[tuple(request["position"])]}

    def command(self, command):
        pytest.fail("Inspection must never mutate the world")


@pytest.mark.parametrize("module", ["register", "arithmetic", "storage", "output"])
def test_actual_readbacks_charged_but_never_behavioral_success(tmp_path, module):
    value = declaration(module)
    world = World(value)
    manifest = TrialManifest(tmp_path)
    actions = Actions(manifest, world, world)
    result = inspect_module(actions, validate_declaration(value, actions.contract))
    assert result["valid"] is True
    assert result["behavioral_passed"] is False
    assert result["scope"] == "interface_readback_only"
    assert actions.used == len(world.cells)
    assert all(result["signals"]["a"] if module == "register" else [True])
    assert manifest.data["final_grade"]["model_success"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        "alias",
        "bounds",
        "float",
        "missing",
        "extra",
        "width",
        "duplicate_control",
        "control_alias",
        "lamp",
        "missing_step",
    ],
)
def test_invalid_declarations_rejected_before_world_access(mutation):
    value = declaration("output")
    if mutation == "alias":
        value["probes"]["o"][1] = copy.deepcopy(value["probes"]["o"][0])
    elif mutation == "bounds":
        value["controls"][0]["position"][0] = 96
    elif mutation == "float":
        value["probes"]["o"][0]["position"][0] = 0.0
    elif mutation == "missing":
        del value["probes"]["strobe"]
    elif mutation == "extra":
        value["command"] = "setblock"
    elif mutation == "width":
        value["probes"]["o"].pop()
    elif mutation == "duplicate_control":
        value["controls"][1]["id"] = "reset"
    elif mutation == "control_alias":
        value["controls"][0]["position"] = value["probes"]["o"][0]["position"]
    elif mutation == "lamp":
        value["probes"]["o"][0]["block"] = "minecraft:redstone_wire"
    else:
        value["controls"].pop(1)
    with pytest.raises(ValueError):
        validate_declaration(value, MachineContract())


@pytest.mark.parametrize("bad", ["missing", "wrong_block", "ambiguous", "wrong_position"])
def test_missing_or_ambiguous_evidence_retains_detailed_failures(tmp_path, bad):
    value = declaration()
    world = World(value)
    cell = world.cells[(0, 64, 0)]
    if bad == "missing":
        cell["properties"] = {}
    elif bad == "wrong_block":
        cell["name"] = "minecraft:air"
    elif bad == "ambiguous":
        cell["properties"]["power"] = True
    else:
        cell["position"] = [90, 64, 90]
    actions = Actions(TrialManifest(tmp_path), world, world)
    result = inspect_module(actions, validate_declaration(value, actions.contract))
    assert result["valid"] is False
    assert result["failed_checks"][0]["target"] == "a[0]"
    assert result["failed_checks"][0]["actual"] == world.request({"position": [0, 64, 0]})


def test_budget_stops_inspection_and_keeps_prior_observations(tmp_path):
    value = declaration()
    world = World(value)
    manifest = TrialManifest(tmp_path)
    actions = Actions(manifest, world, world, max_actions=2)
    with pytest.raises(ActionLimit):
        inspect_module(actions, validate_declaration(value, actions.contract))
    assert actions.used == 2
    assert len([e for e in manifest.data["events"] if e["outcome"] == "observed"]) == 2


def test_structural_identity_excludes_only_block_specific_transient_state():
    base = {
        "position": [1, 64, 1],
        "name": "minecraft:repeater",
        "properties": {"facing": "north", "delay": 2, "powered": False, "locked": False},
    }
    changed = copy.deepcopy(base)
    changed["properties"].update(powered=True, locked=True)
    assert structural_identity(base) == structural_identity(changed)
    changed["properties"]["delay"] = 3
    assert structural_identity(base) != structural_identity(changed)
    changed = copy.deepcopy(base)
    changed["name"] = "minecraft:comparator"
    assert structural_identity(base) != structural_identity(changed)
    assert "powered" in base["properties"]  # caller evidence is not modified


def test_time_budget_blocks_inspection_before_read(tmp_path):
    value = declaration()
    world = World(value)
    now = [0.0]
    actions = Actions(TrialManifest(tmp_path), world, world, clock=lambda: now[0])
    now[0] = actions.contract.budgets.wall_seconds
    with pytest.raises(ActionLimit):
        inspect_module(actions, validate_declaration(value, actions.contract))
    assert actions.used == 0


def test_wrong_control_block_fails_closed(tmp_path):
    value = declaration()
    world = World(value)
    world.cells[(0, 64, 1)] = {"name": "minecraft:air", "properties": {}}
    actions = Actions(TrialManifest(tmp_path), world, world)
    result = inspect_module(actions, validate_declaration(value, actions.contract))
    assert result["valid"] is False
    assert result["failed_checks"][0]["target"] == "reset"
    assert result["controls"]["reset"] is None


def test_inspection_collects_every_unready_probe_and_control(tmp_path):
    value = declaration()
    world = World(value)
    world.cells[(0, 64, 0)]["name"] = "minecraft:air"
    world.cells[(0, 64, 1)]["name"] = "minecraft:stone"
    actions = Actions(TrialManifest(tmp_path), world, world)

    result = inspect_module(actions, validate_declaration(value, actions.contract))

    assert result["valid"] is False
    assert [failure["target"] for failure in result["failed_checks"]] == ["a[0]", "reset"]
    assert actions.used == len(world.cells)
