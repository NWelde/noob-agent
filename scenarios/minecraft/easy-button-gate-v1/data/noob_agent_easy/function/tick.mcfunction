# Non-benchmark diagnostic: pressing the gate button opens the iron bars once.
# The public message is shown before the bars are removed, so it fires exactly once.
execute if block 30 101 -2 minecraft:stone_button[powered=true] if block 31 101 0 minecraft:iron_bars run title @a[x=24,y=99,z=-4,dx=10,dy=5,dz=8] actionbar {"text":"The iron-bar gateway opens.","color":"green"}
execute if block 30 101 -2 minecraft:stone_button[powered=true] if block 31 101 0 minecraft:iron_bars run fill 31 100 -1 31 102 1 minecraft:air
