execute if block 54 100 0 minecraft:redstone_lamp[lit=true] if entity @e[tag=lamp_marker,tag=!solved] run tellraw @a[x=48,y=99,z=-4,dx=10,dy=6,dz=8] {"text":"The redstone lamp lights up.","color":"green"}
execute if block 54 100 0 minecraft:redstone_lamp[lit=true] run tag @e[tag=lamp_marker] add solved
