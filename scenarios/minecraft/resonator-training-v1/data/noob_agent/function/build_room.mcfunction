# Fixed compact training room: x=-7..7, y=99..105, z=-7..7.
fill -7 99 -7 7 105 7 minecraft:smooth_stone hollow
fill -6 100 -6 6 104 6 minecraft:air
fill -6 99 -6 6 99 6 minecraft:polished_andesite

# Resonator, input basin, activation control, and labeled supply/output containers.
setblock 0 100 0 minecraft:hopper[facing=down,enabled=false]
setblock 0 99 0 minecraft:lodestone
setblock 0 100 2 minecraft:stone_button[face=floor,facing=north,powered=false]
setblock -4 100 0 minecraft:barrel[facing=east,open=false]
data merge block -4 100 0 {CustomName:'{"text":"Dull Shards","italic":false}'}
setblock 2 100 0 minecraft:barrel[facing=west,open=false]
data merge block 2 100 0 {CustomName:'{"text":"Resonator Output","italic":false}'}
item replace block -4 100 0 container.0 with minecraft:amethyst_shard[minecraft:custom_name='{"text":"Dull Shard","italic":false}'] 1
item replace block -4 100 0 container.1 with minecraft:amethyst_shard[minecraft:custom_name='{"text":"Dull Shard","italic":false}'] 1
item replace block -4 100 0 container.2 with minecraft:amethyst_shard[minecraft:custom_name='{"text":"Dull Shard","italic":false}'] 1
item replace block -4 100 0 container.3 with minecraft:amethyst_shard[minecraft:custom_name='{"text":"Dull Shard","italic":false}'] 1
item replace block -4 100 0 container.4 with minecraft:amethyst_shard[minecraft:custom_name='{"text":"Dull Shard","italic":false}'] 1
item replace block -4 100 0 container.5 with minecraft:amethyst_shard[minecraft:custom_name='{"text":"Dull Shard","italic":false}'] 1

# Two harmless distractors live in a separate container.
setblock -4 100 3 minecraft:barrel[facing=east,open=false]
data merge block -4 100 3 {CustomName:'{"text":"Loose Objects","italic":false}'}
item replace block -4 100 3 container.0 with minecraft:flint[minecraft:custom_name='{"text":"Slate Chip","italic":false}'] 1
item replace block -4 100 3 container.1 with minecraft:prismarine_shard[minecraft:custom_name='{"text":"Sea Splinter","italic":false}'] 1

# The sealed gateway and its ordinary activation control.
fill 5 100 -2 5 104 2 minecraft:deepslate_bricks
fill 5 101 -1 5 103 1 minecraft:iron_bars
setblock 4 101 0 minecraft:stone_button[face=wall,facing=west,powered=false]

# Public labels name objects without explaining their purpose. Marker armor
# stands are invisible and non-colliding; only their visible names are rendered.
summon minecraft:armor_stand 0.5 102.5 0.5 {Tags:["noob_agent_scenario"],Invisible:1b,Marker:1b,CustomNameVisible:1b,CustomName:'{"text":"Resonator","color":"light_purple"}'}
summon minecraft:armor_stand 0.5 101.5 2.5 {Tags:["noob_agent_scenario"],Invisible:1b,Marker:1b,CustomNameVisible:1b,CustomName:'{"text":"Activation Control","color":"white"}'}
summon minecraft:armor_stand 5.5 104.5 0.5 {Tags:["noob_agent_scenario"],Invisible:1b,Marker:1b,CustomNameVisible:1b,CustomName:'{"text":"Sealed Gateway","color":"gray"}'}
