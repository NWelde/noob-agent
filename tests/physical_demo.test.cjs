const test = require('node:test');
const assert = require('node:assert/strict');
const { physicalAction } = require('../src/noob_agent/connectors/minecraft_sidecar/physical_demo.js');
const { Vec3 } = require('../src/noob_agent/connectors/minecraft_sidecar/node_modules/vec3');
function fixture() {
  const cells = new Map();
  const b = {entity:{position:new Vec3(2.5,64,2.5)}, game:{gameMode:'creative'},
    inventory:{items:()=>[{name:'stone',count:64}]},
    blockAt:p=>({name:cells.get(p.toString()) || (p.y===63?'grass_block':'air'),position:p,
      getProperties:()=>({})}),
    equip:async()=>{}, lookAt:async()=>{}, look:async()=>{}, waitForTicks:async()=>{},
    setControlState:()=>{},
    _placeBlockWithOptions:async(ref,face)=>cells.set(ref.position.plus(face).toString(),'stone'),
    dig:async(block)=>cells.delete(block.position.toString())};
  return b;
}
test('physical placement invokes ordinary placement and returns actual block', async()=>{
  const b=fixture(); const result=await physicalAction(b,{op:'physical',action:'place',position:[3,64,2],name:'minecraft:stone',properties:{}});
  assert.equal(result.after.name,'minecraft:stone'); assert.equal(result.method,'mineflayer_player_action');
});
test('missing inventory or support rejects without placing',async()=>{
  const b=fixture(); b.inventory.items=()=>[];
  await assert.rejects(physicalAction(b,{op:'physical',action:'place',position:[3,64,2],name:'minecraft:stone',properties:{}}),/inventory/);
  const c=fixture();
  await assert.rejects(physicalAction(c,{op:'physical',action:'place',position:[3,66,2],name:'minecraft:stone',properties:{}}),/support/);
});
test('walking failure clears controls and cannot proceed to placement',async()=>{
  const b=fixture(); const controls=[]; b.setControlState=(key,value)=>controls.push([key,value]);
  await assert.rejects(physicalAction(b,{op:'physical',action:'place',position:[30,64,2],name:'minecraft:stone',properties:{}}),/walking/);
  assert.deepEqual(controls.slice(-2),[['forward',false],['jump',false]]);
});
test('placement retreats from an occupied player target before clicking',async()=>{
  const b=fixture();const controls={};let clicked=false;
  b.entity.position=new Vec3(3.5,64,2.5);
  b.setControlState=(key,value)=>controls[key]=value;
  b.waitForTicks=async()=>{if(controls.back)b.entity.position.z+=.2;};
  b._placeBlockWithOptions=async()=>{
    assert.ok(Math.abs(b.entity.position.z-2.5)>=.85);
    clicked=true;
  };
  await physicalAction(b,{op:'physical',action:'place',position:[3,64,2],name:'minecraft:stone',properties:{}});
  assert.equal(clicked,true); assert.equal(controls.back,false);
});
test('live-verified repeater and floor lever yaw rules wait before clicking',async()=>{
  for(const [name,properties,expectedYaw] of [
    ['repeater',{facing:'north'},Math.PI],
    ['lever',{face:'floor',facing:'north'},2*Math.PI],
  ]){
    const b=fixture();const events=[];
    b.inventory.items=()=>[{name,count:64}];
    b.look=async(yaw)=>events.push(['look',yaw]);
    b.waitForTicks=async(ticks)=>events.push(['wait',ticks]);
    b._placeBlockWithOptions=async()=>events.push(['place']);
    await physicalAction(b,{op:'physical',action:'place',position:[3,64,2],name:`minecraft:${name}`,properties});
    assert.deepEqual(events.slice(0,3),[['look',expectedYaw],['wait',2],['place']]);
  }
});
