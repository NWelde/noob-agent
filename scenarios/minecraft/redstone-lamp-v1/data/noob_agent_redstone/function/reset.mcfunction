# Isolated demo room. Only the bot's inventory is cleared.
forceload add 48 -4 58 4
kill @e[tag=noob_redstone]
kill @e[type=minecraft:item,x=48,y=99,z=-4,dx=10,dy=6,dz=8]
fill 48 99 -4 58 105 4 minecraft:quartz_block hollow
fill 49 100 -3 57 104 3 minecraft:air
fill 49 104 -3 57 104 3 minecraft:sea_lantern
setblock 53 99 0 minecraft:yellow_concrete
setblock 54 100 0 minecraft:redstone_lamp[lit=false]
setblock 50 100 -2 minecraft:barrel[facing=up]{Items:[{Slot:0b,id:"minecraft:redstone_block",count:1}]}
summon minecraft:armor_stand 54.5 100 0.5 {Tags:["noob_redstone","lamp_marker"],Invisible:1b,Marker:1b,CustomNameVisible:1b,CustomName:'{"text":"Redstone Lamp","color":"gold"}'}
summon minecraft:armor_stand 53.5 100 0.5 {Tags:["noob_redstone"],Invisible:1b,Marker:1b,CustomNameVisible:1b,CustomName:'{"text":"Empty Socket","color":"yellow"}'}
tag @e[tag=noob_redstone] remove solved
clear @a[name=noobagentbot]
gamemode survival @a[name=noobagentbot]
tp @a[name=noobagentbot] 50.5 100 0.5 -90 0
gamemode spectator @a[name=!noobagentbot]
tp @a[name=!noobagentbot] 51.5 102 -3 -65 25
effect give @a minecraft:night_vision infinite 0 true
effect give @a minecraft:saturation infinite 0 true
effect give @a minecraft:resistance infinite 4 true
