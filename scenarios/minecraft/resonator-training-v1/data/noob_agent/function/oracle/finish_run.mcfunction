execute unless score #gateway_open noob_agent.state matches 1 run scoreboard players add #oracle_failures noob_agent.test 1
execute as @e[type=minecraft:armor_stand,tag=noob_agent_oracle_actor,limit=1] if items entity @s weapon.mainhand minecraft:echo_shard run scoreboard players add #oracle_failures noob_agent.test 1
execute if score #oracle_run noob_agent.test matches 2 run function noob_agent:oracle/compare_runs
execute if score #oracle_run noob_agent.test matches 1 run scoreboard players operation #first_failures noob_agent.test = #oracle_failures noob_agent.test
execute if score #oracle_run noob_agent.test matches 1 run function noob_agent:oracle/reset_for_second_run
