# Executable negative-path check with no online player required.
function noob_agent:reset
scoreboard players set #negative_failures noob_agent.test 0
summon minecraft:armor_stand 0.5 100 -4.5 {Tags:["noob_agent_scenario","noob_agent_oracle_actor"],Invisible:1b,Marker:1b}
summon minecraft:item 0.5 101.25 0.5 {Tags:["noob_agent_scenario"],Item:{id:"minecraft:flint",count:1}}
execute as @e[type=minecraft:item,tag=noob_agent_scenario,limit=1] run function noob_agent:device/reject_input
execute unless score #public_message noob_agent.state matches 1 run scoreboard players add #negative_failures noob_agent.test 1
execute unless entity @e[type=minecraft:item,tag=noob_agent_scenario,limit=1] run scoreboard players add #negative_failures noob_agent.test 1
scoreboard players set #inputs noob_agent.state 1
execute as @e[type=minecraft:armor_stand,tag=noob_agent_oracle_actor,limit=1] run function noob_agent:device/activate
execute unless score #public_message noob_agent.state matches 2 run scoreboard players add #negative_failures noob_agent.test 1
execute unless score #inputs noob_agent.state matches 1 run scoreboard players add #negative_failures noob_agent.test 1
scoreboard players set #feedback_status noob_agent.test -1
execute if score #negative_failures noob_agent.test matches 0 run scoreboard players set #feedback_status noob_agent.test 1
execute if score #negative_failures noob_agent.test matches 0 run tellraw @a {"text":"Resonator feedback checks: PASS.","color":"green"}
execute if score #negative_failures noob_agent.test matches 1.. run tellraw @a {"text":"Resonator feedback checks: FAIL.","color":"red"}
