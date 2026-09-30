'use strict';
const {Vec3}=require('vec3');
async function animate(bot,request){
  let held=null;
  if(request.name){
    const itemName=request.name.replace('minecraft:','').replace('redstone_wall_torch','redstone_torch')
      .replace('redstone_wire','redstone').replace('oak_wall_sign','oak_sign');
    const item=bot.inventory.items().find(i=>i.name===itemName&&i.count>0);
    if(!item)throw Error('visual_item_unavailable');
    await bot.equip(item,'hand');held=itemName;
  }
  const target=new Vec3(...request.position).offset(.5,.5,.5);
  await bot.lookAt(target,true);await bot.waitForTicks(2);bot.swingArm('right');
  return {visual_only:true,held,position:[bot.entity.position.x,bot.entity.position.y,bot.entity.position.z]};
}
module.exports={animate};
