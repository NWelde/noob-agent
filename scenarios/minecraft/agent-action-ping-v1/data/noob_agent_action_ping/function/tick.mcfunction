execute as @e[type=minecraft:text_display,tag=noob_agent_action_ping] at @a[name=noobagentbot,limit=1] run teleport @s ~ ~2.45 ~
scoreboard players add @e[type=minecraft:text_display,tag=noob_agent_action_ping] noob_agent_action_ping_age 1
kill @e[type=minecraft:text_display,tag=noob_agent_action_ping,scores={noob_agent_action_ping_age=40..}]
