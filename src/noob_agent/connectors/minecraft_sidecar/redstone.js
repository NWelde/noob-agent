'use strict';

// Dedicated milestone-2 transport. Never imports or alters the legacy sidecar.
const { createHash } = require('node:crypto');
const fs = require('node:fs');
const net = require('node:net');
const readline = require('node:readline');
const { once } = require('node:events');
const path = require('node:path');
const { Vec3 } = require('vec3');
const contract = require('../../../../scenarios/minecraft/redstone-computer-v1/contract.json');

function createDedicatedBot(mineflayer = require('mineflayer')) {
  return mineflayer.createBot({ host: '127.0.0.1', port: 25567,
    username: 'noobagentbot', auth: 'offline', version: '1.21.1', hideErrors: true });
}
function object(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
function validateBlock(registry, name, properties = {}) {
  if (!contract.permitted_blocks.includes(name) || !object(properties)) throw Error('invalid_block');
  const definition = registry.blocksByName[name.slice('minecraft:'.length)];
  if (!definition) throw Error('unknown_block');
  for (const [key, value] of Object.entries(properties)) {
    const state = definition.states.find(s => s.name === key);
    if (!state) throw Error('unknown_property');
    const valid = state.type === 'bool' ? typeof value === 'boolean'
      : state.type === 'int' ? Number.isInteger(value) && state.values.includes(String(value))
      : typeof value === 'string' && state.values.includes(value);
    if (!valid) throw Error('invalid_property');
  }
  return { name, properties };
}
function position(value, readSupport = false) {
  if (!Array.isArray(value) || value.length !== 3 || value.some((v, i) =>
    !Number.isInteger(v) || v < contract.build_min[i] - (readSupport && i === 1 ? 1 : 0) || v > contract.build_max[i])) {
    throw Error('invalid_position');
  }
  return new Vec3(...value);
}
function readBlock(bot, target) {
  const block = bot.blockAt(target);
  if (!block) throw Error('unloaded_block');
  const properties = { ...block.getProperties() };
  for (const state of bot.registry.blocksByName[block.name].states) {
    if (state.type === 'int' && Object.hasOwn(properties, state.name)) {
      const value = Number(properties[state.name]);
      if (!Number.isInteger(value) || !state.values.includes(String(value))) throw Error('invalid_readback');
      properties[state.name] = value;
    }
  }
  return { position: [target.x, target.y, target.z], name: `minecraft:${block.name}`,
    properties };

}
async function dispatch(bot, request) {
  if (!object(request)) throw Error('invalid_request');
  const keys = {
    baseline: ['op'], settle: ['op'], player: ['op'], block: ['op', 'position'], interact: ['op', 'position'],
    validate: ['op', 'name', 'properties'],
  }[request.op];
  if (!keys || Object.keys(request).some(k => !keys.includes(k))) throw Error('invalid_request');
  if (request.op === 'settle') {
    await bot.waitForTicks(10);
    return { settled: true };
  }
  if (request.op === 'baseline') {
    const layers = []; let blocks = 0;
    for (let y = 60; y <= 95; y++) {
      const hash = createHash('sha256');
      for (let x = 0; x <= 95; x++) for (let z = 0; z <= 111; z++) {
        const state = readBlock(bot, new Vec3(x, y, z));
        const properties = Object.fromEntries(Object.entries(state.properties).sort(([a], [b]) => a.localeCompare(b)));
        hash.update(JSON.stringify({name: state.name, properties}) + '\n'); blocks++;
      }
      layers.push({y, sha256: hash.digest('hex')});
    }
    return {layers, blocks};
  }
  if (request.op === 'validate') return validateBlock(bot.registry, request.name, request.properties);
  if (request.op === 'player') {
    const { x, y, z } = bot.entity.position;
    return { username: bot.username, position: [x, y, z], yaw: bot.entity.yaw,
      pitch: bot.entity.pitch, orientationUnits: 'radians', gameMode: bot.game.gameMode,
      dimension: bot.game.dimension, inventory: bot.inventory.slots.flatMap((item, slot) =>
        item ? [{ slot, name: item.name, count: item.count }] : []) };
  }
  const target = position(request.position, request.op === 'block');
  const before = readBlock(bot, target);
  if (request.op === 'block') return before;
  // Keep this initial action surface restricted to the infrastructure lever check.
  // Calling harness must journal/charge the primitive before delivery.
  const p = bot.entity.position;
  if (Math.hypot(p.x - target.x - 0.5, p.y + 1.62 - target.y - 0.5,
    p.z - target.z - 0.5) > 4.5) throw Error('out_of_reach');
  if (bot.game.gameMode !== 'creative' || before.name !== 'minecraft:lever') {
    throw Error('invalid_interaction');
  }
  await bot.activateBlock(bot.blockAt(target));
  await bot.waitForTicks(10);
  return { before, after: readBlock(bot, target) };
}
async function main() {
  const bot = createDedicatedBot();
  // Never emit arbitrary server/kick/error text: it can contain sensitive data.
  bot.on('error', () => {});
  bot.on('kicked', () => process.exit(2));
  bot.on('end', () => process.exit(2));
  const startup = setTimeout(() => process.exit(2), 20000);
  await once(bot, 'spawn');
  await bot.waitForChunksToLoad();
  clearTimeout(startup);
  process.stdout.write(JSON.stringify({ ready: true, version: bot.version }) + '\n');
  const lines = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  for await (const line of lines) {
    // Python's exchange deadline is the second boundary; a hung operation exits here.
    const deadline = setTimeout(() => process.exit(2), 10000);
    try {
      if (Buffer.byteLength(line) > 8192) throw Error('oversized_request');
      const request = JSON.parse(line);
      const result = await dispatch(bot, request);
      process.stdout.write(JSON.stringify({ ok: true, result }) + '\n');
    } catch {
      process.stdout.write(JSON.stringify({ ok: false, error: 'request_failed' }) + '\n');
    } finally { clearTimeout(deadline); }
  }
  bot.quit();
}

async function persistentMain(socketPath) {
  const resolvedSocket = path.resolve(socketPath);
  if (!resolvedSocket.endsWith(path.join('.noob-agent', 'redstone-sidecar.sock')))
    throw Error('invalid_socket_path');
  const bot = createDedicatedBot();
  let server;
  let activeClient = null;
  let stopping = false;
  // A disconnected client does not cancel an in-flight Mineflayer dispatch.
  // Serialize work across reconnects so recovery settle/readback cannot race it.
  let dispatchQueue = Promise.resolve();
  const pidPath = path.join(path.dirname(resolvedSocket), 'redstone-sidecar.pid');
  const shutdown = () => {
    if (stopping) return;
    stopping = true;
    if (server) server.close();
    if (activeClient) activeClient.end();
    bot.quit();
    setTimeout(() => {
      try { fs.unlinkSync(resolvedSocket); } catch {}
      try { fs.unlinkSync(pidPath); } catch {}
      process.exit(0);
    }, 1000).unref();
  };
  bot.on('error', () => {});
  bot.on('kicked', () => { if (!stopping) process.exit(2); });
  bot.on('end', () => { if (!stopping) process.exit(2); });
  const startup = setTimeout(() => process.exit(2), 20000);
  await once(bot, 'spawn');
  await bot.waitForChunksToLoad();
  clearTimeout(startup);

  fs.mkdirSync(path.dirname(resolvedSocket), { recursive: true });
  try { fs.unlinkSync(resolvedSocket); } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  server = net.createServer(socket => {
    if (activeClient) { socket.destroy(); return; }
    activeClient = socket;
    socket.write(JSON.stringify({ ready: true, version: bot.version }) + '\n');
    const lines = readline.createInterface({ input: socket, crlfDelay: Infinity });
    (async () => {
      for await (const line of lines) {
        if (Buffer.byteLength(line) > 8192) { socket.destroy(); return; }
        let request;
        try { request = JSON.parse(line); } catch { socket.destroy(); return; }
        if (request && request.control === 'shutdown' && Object.keys(request).length === 1) {
          socket.write(JSON.stringify({ ok: true, stopping: true }) + '\n');
          shutdown();
          return;
        }
        try {
          const pending = dispatchQueue.then(() => dispatch(bot, request));
          dispatchQueue = pending.catch(() => {});
          const result = await pending;
          socket.write(JSON.stringify({ ok: true, result }) + '\n');
        } catch {
          socket.write(JSON.stringify({ ok: false, error: 'request_failed' }) + '\n');
        }
      }
    })().catch(() => socket.destroy());
    socket.on('close', () => { if (activeClient === socket) activeClient = null; });
    socket.on('error', () => {});
  });
  server.listen(resolvedSocket);
  await once(server, 'listening');
  fs.chmodSync(resolvedSocket, 0o600);
  process.on('SIGTERM', shutdown);
  process.on('SIGINT', shutdown);
  process.stdout.write(JSON.stringify({ ready: true, persistent: true }) + '\n');
}
module.exports = { createDedicatedBot, dispatch, validateBlock };
if (require.main === module) {
  if (process.argv[2] === '--persistent') {
    persistentMain(process.argv[3]).catch(() => process.exit(2));
  } else {
    main().catch(() => process.exit(2));
  }
}
