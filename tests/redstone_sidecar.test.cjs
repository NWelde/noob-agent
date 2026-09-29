const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { dispatch, validateBlock, createDedicatedBot } = require('../src/noob_agent/connectors/minecraft_sidecar/redstone.js');
const registry = require('../src/noob_agent/connectors/minecraft_sidecar/node_modules/minecraft-data')('1.21.1');
test('floor support is readable without allowing below-bound interactions', async () => {
  const b = bot();
  b.blockAt = p => ({name: 'grass_block', getProperties: () => ({snowy:false})});
  assert.equal((await dispatch(b, {op:'block', position:[2,63,2]})).name, 'minecraft:grass_block');
  await assert.rejects(dispatch(b, {op:'interact', position:[2,63,2]}), /invalid_position/);
  await assert.rejects(dispatch(b, {op:'block', position:[2,62,2]}), /invalid_position/);
});
function bot() {
  const result = new EventEmitter();
  Object.assign(result, { registry, username: 'noobagentbot',
    entity: { position: { x: 2, y: 64, z: 2 }, yaw: Math.PI, pitch: 0 },
    game: { gameMode: 'creative', dimension: 'overworld' },
    inventory: { slots: [null, { name: 'stone', count: 64, slot: 36 }] },
    blockAt: () => ({ name: 'lever', position: { x: 2, y: 64, z: 2 }, getProperties: () => ({ powered: false }) }),
    waitForTicks: async () => {}, activateBlock: async () => { result.used = true; },
  });
  return result;
}
test('dedicated endpoint cannot be supplied by caller', () => {
  let options;
  createDedicatedBot({ createBot: o => { options = o; return bot(); } });
  assert.equal(options.host, '127.0.0.1'); assert.equal(options.port, 25567);
  assert.equal(options.username, 'noobagentbot'); assert.equal(options.version, '1.21.1');
});
test('observations report actual occupied slots and orientation', async () => {
  const actual = await dispatch(bot(), { op: 'player' });
  assert.deepEqual(actual.inventory, [{ name: 'stone', count: 64, slot: 1 }]);
  assert.equal(actual.yaw, Math.PI); assert.equal(actual.gameMode, 'creative');
});
test('readback rejects unloaded blocks rather than inventing air', async () => {
  const b = bot(); b.blockAt = () => null;
  await assert.rejects(dispatch(b, { op: 'block', position: [2,64,2] }), /unloaded/);
});
test('normal interaction uses activateBlock and reads actual post-state', async () => {
  const b = bot();
  const result = await dispatch(b, { op: 'interact', position: [2,64,2] });
  assert.equal(b.used, true); assert.equal(result.after.properties.powered, false);
});
test('rejects distant, out-of-bounds, multiple-target and nonlever interactions', async () => {
  for (const request of [
    { op: 'interact', position: [20,64,2] },
    { op: 'interact', position: [-1,64,2] },
    { op: 'interact', position: [2,64,2], positions: [[3,64,2]] },
    { op: 'command', command: 'stop' },
  ]) await assert.rejects(dispatch(bot(), request));
  const b = bot(); b.blockAt = () => ({name: 'stone', getProperties: () => ({})});
  await assert.rejects(dispatch(b, {op:'interact', position:[2,64,2]}));
});
test('registry validation rejects unknown properties, values and nonwhitelisted blocks', () => {
  assert.deepEqual(validateBlock(registry, 'minecraft:lever', { face: 'floor', powered: false }),
    { name: 'minecraft:lever', properties: {face:'floor', powered:false} });
  for (const [name, properties] of [
    ['minecraft:command_block', {}], ['minecraft:lever', { face: 'ceiling;stop' }],
    ['minecraft:lever', { bogus: 'north' }], ['minecraft:redstone_wire', { power: 16 }],
    ['minecraft:lever', { powered: 'false' }],
  ]) assert.throws(() => validateBlock(registry, name, properties));
});
test('baseline scan hashes all cells and fails on any unloaded cell', async () => {
  const b = bot(); let count = 0;
  b.blockAt = p => { count++; return {name: p.y === 60 ? 'bedrock' : p.y < 63 ? 'dirt' : p.y === 63 ? 'grass_block' : 'air',
    getProperties: () => p.y === 63 ? {snowy:false} : {}}; };
  const result = await dispatch(b, {op:'baseline'});
  assert.equal(count, 96 * 112 * 36);
  assert.equal(result.blocks, count); assert.equal(result.layers.length, 36);
  b.blockAt = () => null;
  await assert.rejects(dispatch(b, {op:'baseline'}), /unloaded/);
});
test('integer registry states are normalized to numbers in actual readbacks', async () => {
  const b = bot();
  b.blockAt = () => ({name:'redstone_wire', getProperties:()=>({power:'15', east:'side'})});
  const result = await dispatch(b, {op:'block', position:[2,64,2]});
  assert.equal(result.properties.power, 15);
});
test('runtime protocol cannot request privileged grader controls or scheduling', async () => {
  for (const op of ['grader', 'pulse', 'set_control', 'wait_ticks', 'command']) {
    await assert.rejects(dispatch(bot(), {op, position:[2,64,2]}), /invalid_request/);
  }
});
test('normal interaction retains its ten client tick wait, not STEP timing', async () => {
  const b = bot(); const waits = [];
  b.waitForTicks = async ticks => { waits.push(ticks); };
  await dispatch(b, {op:'interact', position:[2,64,2]});
  assert.deepEqual(waits, [10]);
});
