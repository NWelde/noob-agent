# Non-benchmark easy diagnostic room: x=24..34, y=99..104, z=-4..4.
# It lies inside the always-loaded spawn chunks and outside the Resonator room
# plus the largest observation radius, so the two packs never see each other.
kill @e[tag=noob_agent_easy_scenario]
kill @e[type=minecraft:item,x=24,y=99,z=-4,dx=10,dy=5,dz=8]
forceload add 24 -4 34 4

# Shell, floor, and a bright back wall that becomes visible when the bars open.
fill 24 99 -4 34 104 4 minecraft:smooth_stone hollow
fill 25 100 -3 33 103 3 minecraft:air
fill 25 99 -3 33 99 3 minecraft:polished_andesite
fill 33 100 -3 33 103 3 minecraft:gold_block

# Gateway wall with a 3x3 iron-bar opening, and a lime-backed button beside it.
fill 31 100 -3 31 103 3 minecraft:deepslate_bricks
fill 31 100 -1 31 102 1 minecraft:iron_bars
setblock 31 101 -2 minecraft:lime_concrete
setblock 30 101 -2 minecraft:stone_button[face=wall,facing=west,powered=false]

# Invisible, non-colliding marker armor stands render only their names.
summon minecraft:armor_stand 30.5 102.5 -1.5 {Tags:["noob_agent_easy_scenario"],Invisible:1b,Marker:1b,CustomNameVisible:1b,CustomName:'{"text":"Gate Button","color":"green"}'}
summon minecraft:armor_stand 30.5 103.5 0.5 {Tags:["noob_agent_easy_scenario"],Invisible:1b,Marker:1b,CustomNameVisible:1b,CustomName:'{"text":"Iron-Bar Gateway","color":"gray"}'}

# Players start facing the gateway with the button in reach and Night Vision
# for the whole run (infinite duration, reapplied on every reset).
clear @a
tp @a 27.5 100 0.5 -90 0
spawnpoint @a 27 100 0
gamemode adventure @a
effect clear @a
effect give @a minecraft:night_vision infinite 0 true
effect give @a minecraft:saturation infinite 0 true
effect give @a minecraft:resistance infinite 4 true
title @a reset
