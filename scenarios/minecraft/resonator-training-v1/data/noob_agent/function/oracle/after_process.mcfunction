# Assert the processing result, transfer its visible output, and use it at the gateway.
execute unless score #inputs noob_agent.state matches 0 run scoreboard players add #oracle_failures noob_agent.test 1
execute unless score #inputs_consumed noob_agent.state matches 2 run scoreboard players add #oracle_failures noob_agent.test 1
execute unless score #outputs_created noob_agent.state matches 1 run scoreboard players add #oracle_failures noob_agent.test 1
execute unless items block 2 100 0 container.0 minecraft:echo_shard run scoreboard players add #oracle_failures noob_agent.test 1
item replace entity @e[type=minecraft:armor_stand,tag=noob_agent_oracle_actor,limit=1] weapon.mainhand from block 2 100 0 container.0
item replace block 2 100 0 container.0 with minecraft:air
execute as @e[type=minecraft:armor_stand,tag=noob_agent_oracle_actor,limit=1] run function noob_agent:gateway/use
schedule function noob_agent:oracle/finish_run 1t replace
