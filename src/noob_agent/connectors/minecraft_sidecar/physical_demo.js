'use strict';
// Opt-in demo transport: ordinary player movement/actions, never server placement.
const {Vec3}=require('vec3');
const directions={north:new Vec3(0,0,-1),south:new Vec3(0,0,1),east:new Vec3(1,0,0),west:new Vec3(-1,0,0)};
function snapshot(block){
  if(!block) throw Error('unloaded_block');
  return {position:[block.position.x,block.position.y,block.position.z],name:`minecraft:${block.name}`,properties:block.getProperties()};
}
function reach(bot,target){return bot.entity.position.offset(0,1.62,0).distanceTo(target.offset(.5,.5,.5));}
async function approach(bot,target){
  const start=bot.entity.position.clone(); let previous=Infinity,stalled=0;
  try{
    for(let tick=0;tick<200;tick++){
      if(reach(bot,target)<=4.2) return {from:[start.x,start.y,start.z],to:[bot.entity.position.x,bot.entity.position.y,bot.entity.position.z],ticks:tick};
      const p=bot.entity.position;
      if(Math.abs(target.y-p.y)>4) throw Error('walking_vertical_unreachable');
      await bot.lookAt(new Vec3(target.x+.5,p.y+1.62,target.z+.5),true);
      const toward=new Vec3(target.x+.5-p.x,0,target.z+.5-p.z).normalize();
      const obstacle=bot.blockAt(p.plus(toward.scaled(.8)).floored());
      const solidAhead=obstacle&&obstacle.boundingBox==='block';
      bot.setControlState('forward',true); bot.setControlState('jump',solidAhead||target.y>p.y+.5);
      await bot.waitForTicks(1);
      const distance=p.distanceTo(target);
      stalled=distance>=previous-.01?stalled+1:0;previous=distance;
      if(stalled>=40) throw Error('walking_stalled');
    }
    throw Error('walking_deadline');
  }finally{bot.setControlState('forward',false);bot.setControlState('jump',false);}
}
function reference(bot,target,name,properties){
  let faces=[new Vec3(0,1,0),new Vec3(1,0,0),new Vec3(-1,0,0),new Vec3(0,0,1),new Vec3(0,0,-1),new Vec3(0,-1,0)];
  if(name==='minecraft:redstone_wall_torch'||name==='minecraft:oak_wall_sign'||(name==='minecraft:lever'&&properties.face==='wall')){
    if(!directions[properties.facing]) throw Error('attachment_facing_required');
    faces=[directions[properties.facing]];
  }else if(name==='minecraft:redstone_wire'||name==='minecraft:repeater'||name==='minecraft:comparator'||name==='minecraft:redstone_torch'||name==='minecraft:lever'){
    faces=[new Vec3(0,properties.face==='ceiling'?-1:1,0)];
  }
  for(const face of faces){const ref=bot.blockAt(target.minus(face));if(ref&&ref.name!=='air'&&ref.name!=='water'&&ref.name!=='lava')return {ref,face};}
  throw Error('missing_support');
}
async function physicalAction(bot,request){
  if(bot.game.gameMode!=='creative') throw Error('creative_required');
  const target=new Vec3(...request.position);let before=snapshot(bot.blockAt(target));
  const movement=await approach(bot,target);
  if(request.action==='place'){
    // A target within reach may still intersect the player's collision box.
    // Back away through ordinary controls before sending any placement click.
    const overlaps=()=>{
      const p=bot.entity.position;
      return p.y<target.y+1&&p.y+1.8>target.y&&
        Math.abs(p.x-(target.x+.5))<.85&&Math.abs(p.z-(target.z+.5))<.85;
    };
    try{
      for(let tick=0;overlaps();tick++){
        if(tick>=40)throw Error('walking_stalled');
        await bot.lookAt(target.offset(.5,1.62,.5),true);
        bot.setControlState('back',true);
        await bot.waitForTicks(1);
        movement.ticks++;
      }
    }finally{bot.setControlState('back',false);}
    movement.to=[bot.entity.position.x,bot.entity.position.y,bot.entity.position.z];
  }
  before=snapshot(bot.blockAt(target)); // Fresh target after walking, before any click.
  if(request.action==='walk'){
    return {movement,method:'walking_controls'};
  }else if(request.action==='place'){
    if(before.name!=='minecraft:air') throw Error('occupied_target');
    const name=request.name; const properties=request.properties||{};
    const itemName=name.replace('minecraft:','').replace('redstone_wall_torch','redstone_torch').replace('redstone_wire','redstone').replace('oak_wall_sign','oak_sign');
    const item=bot.inventory.items().find(item=>item.name===itemName&&item.count>0);
    if(!item) throw Error('missing_inventory');
    const {ref,face}=reference(bot,target,name,properties);
    await bot.equip(item,'hand');
    // Preserve yaw for directional components while the click packet targets the actual support.
    const facing=properties.facing;
    const yaw={north:Math.PI,south:0,east:-Math.PI/2,west:Math.PI/2};
    if(facing){
      // This Mineflayer yaw mapping was verified against actual server facing.
      const leverOffset=name==='minecraft:lever'&&properties.face!=='wall'?Math.PI:0;
      await bot.look(yaw[facing]+leverOffset,0,true);
      // Movement/look packets publish on the client physics tick; do not click
      // before the server has received the orientation used for placement.
      await bot.waitForTicks(2);
    }else await bot.lookAt(ref.position.offset(.5,.5,.5),true);
    bot.setControlState('sneak',true);
    try{await bot._placeBlockWithOptions(ref,face,{forceLook:'ignore',swingArm:'right'});}
    finally{bot.setControlState('sneak',false);}
    // Normal clicks set repeater delay and comparator mode, never synthetic state writes.
    const placed=bot.blockAt(target);
    if(name==='minecraft:repeater'&&properties.delay){for(let n=1;n<properties.delay;n++)await bot.activateBlock(placed);}
    if(name==='minecraft:comparator'&&properties.mode==='subtract')await bot.activateBlock(placed);
  }else if(request.action==='break'){
    if(before.name==='minecraft:air') throw Error('empty_target');
    await bot.dig(bot.blockAt(target),true);
  }else if(request.action==='interact'){
    if(before.name!=='minecraft:lever')throw Error('invalid_interaction');
    await bot.activateBlock(bot.blockAt(target));
  }else throw Error('invalid_action');
  await bot.waitForTicks(10);
  return {before,after:snapshot(bot.blockAt(target)),movement,method:'mineflayer_player_action'};
}
module.exports={physicalAction,approach};
