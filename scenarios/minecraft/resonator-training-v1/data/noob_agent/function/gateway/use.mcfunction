scoreboard players set @s noob_agent.state 0
execute if items entity @s weapon.mainhand minecraft:echo_shard run scoreboard players set @s noob_agent.state 1
execute if score @s noob_agent.state matches 0 run title @s actionbar {"text":"The gateway remains sealed.","color":"gray"}
execute if score @s noob_agent.state matches 1 run item replace entity @s weapon.mainhand with minecraft:air
execute if score @s noob_agent.state matches 1 run fill 5 101 -1 5 103 1 minecraft:air
execute if score @s noob_agent.state matches 1 run scoreboard players set #gateway_open noob_agent.state 1
execute if score @s noob_agent.state matches 1 run scoreboard players set #public_message noob_agent.state 6
execute if score @s noob_agent.state matches 1 run title @s actionbar {"text":"The sealed gateway opens.","color":"green"}
