# Retained Minecraft redstone fixture

The cold/Builder/skill-reuse runner was retired on 2026-09-20 as part of the
Minecraft evaluation pivot. This data pack remains a connector fixture, not an
unfamiliar-mechanic benchmark. No replacement agent command is available yet.

The barrel contains one redstone block. The goal is to light the nearby lamp.
The public success message is emitted only when Minecraft reports the lamp lit.
The connector exposes the lamp, power block, and ordinary lit state. Tests retain
checks for reset commands, actual lamp-state feedback, and connector visibility.

The existing server setup installs this pack. On an existing server, the pack is
`scenarios/minecraft/redstone-lamp-v1`; its reset function is
`noob_agent_redstone:reset`. Reset rebuilds x=48..58, y=99..105, z=-4..4, clears
the bot inventory, and moves human players to a spectator viewpoint. Use only
in the dedicated test world. See [server documentation](minecraft-server.md).

Historical recordings and databases remain unchanged. Their cold gameplay and
failed Builder attempts are historical prototype evidence, not results for the
planned Jev/open-model comparison. The retired runner is recoverable from Git
history. See [the current plan](../hackathon_plan.md) for the replacement scope.
