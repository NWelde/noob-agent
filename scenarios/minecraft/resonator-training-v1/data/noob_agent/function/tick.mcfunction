# Accept or reject newly dropped objects resting on the Resonator basin.
execute positioned 0.5 101.25 0.5 as @e[type=minecraft:item,tag=!noob_agent_seen,distance=..1.15,sort=nearest] if data entity @s Item{id:"minecraft:amethyst_shard"} run function noob_agent:device/accept_input
execute positioned 0.5 101.25 0.5 as @e[type=minecraft:item,tag=!noob_agent_seen,distance=..1.15,sort=nearest] unless data entity @s Item{id:"minecraft:amethyst_shard"} run function noob_agent:device/reject_input

# Edge-trigger the two stone controls so a held button activates only once.
execute if block 0 100 2 minecraft:stone_button[powered=true] if score #activate_latch noob_agent.state matches 0 positioned 0.5 100 2.5 as @p[distance=..4,limit=1] run function noob_agent:device/activate
execute if block 0 100 2 minecraft:stone_button[powered=true] run scoreboard players set #activate_latch noob_agent.state 1
execute unless block 0 100 2 minecraft:stone_button[powered=true] run scoreboard players set #activate_latch noob_agent.state 0
execute if block 4 101 0 minecraft:stone_button[powered=true] if score #gateway_latch noob_agent.state matches 0 positioned 4.5 101 0.5 as @p[distance=..4,limit=1] run function noob_agent:gateway/use
execute if block 4 101 0 minecraft:stone_button[powered=true] run scoreboard players set #gateway_latch noob_agent.state 1
execute unless block 4 101 0 minecraft:stone_button[powered=true] run scoreboard players set #gateway_latch noob_agent.state 0

execute if score #timer noob_agent.state matches 1.. run scoreboard players remove #timer noob_agent.state 1
