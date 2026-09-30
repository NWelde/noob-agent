const test=require('node:test');const assert=require('node:assert/strict');
const {animate}=require('../src/noob_agent/connectors/minecraft_sidecar/server_visual.js');
const {Vec3}=require('../src/noob_agent/connectors/minecraft_sidecar/node_modules/vec3');
test('visual preparation equips actual item, faces target and swings without placing',async()=>{
  const events=[];const b={entity:{position:new Vec3(1,64,3)},
    inventory:{items:()=>[{name:'redstone',count:64}]},
    equip:async item=>events.push(['equip',item.name]),
    lookAt:async p=>events.push(['look',p.x,p.y,p.z]),
    waitForTicks:async n=>events.push(['ticks',n]),
    swingArm:arm=>events.push(['swing',arm])};
  const result=await animate(b,{position:[1,64,1],name:'minecraft:redstone_wire'});
  assert.equal(result.visual_only,true);
  assert.deepEqual(events.map(e=>e[0]),['equip','look','ticks','swing']);
});
