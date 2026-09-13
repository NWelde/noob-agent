# Consume the complete dropped stack and preserve its exact count in device state.
execute store result score #dropped noob_agent.state run data get entity @s Item.count 1
scoreboard players operation #inputs noob_agent.state += #dropped noob_agent.state
kill @s
scoreboard players set #public_message noob_agent.state 3
title @a actionbar {"text":"The Resonator accepts the shard.","color":"light_purple"}
