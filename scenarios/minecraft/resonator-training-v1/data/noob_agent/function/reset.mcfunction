# Cancel every scenario timer before reconstructing the fixed initial state.
schedule clear noob_agent:device/complete
schedule clear noob_agent:oracle/after_process
schedule clear noob_agent:oracle/finish_run
schedule clear noob_agent:oracle/reset_for_second_run

# Restore entities, dropped objects, players, messages, timers, and counters.
kill @e[tag=noob_agent_scenario]
kill @e[type=minecraft:item,x=-7,y=99,z=-7,dx=14,dy=7,dz=14]
clear @a
tp @a 0.5 100 -5.5 0 0
spawnpoint @a 0 100 -5
gamemode adventure @a
effect clear @a
effect give @a minecraft:saturation infinite 0 true
effect give @a minecraft:resistance infinite 4 true
title @a reset
scoreboard players reset * noob_agent.state

forceload add -1 -1 0 0
function noob_agent:build_room
scoreboard players set #inputs noob_agent.state 0
scoreboard players set #timer noob_agent.state 0
scoreboard players set #inputs_consumed noob_agent.state 0
scoreboard players set #outputs_created noob_agent.state 0
scoreboard players set #gateway_open noob_agent.state 0
scoreboard players set #public_message noob_agent.state 0
scoreboard players set #activate_latch noob_agent.state 0
scoreboard players set #gateway_latch noob_agent.state 0
scoreboard players set #scenario_seed noob_agent.state 20260912
