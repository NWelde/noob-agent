function noob_agent:reset
summon minecraft:armor_stand 0.5 100 -4.5 {Tags:["noob_agent_scenario","noob_agent_oracle_actor"],Invisible:1b,Marker:1b}
summon minecraft:item 0.5 101.25 0.5 {Tags:["noob_agent_oracle_input"],Item:{id:"minecraft:amethyst_shard",count:2}}
execute as @e[type=minecraft:item,tag=noob_agent_oracle_input,limit=1] run function noob_agent:device/accept_input
execute as @e[type=minecraft:armor_stand,tag=noob_agent_oracle_actor,limit=1] run function noob_agent:device/activate
schedule function noob_agent:oracle/after_process 41t replace
