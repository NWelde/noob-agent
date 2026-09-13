# Tag, but never consume, an incorrect object so feedback is emitted only once.
tag @s add noob_agent_seen
scoreboard players set #public_message noob_agent.state 1
title @a actionbar {"text":"The device does not respond.","color":"gray"}
