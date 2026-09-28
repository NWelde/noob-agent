from noob_agent.redstone.demo import (
    ActionPing,
    LAMP_POSITION,
    LEVER_POSITION,
    WIRE_POSITION,
    LampRepairCheck,
    prepare_lamp_repair,
)


class ObservedWorld:
    def __init__(self, states):
        self.states = iter(states)
        self.positions = []

    def observe(self, position):
        self.positions.append(position)
        return next(self.states)


def block(name, **properties):
    return {"name": name, "properties": properties}


def test_action_ping_announces_selected_block_above_agent_for_two_seconds():
    class Manifest:
        def __init__(self):
            self.events = []

        def attempt(self, kind, request):
            self.events.append((kind, request, None))
            return 0

        def observed(self, sequence, result):
            kind, request, _ = self.events[sequence]
            self.events[sequence] = (kind, request, result)

    class Transport:
        def __init__(self):
            self.commands = []

        def command(self, command):
            self.commands.append(command)
            return "ok"

    manifest, transport = Manifest(), Transport()

    ActionPing(manifest, transport).show("place", "minecraft:redstone_wire")

    assert manifest.events == [
        (
            "action_ping",
            {
                "action": "place",
                "block": "minecraft:redstone_wire",
                "label": "Place redstone wire",
            },
            {"shown": True, "duration_ticks": 40},
        )
    ]
    assert transport.commands[0].startswith("kill @e[type=minecraft:text_display")
    assert (
        "execute at noobagentbot run summon minecraft:text_display ~ ~2.45 ~"
        in transport.commands[1]
    )
    assert '\"text\":\"Place redstone wire\"' in transport.commands[1]
    assert '\"color\":\"#55ff55\"' in transport.commands[1]
    assert len(transport.commands) == 2


def test_action_ping_uses_short_labels_for_other_demo_actions():
    assert ActionPing.label("interact", None) == "Use lever"
    assert ActionPing.label("observe", None) == "Inspect block"
    assert ActionPing.label("break", None) == "Break block"


def test_prepare_lamp_repair_seeds_only_the_input_and_output_endpoints():
    class FixtureActions:
        def __init__(self):
            self.placed = []

        def observe(self, position):
            return block("minecraft:air")

        def apply(self, action, position, name, properties=None):
            self.placed.append((action, position, name, properties))
            return {"effect_verified": True}

    actions = FixtureActions()

    result = prepare_lamp_repair(actions)

    assert result["fixture_created_by_harness"] is True
    assert result["model_built"] is False
    assert result["fixture_effects_verified"] is True
    assert [item[1] for item in actions.placed] == [LEVER_POSITION, LAMP_POSITION]
    assert actions.placed[0][3]["powered"] is False


def test_lamp_repair_checker_requires_wire_and_off_on_off_behavior():
    world = ObservedWorld(
        [
            block("minecraft:lever", powered=False),
            block("minecraft:air"),
            block("minecraft:redstone_lamp", lit=False),
            block("minecraft:lever", powered=False),
            block("minecraft:redstone_wire", power=0),
            block("minecraft:redstone_lamp", lit=False),
            block("minecraft:lever", powered=True),
            block("minecraft:redstone_wire", power=15),
            block("minecraft:redstone_lamp", lit=True),
            block("minecraft:lever", powered=False),
            block("minecraft:redstone_wire", power=0),
            block("minecraft:redstone_lamp", lit=False),
        ]
    )
    checker = LampRepairCheck()

    missing = checker(world)
    wired_off = checker(world)
    powered = checker(world)
    final_off = checker(world)

    assert missing["passed"] is False
    assert missing["name"] == "connection_missing"
    assert wired_off["passed"] is True and wired_off["complete"] is False
    assert powered["passed"] is True and powered["complete"] is False
    assert final_off["passed"] is True and final_off["complete"] is True
    assert final_off["name"] == "repair_verified_off_on_off"
    assert (
        world.positions
        == [
            LEVER_POSITION,
            WIRE_POSITION,
            LAMP_POSITION,
        ]
        * 4
    )


def test_lamp_repair_checker_remembers_seen_power_across_sessions():
    checker = LampRepairCheck()
    checker.powered_output_seen = True
    restored = LampRepairCheck()

    restored.load_state(checker.save_state())

    assert restored.powered_output_seen is True
