import pytest

from noob_agent.redstone.execution import ConstructionExecution, DependencyError


def action(id, kind, position, block=None, depends_on=(), properties=None):
    return {
        "id": id,
        "action": kind,
        "position": position,
        "block": block,
        "properties": properties or {},
        "depends_on": list(depends_on),
    }


def test_shuffled_wire_and_support_only_offer_wire_after_verified_support():
    queue = ConstructionExecution(
        [
            action("wire", "place", [4, 65, 4], "minecraft:redstone_wire"),
            action("base", "place", [4, 64, 4], "minecraft:stone"),
        ]
    )
    assert set(queue.eligible()) == {"base"}
    queue.start("base")
    queue.complete("base", {"effect_verified": False}, verified=False)
    assert queue.eligible() == {}
    assert queue.finish_status() == {
        "complete": False,
        "blocked": True,
        "remaining": ["wire", "base"],
        "failed": ["base"],
        "blockers": [
            {"action_id": "wire", "dependencies": ["base"], "failed_dependencies": ["base"]},
            {"action_id": "base", "dependencies": [], "failed_dependencies": ["base"]},
        ],
    }


def test_verified_support_releases_component():
    queue = ConstructionExecution(
        [
            action("wire", "place", [4, 65, 4], "minecraft:redstone_wire"),
            action("base", "place", [4, 64, 4], "minecraft:stone"),
        ]
    )
    queue.start("base")
    queue.complete("base", {"effect_verified": True}, verified=True)
    assert set(queue.eligible()) == {"wire"}


def test_break_is_required_before_same_cell_replacement_even_if_shuffled():
    queue = ConstructionExecution(
        [
            action("replacement", "place", [2, 64, 2], "minecraft:stone"),
            action("remove", "break", [2, 64, 2]),
        ]
    )
    assert set(queue.eligible()) == {"remove"}
    queue.start("remove")
    queue.complete("remove", {"effect_verified": True}, verified=True)
    assert set(queue.eligible()) == {"replacement"}


def test_unknown_explicit_dependency_rejected():
    with pytest.raises(DependencyError, match="Unknown dependency"):
        ConstructionExecution([action("x", "observe", [0, 64, 0], depends_on=["missing"])])


def test_dependency_cycle_rejected_including_implicit_constraints():
    with pytest.raises(DependencyError, match="cycle"):
        ConstructionExecution(
            [
                action("a", "observe", [0, 64, 0], depends_on=["b"]),
                action("b", "observe", [1, 64, 0], depends_on=["a"]),
            ]
        )


def test_finish_reports_pending_without_blocker_and_never_claims_complete():
    queue = ConstructionExecution([action("inspect", "observe", [0, 64, 0])])
    assert queue.finish_status() == {
        "complete": False,
        "blocked": False,
        "remaining": ["inspect"],
        "failed": [],
        "blockers": [],
    }


@pytest.mark.parametrize(
    ("component", "component_pos", "properties", "support_pos"),
    [
        ("minecraft:redstone_torch", [1, 65, 1], {}, [1, 64, 1]),
        ("minecraft:repeater", [1, 65, 1], {}, [1, 64, 1]),
        ("minecraft:comparator", [1, 65, 1], {}, [1, 64, 1]),
        ("minecraft:lever", [1, 65, 1], {"face": "floor", "facing": "north"}, [1, 64, 1]),
        ("minecraft:lever", [1, 65, 1], {"face": "ceiling", "facing": "north"}, [1, 66, 1]),
        ("minecraft:lever", [1, 65, 1], {"face": "wall", "facing": "north"}, [1, 65, 2]),
        ("minecraft:redstone_wall_torch", [1, 65, 1], {"facing": "east"}, [0, 65, 1]),
    ],
)
def test_supported_components_wait_for_oriented_support(
    component, component_pos, properties, support_pos
):
    queue = ConstructionExecution(
        [
            action("component", "place", component_pos, component, properties=properties),
            action("support", "place", support_pos, "minecraft:stone"),
        ]
    )
    assert set(queue.eligible()) == {"support"}
    queue.start("support")
    queue.complete("support", {"effect_verified": True}, verified=True)
    assert set(queue.eligible()) == {"component"}


def test_wall_torch_does_not_depend_on_wire_in_front_of_it():
    # Reproduces the false cycle seen in the model's last register intention.
    queue = ConstructionExecution(
        [
            action(
                "torch",
                "place",
                [24, 65, 19],
                "minecraft:redstone_wall_torch",
                properties={"facing": "north"},
            ),
            action("support", "place", [24, 65, 20], "minecraft:stone"),
            action("wire", "place", [24, 65, 18], "minecraft:redstone_wire", depends_on=["torch"]),
        ]
    )
    assert queue.dependencies["torch"] == {"support"}
    assert set(queue.eligible()) == {"support"}


def test_ambiguous_attachment_requires_explicit_dependency():
    with pytest.raises(DependencyError, match="Ambiguous attachment"):
        ConstructionExecution(
            [
                action("lever", "place", [1, 65, 1], "minecraft:lever"),
                action("support", "place", [1, 64, 1], "minecraft:stone"),
            ]
        )
    queue = ConstructionExecution(
        [
            action("lever", "place", [1, 65, 1], "minecraft:lever", depends_on=["support"]),
            action("support", "place", [1, 64, 1], "minecraft:stone"),
        ]
    )
    assert set(queue.eligible()) == {"support"}


def test_attached_component_break_precedes_existing_support_break():
    queue = ConstructionExecution(
        [
            action("remove_support", "break", [1, 64, 1]),
            action("remove_lever", "break", [1, 65, 1]),
        ],
        verified_layout=[
            {
                "position": [1, 65, 1],
                "name": "minecraft:lever",
                "properties": {"face": "floor", "facing": "north"},
            },
            {"position": [1, 64, 1], "name": "minecraft:stone", "properties": {}},
        ],
    )
    assert set(queue.eligible()) == {"remove_lever"}


def test_support_break_rejected_when_verified_attached_hardware_is_not_removed():
    with pytest.raises(DependencyError, match="attached hardware remains"):
        ConstructionExecution(
            [action("remove_support", "break", [1, 64, 1])],
            verified_layout=[
                {
                    "position": [1, 65, 1],
                    "name": "minecraft:repeater",
                    "properties": {"facing": "north"},
                }
            ],
        )


def test_cycle_diagnostic_names_ordered_actions_and_positions():
    with pytest.raises(DependencyError) as exc:
        ConstructionExecution([
            action("a", "observe", [0, 64, 0], depends_on=["b"]),
            action("b", "observe", [1, 64, 0], depends_on=["a"]),
        ])
    assert str(exc.value) == (
        "Dependency cycle: a@[0, 64, 0] -> b@[1, 64, 0] -> a@[0, 64, 0]"
    )
