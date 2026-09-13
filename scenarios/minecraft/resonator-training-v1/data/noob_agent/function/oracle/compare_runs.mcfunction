scoreboard players set #oracle_status noob_agent.test -1
execute unless score #oracle_failures noob_agent.test = #first_failures noob_agent.test run tellraw @a {"text":"Resonator oracle: runs differed.","color":"red"}
execute if score #oracle_failures noob_agent.test matches 0 if score #first_failures noob_agent.test matches 0 run scoreboard players set #oracle_status noob_agent.test 1
execute if score #oracle_failures noob_agent.test matches 0 if score #first_failures noob_agent.test matches 0 run tellraw @a {"text":"Resonator oracle: PASS (2 identical runs).","color":"green"}
execute if score #oracle_failures noob_agent.test matches 1.. run tellraw @a {"text":"Resonator oracle: FAIL.","color":"red"}
