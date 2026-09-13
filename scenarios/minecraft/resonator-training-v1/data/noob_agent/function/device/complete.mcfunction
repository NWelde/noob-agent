scoreboard players remove #inputs noob_agent.state 2
scoreboard players add #inputs_consumed noob_agent.state 2
scoreboard players add #outputs_created noob_agent.state 1
scoreboard players set #timer noob_agent.state 0
item replace block 2 100 0 container.0 with minecraft:echo_shard[minecraft:custom_name='{"text":"Charged Key","italic":false}'] 1
scoreboard players set #public_message noob_agent.state 5
title @a actionbar {"text":"A clear tone rings out.","color":"aqua"}
