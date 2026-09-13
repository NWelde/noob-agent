scoreboard players set #can_start noob_agent.state 0
execute if score #timer noob_agent.state matches 0 if score #inputs noob_agent.state matches 2.. run scoreboard players set #can_start noob_agent.state 1
execute if score #timer noob_agent.state matches 0 if score #inputs noob_agent.state matches ..1 run scoreboard players set #public_message noob_agent.state 2
execute if score #timer noob_agent.state matches 0 if score #inputs noob_agent.state matches ..1 run title @s actionbar {"text":"The device hums, but nothing changes.","color":"gray"}
execute if score #can_start noob_agent.state matches 1 run scoreboard players set #timer noob_agent.state 40
execute if score #can_start noob_agent.state matches 1 run scoreboard players set #public_message noob_agent.state 4
execute if score #can_start noob_agent.state matches 1 run title @s actionbar {"text":"The Resonator begins to vibrate.","color":"light_purple"}
execute if score #can_start noob_agent.state matches 1 run schedule function noob_agent:device/complete 40t replace
