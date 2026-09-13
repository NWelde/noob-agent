"use strict";

const { once } = require("node:events");
const readline = require("node:readline");
const mineflayer = require("mineflayer");

const MAX_MESSAGES = 20;
const MAX_OBJECTS = 64;
const PUBLIC_BLOCKS = new Set([
  "barrel",
  "hopper",
  "iron_bars",
  "lodestone",
  "stone_button",
]);

let bot = null;
let publicMessages = [];

function write(reply) {
  process.stdout.write(`${JSON.stringify(reply)}\n`);
}

function diagnose(message) {
  process.stderr.write(`[minecraft-sidecar] ${message}\n`);
}

function text(value) {
  if (typeof value === "string") return value;
  if (value && typeof value.toString === "function") return value.toString();
  return "";
}

function rememberMessage(value) {
  const rendered = text(value).trim();
  if (!rendered) return;
  publicMessages.push({ kind: "game", text: rendered });
  publicMessages = publicMessages.slice(-MAX_MESSAGES);
}

function distance(position) {
  return bot.entity.position.distanceTo(position);
}

function namedEntities(radius) {
  const objects = [];
  for (const entity of Object.values(bot.entities)) {
    if (entity === bot.entity || !entity.position || distance(entity.position) > radius) continue;

    if (entity.name === "armor_stand") {
      const label = text(entity.customName).trim();
      if (label) {
        objects.push({
          label,
          position: entity.position,
          distance: distance(entity.position),
          properties: { kind: "label" },
        });
      }
      continue;
    }

    if (entity.name === "item" && typeof entity.getDroppedItem === "function") {
      const item = entity.getDroppedItem();
      if (!item) continue;
      const label = text(item.customName).trim() || item.displayName || item.name;
      objects.push({
        label,
        position: entity.position,
        distance: distance(entity.position),
        properties: { kind: "item", count: item.count },
      });
    }
  }
  return objects;
}

function nearbyPublicBlocks(radius) {
  const objects = [];
  const center = bot.entity.position.floored();
  const verticalRadius = Math.min(radius, 4);
  for (let x = -radius; x <= radius; x += 1) {
    for (let y = -verticalRadius; y <= verticalRadius; y += 1) {
      for (let z = -radius; z <= radius; z += 1) {
        if (x * x + y * y + z * z > radius * radius) continue;
        const block = bot.blockAt(center.offset(x, y, z), false);
        if (!block || !PUBLIC_BLOCKS.has(block.name)) continue;
        objects.push({
          label: block.displayName,
          position: block.position,
          distance: distance(block.position),
          properties: {
            kind: "block",
            facing: block.getProperties().facing ?? null,
            open: block.getProperties().open ?? null,
            powered: block.getProperties().powered ?? null,
            enabled: block.getProperties().enabled ?? null,
          },
        });
      }
    }
  }
  return objects;
}

function position(value) {
  return { x: value.x, y: value.y, z: value.z };
}

function snapshot(radius) {
  const heldItem = bot.heldItem;
  const objects = [...namedEntities(radius), ...nearbyPublicBlocks(radius)]
    .sort((left, right) =>
      left.label.localeCompare(right.label) ||
      left.position.x - right.position.x ||
      left.position.y - right.position.y ||
      left.position.z - right.position.z
    )
    .slice(0, MAX_OBJECTS)
    .map((object) => ({
      ...object,
      position: position(object.position),
    }));

  return {
    logical_time: bot.time.age,
    radius,
    terminal: false,
    terminal_reason: null,
    player: {
      position: position(bot.entity.position),
      orientation: { yaw: bot.entity.yaw, pitch: bot.entity.pitch },
      properties: {
        game_mode: bot.game.gameMode,
        held_item: heldItem ? heldItem.displayName : null,
        selected_slot: bot.quickBarSlot,
        inventory_labels: bot.inventory.items().map((item) => item.displayName).sort().join(", "),
      },
    },
    status: {
      health: bot.health,
      food: bot.food,
      air: bot.oxygenLevel,
      on_ground: bot.entity.onGround,
      game_mode: bot.game.gameMode,
      experience_level: bot.experience.level,
    },
    visible_objects: objects,
    messages: publicMessages,
  };
}

async function connect(message) {
  if (bot) throw new Error("the sidecar is already connected");
  bot = mineflayer.createBot({
    host: message.host,
    port: message.port,
    username: message.username,
    version: message.version,
    auth: "offline",
    hideErrors: true,
  });
  bot.on("messagestr", rememberMessage);
  bot.on("actionBar", rememberMessage);
  bot.on("error", (error) => diagnose(error.message));

  await Promise.race([
    once(bot, "spawn"),
    once(bot, "kicked").then(([reason]) => {
      throw new Error(`kicked: ${text(reason)}`);
    }),
    once(bot, "error").then(([error]) => {
      throw error;
    }),
  ]);
}

async function reset(message) {
  for (const command of message.commands ?? []) {
    if (typeof command !== "string" || !command.trim()) continue;
    bot.chat(command.startsWith("/") ? command : `/${command}`);
  }
  await bot.waitForTicks(message.settle_ticks ?? 5);
  // Command feedback is administrative transport detail, not public game state.
  publicMessages = [];
  return snapshot(message.radius ?? 5);
}

async function handle(message) {
  const id = message && message.id;
  try {
    if (!message || typeof message !== "object") throw new Error("request must be an object");
    if (message.op === "connect") {
      await connect(message);
      write({ id, ok: true });
    } else if (message.op === "reset") {
      if (!bot) throw new Error("connect must succeed before reset");
      write({ id, ok: true, observation: await reset(message) });
    } else if (message.op === "observe") {
      if (!bot) throw new Error("connect must succeed before observe");
      write({ id, ok: true, observation: snapshot(message.radius ?? 5) });
    } else if (message.op === "close") {
      write({ id, ok: true });
      if (bot) bot.quit("Connector closed");
      bot = null;
      process.exitCode = 0;
      input.close();
    } else {
      throw new Error(`unsupported operation: ${String(message.op)}`);
    }
  } catch (error) {
    diagnose(error instanceof Error ? error.message : String(error));
    write({ id, ok: false, code: "GAME_REJECTED", error: "The game request failed." });
  }
}

const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
let queue = Promise.resolve();
input.on("line", (line) => {
  queue = queue.then(async () => {
    let message;
    try {
      message = JSON.parse(line);
    } catch {
      write({ id: null, ok: false, code: "GAME_REJECTED", error: "Invalid JSON request." });
      return;
    }
    await handle(message);
  });
});
